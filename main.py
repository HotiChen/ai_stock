from __future__ import annotations

"""
Main scheduler — daily jobs:
  08:30  PremarketJob      : AI 波段選股 + 風控 + Telegram 確認
         DaytradingReport  : 當沖預測 → Telegram（開盤前 30 分鐘推播）
  09:00  MarketOpenJob     : 波段下單
  09:05  DT 開盤確認       : 量能/方向確認，過濾候選（不符合 → skipped）
  09:10  DT 進場           : 下當沖買單 + 啟動 tick 監控
  10:00  DT 盤中檢查       : 持倉損益報告 + 剩餘候選提示（可二次進場）
  13:00  DT 出場警報       : 30 分鐘倒數提醒 + 損益預覽
  13:15  停止 tick 監控
  13:25  ForceCloseJob     : 強制平倉當沖部位
  13:35  PostMarketJob     : 停監控 + 存每日摘要
  13:35  DaytradingReview  : 收盤後檢討當日預測準確度

Run: python3 main.py
"""

import os
import signal
import time
from collections import defaultdict
from datetime import datetime, date
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from daytrading_config import load_daytrading_config, DaytradingConfig
from deep_analyzer import run_deep_analysis
from executor import place_stock_order, ExecutionResult, force_stop_loss, _normalize_action
from logger import get_logger
from market_scan import batch_fetch_snapshots
from market_scanner import ScanCriteria, get_all_stock_codes, screen_candidates, fetch_twse_sim_candidates
from monitor_agent import MonitorAgent, ensure_connected
from research_db import (
    init_db, save_daily_plan, load_daily_plan, save_daily_trade,
    save_daily_summary, DailySummaryRow, load_daily_trades,
)
from risk_guard import validate_plan
from user_confirm import send_confirmation, send_dt_buy_confirmation

log = get_logger(__name__)

# Track which years have already triggered an incomplete-calendar warning so the
# log isn't flooded — the scheduler calls is_trading_day() every 30–60 seconds.
_warned_incomplete_calendar_years: set[int] = set()

DB_PATH          = os.getenv("DB_PATH", "data/research.db")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CAPITAL          = float(os.getenv("BUDGET", "100000"))
HARD_LIMIT       = float(os.getenv("ORDER_HARD_LIMIT", "150000"))
SIMULATION       = os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"


# ── Pure helpers ──────────────────────────────────────────────────────────────

def is_trading_day(dt: datetime) -> bool:
    """Return True for TWSE trading days: Mon–Fri excluding public holidays.

    Two-stage check:
      1. Weekday gate  — Sat (5) and Sun (6) are never trading days.
      2. Holiday gate  — dates listed in ``tw_trading_calendar._TWSE_HOLIDAYS``
                         are also excluded (228, 清明, 春節, 勞動節, etc.).

    Degraded mode: if the year is not yet in the holiday calendar, the function
    falls back to weekday-only logic and emits a WARNING so operators know the
    system is running without holiday awareness for that year.
    """
    if dt.weekday() >= 5:
        return False
    from tw_trading_calendar import is_twse_holiday, is_year_in_calendar, is_calendar_complete
    year = dt.year
    if not is_year_in_calendar(year):
        log.warning(
            "is_trading_day: year %d is not in the TWSE holiday calendar — "
            "falling back to weekday-only logic (public holidays NOT excluded). "
            "Update tw_trading_calendar.py to fix this.",
            year,
        )
    elif not is_calendar_complete(year) and year not in _warned_incomplete_calendar_years:
        _warned_incomplete_calendar_years.add(year)
        log.warning(
            "is_trading_day: year %d holiday calendar is INCOMPLETE — "
            "some holidays (e.g. 端午節, 中秋節) are missing and those dates will "
            "be treated as trading days. Update tw_trading_calendar.py once "
            "TWSE publishes the official schedule.",
            year,
        )
    return not is_twse_holiday(dt.date())


def _confidence_budget(
    confidence: int,
    capital: float,
    min_pct: float = 0.02,
    max_pct: float = 0.05,
) -> float:
    """Map confidence (1–10) linearly to [min_pct, max_pct] of capital."""
    c = max(1, min(10, confidence))
    pct = min_pct + (c - 1) / 9 * (max_pct - min_pct)
    return capital * pct


def scan_candidates(
    api,
    criteria: Optional[ScanCriteria] = None,
    name_map: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Screen all listed stocks and return top candidates for deep analysis.

    Real mode   : Shioaji snapshots → screen_candidates()
    Simulation  : TWSE OpenAPI fallback when api is None or Shioaji fails
    """
    if api is not None:
        try:
            codes = get_all_stock_codes(api)
            snapshots = batch_fetch_snapshots(api, codes)
            if name_map:
                for code, snap in snapshots.items():
                    snap["name"] = name_map.get(code, code)
            rows = screen_candidates(snapshots, criteria or ScanCriteria())
            for row in rows:
                if "name" not in row and name_map:
                    row["name"] = name_map.get(row["code"], row["code"])
            return rows
        except Exception as e:
            log.warning("scan_candidates (Shioaji) failed: %s — 嘗試 TWSE fallback", e)

    # 模擬模式 / Shioaji 失敗 → TWSE OpenAPI
    log.info("scan_candidates: 使用 TWSE OpenAPI 取候選股（模擬模式）")
    return fetch_twse_sim_candidates(criteria)


def load_prior_orders(api) -> list[dict]:
    """Return today's already-placed orders from Shioaji as plain dicts.
    Used to prevent duplicate orders at 09:00.

    Each dict contains: code, action, quantity, price, date (ISO string).
    The ``date`` field is required by executor.is_duplicate_order() to block
    same-stock same-action same-day re-submissions.
    """
    if api is None:
        return []
    try:
        trades = api.list_trades()
        today = date.today().isoformat()
        return [
            {
                "code":     t.contract.code,
                # Shioaji returns 'Action.Buy' / 'Action.Sell'; normalize to
                # 'buy' / 'sell' so is_duplicate_order() matches correctly.
                "action":   _normalize_action(str(t.order.action)),
                "quantity": t.order.quantity,
                "price":    float(t.order.price),
                "date":     today,              # required by is_duplicate_order()
            }
            for t in (trades or [])
        ]
    except Exception as e:
        log.warning("load_prior_orders failed: %s", e)
        return []


def load_current_positions(trade_date: date, db_path: str) -> list[dict]:
    """Return today's executed buy trades as a risk_guard-compatible position list.

    Each returned dict has the shape expected by risk_guard.validate_plan():
        {code, name, sector, value, lot_type, quantity, price}

    Field mapping from daily_trades:
        sector   ← daily_trades.sector  (written at trade time from pick["sector"])
        value    ← daily_trades.amount  (cost basis = quantity × price)
        lot_type ← daily_trades.lot_type (written at trade time from ExecutionResult)

    Limitation: this reflects *today's executed buys* only.  It does NOT include
    positions carried over from previous sessions.  For a production multi-day
    position tracker, replace with a dedicated `positions` table (A1 task).
    """
    try:
        trades = load_daily_trades(trade_date, db_path)

        # ── Net-quantity approach ─────────────────────────────────────────────
        # Only CONFIRMED fills (action="sell") reduce the open position.
        # "force_close_requested" means the close ORDER was submitted, not that
        # it filled.  If the order is rejected or times out the position is still
        # open and must remain visible so ForceCloseJob can retry.
        #
        # Idempotency for real-mode close orders is handled separately by
        # ForceCloseJob.run() via an explicit pending_close_codes guard.
        #
        # Limitation: multiple buy trades for the same code are merged; value
        # and price reflect the LAST buy record.  For the current intraday
        # single-buy-per-stock model this is exact; multi-buy sessions are a
        # known TODO (A1 task: dedicated positions table).
        buy_qty:   defaultdict[str, int] = defaultdict(int)
        close_qty: defaultdict[str, int] = defaultdict(int)
        last_buy:  dict[str, dict]       = {}   # metadata from latest buy record

        for t in trades:
            code   = t["code"]
            action = t.get("action")
            qty    = t.get("quantity", 0)
            if action == "buy":
                buy_qty[code]  += qty
                last_buy[code]  = t
            elif action == "sell":          # only confirmed fills reduce position
                close_qty[code] += qty
            # "force_close_requested" is intentionally NOT counted here

        return [
            {
                "code":     code,
                "name":     last_buy[code].get("name", code),
                "sector":   last_buy[code].get("sector", "未知"),
                "value":    last_buy[code].get("amount", 0.0),
                "lot_type": last_buy[code].get("lot_type", "common"),
                "quantity": buy_qty[code] - close_qty.get(code, 0),
                "price":    last_buy[code].get("price", 0.0),
            }
            for code in buy_qty
            if buy_qty[code] - close_qty.get(code, 0) > 0
        ]
    except Exception as e:
        log.warning("load_current_positions failed: %s", e)
        return []


# ── PremarketJob ──────────────────────────────────────────────────────────────

class PremarketJob:
    """
    08:30 job:
      1. Run deep analysis on each candidate
      2. Filter to 'buy' signals only
      3. Validate with risk_guard
      4. Save approved picks to daily_plans
      5. Send Telegram confirmation keyboard
    Returns approved picks list.
    """

    def __init__(
        self,
        candidates: Optional[list[dict]],
        capital: float,
        db_path: str,
        telegram_chat_id: Optional[str],
        current_positions: Optional[list[dict]] = None,
        api=None,
    ) -> None:
        self._candidates       = candidates
        self._capital          = capital
        self._db_path          = db_path
        self._telegram_chat_id = telegram_chat_id
        self._current_positions = current_positions
        self._api              = api

    def run(self, market_summary: str = "", theme_info: str = "") -> list[dict]:
        candidates = (
            self._candidates
            if self._candidates is not None
            else scan_candidates(self._api)
        )
        buy_picks: list[dict] = []

        for cand in candidates:
            code = cand["code"]
            name = cand.get("name", code)
            try:
                analysis = run_deep_analysis(
                    api=self._api,
                    code=code, name=name, news=[],
                    fundamentals_text="", market_summary=market_summary,
                    theme_info=theme_info,
                )
                if analysis.signal != "buy":
                    continue
                buy_picks.append({
                    "code":             code,
                    "name":             name,
                    "budget":           _confidence_budget(analysis.confidence, self._capital),
                    "sector":           cand.get("sector", "未知"),
                    "signal":           analysis.signal,
                    "confidence":       analysis.confidence,
                    "target_price":     analysis.target_price,
                    "stop_loss_price":  analysis.stop_loss_price,
                })
            except Exception as e:
                log.warning("PremarketJob analysis failed for %s: %s", code, e)

        positions = (
            self._current_positions
            if self._current_positions is not None
            else load_current_positions(date.today(), self._db_path)
        )
        result = validate_plan(buy_picks, self._capital, positions)
        approved: list[dict] = result["approved"]
        rejected: list[dict] = result["rejected"]

        save_daily_plan(date.today(), approved, self._db_path)
        log.info("Premarket: %d approved, %d rejected", len(approved), len(rejected))

        if approved and self._telegram_chat_id:
            send_confirmation(approved, self._telegram_chat_id)

        return approved


# ── MarketOpenJob ─────────────────────────────────────────────────────────────

class MarketOpenJob:
    """
    09:00 job:
      1. Execute approved picks via executor (with safety guards)
      2. Save trade records to daily_trades
      3. Start MonitorAgent watching the new positions
    Returns the running MonitorAgent instance.
    """

    def __init__(
        self,
        api,
        approved_picks: list[dict],
        db_path: str,
        telegram_chat_id: Optional[str],
        hard_limit: float,
        prior_orders: Optional[list[dict]] = None,
    ) -> None:
        self._api             = api
        self._picks           = approved_picks
        self._db_path         = db_path
        self._telegram_chat_id = telegram_chat_id
        self._hard_limit      = hard_limit
        self._prior_orders    = prior_orders
        init_db(db_path)

    def _resolve_picks(self) -> list[dict]:
        """Determine the authoritative pick list for 09:00 execution.

        Single-source-of-truth rule
        ───────────────────────────
        DB read succeeds  → use DB result, regardless of whether it is empty.
                            An empty list means "zero orders for today"
                            (e.g. user pressed reject_all on Telegram between
                            08:30 and 09:00).  We NEVER fall back to the
                            in-memory list in this path because that would
                            silently undo an explicit user rejection.
        DB read raises    → DEGRADED MODE: fall back to in-memory picks and
                            emit a WARNING so the operator can see the system
                            is running without the authoritative DB state.
        """
        try:
            picks = load_daily_plan(date.today(), self._db_path)
            if not picks:
                log.info(
                    "MarketOpenJob: DB plan is empty for %s — zero orders today "
                    "(all picks rejected or none approved)",
                    date.today(),
                )
            return picks
        except Exception as e:
            log.warning(
                "MarketOpenJob: DB read failed — DEGRADED MODE, "
                "falling back to in-memory picks. Cause: %s", e,
            )
            return self._picks

    def run(self) -> MonitorAgent:
        picks_to_execute = self._resolve_picks()
        prior_orders = (
            self._prior_orders
            if self._prior_orders is not None
            else load_prior_orders(self._api)
        )
        executed_picks: list[dict] = []

        for pick in picks_to_execute:
            code  = pick["code"]
            name  = pick.get("name", code)
            price = self._get_price(code)

            result: ExecutionResult = place_stock_order(
                api=self._api,
                code=code,
                name=name,
                action="buy",
                budget=pick.get("budget", 0),
                price=price,
                hard_limit=self._hard_limit,
                prior_orders=prior_orders,
            )

            if result.success:
                try:
                    save_daily_trade({
                        "trade_date": date.today(),
                        "code":       result.code,
                        "name":       result.name,
                        "action":     result.action,
                        "quantity":   result.quantity,
                        "price":      result.price,
                        "amount":     result.amount,
                        "pnl":        None,
                        "lot_type":   result.lot_type,            # proper column (not note)
                        "sector":     pick.get("sector", "未知"),  # proper column (not note)
                        "note":       f"id={result.order_id}",    # note: only order_id now
                    }, self._db_path)
                except Exception as db_err:
                    log.error("save_daily_trade failed for %s: %s", code, db_err)
                executed_picks.append(pick)
                log.info("Executed: %s %s", code, result.order_id)
            else:
                log.warning("Skipped %s: %s", code, result.reason)

        monitor = MonitorAgent(
            api_key=os.getenv("SHIOAJI_API_KEY", ""),
            secret_key=os.getenv("SHIOAJI_SECRET_KEY", ""),
            simulation=SIMULATION,
            db_path=self._db_path,
            telegram_chat_id=self._telegram_chat_id,
            api=self._api,
        )
        monitor.set_watchlist(executed_picks)
        monitor.start()
        return monitor

    def _get_price(self, code: str) -> float:
        try:
            contract = self._api.Contracts.Stocks.get(code)
            if not contract:
                return 0.0
            snaps = self._api.snapshots([contract])
            return float(snaps[0].close) if snaps else 0.0
        except Exception:
            return 0.0


# ── PostMarketJob ─────────────────────────────────────────────────────────────

class PostMarketJob:
    """
    13:35 job:
      1. Stop MonitorAgent
      2. Save DailySummaryRow to research_db
    """

    def __init__(
        self,
        monitor: Optional[MonitorAgent],
        db_path: str,
        execution_id: str,
    ) -> None:
        self._monitor      = monitor
        self._db_path      = db_path
        self._execution_id = execution_id

    def run(self, trades_summary: str) -> float:
        """Stop monitor, compute PnL from sell trades in DB, save daily summary.

        PnL is computed by summing ``pnl`` on all ``daily_trades`` for today.
        Force-close sell trades are written with real PnL by ForceCloseJob, so
        this value reflects actual realized results — not a caller-supplied
        constant.

        Returns the computed ``total_pnl`` so callers can use it for
        notifications without an extra DB round-trip.
        """
        if self._monitor is not None:
            self._monitor.stop()

        try:
            trades = load_daily_trades(date.today(), self._db_path)
            # Only sum PnL from CONFIRMED sell trades.
            # "force_close_requested" records have pnl=None because the fill is
            # not yet confirmed — they must NOT contribute to realized PnL.
            total_pnl = sum(
                t.get("pnl") or 0.0
                for t in trades
                if t.get("action") == "sell"
            )
            pending_closes = sum(
                1 for t in trades if t.get("action") == "force_close_requested"
            )
            if pending_closes:
                log.warning(
                    "PostMarket: %d force_close_requested order(s) with unconfirmed fill — "
                    "excluded from realized PnL", pending_closes,
                )
        except Exception as e:
            log.warning("PostMarket: could not load trades for PnL: %s", e)
            total_pnl = 0.0

        row = DailySummaryRow(
            execution_id=self._execution_id,
            date=date.today(),
            total_pnl=total_pnl,
            target_met=total_pnl > 0,
            review=trades_summary,
            next_day_plan="",
        )
        save_daily_summary(row, self._db_path)
        log.info("PostMarket: pnl=%+.0f saved", total_pnl)
        return total_pnl


# ── ForceCloseJob helpers ─────────────────────────────────────────────────────

def _get_snapshot_price(api, code: str, fallback: float) -> float:
    """Return the latest close price from Shioaji snapshot, or ``fallback``."""
    try:
        contract = api.Contracts.Stocks.get(code)
        if contract:
            snaps = api.snapshots([contract])
            if snaps:
                return float(snaps[0].close)
    except Exception:
        pass
    return fallback


# ── ForceCloseJob ─────────────────────────────────────────────────────────────

class ForceCloseJob:
    """
    13:25 job (10 min before market close):
      Sell all open buy positions at market price to avoid overnight exposure.
    """

    def __init__(self, api, db_path: str) -> None:
        self._api     = api
        self._db_path = db_path

    def run(self) -> list[dict]:
        positions = load_current_positions(date.today(), self._db_path)

        # Idempotency guard — real mode only.
        # load_current_positions() does NOT subtract force_close_requested from
        # position quantities (those orders may not have filled).  Without this
        # guard ForceCloseJob would re-submit a close order every time it runs
        # while the position is still open and no confirmed sell exists yet.
        #
        # Simulation mode never populates this set because ForceCloseJob writes
        # action="sell" there, which IS subtracted by load_current_positions().
        try:
            all_trades = load_daily_trades(date.today(), self._db_path)
        except Exception:
            all_trades = []
        pending_close_codes = {
            t["code"] for t in all_trades
            if t.get("action") == "force_close_requested"
        }

        results = []
        for pos in positions:
            code      = pos["code"]
            name      = pos.get("name", code)
            quantity  = pos.get("quantity", 0)
            lot_type  = pos.get("lot_type", "common")
            buy_price = pos.get("price", 0.0)

            # Skip if a close order is already outstanding (real mode).
            if code in pending_close_codes:
                log.info(
                    "ForceClose: skipping %s — pending close order already submitted",
                    code,
                )
                results.append({"code": code, "success": True})
                continue

            # Best-effort snapshot price; fall back to buy price when API is
            # unavailable (e.g. tests, network outage).
            close_price = _get_snapshot_price(self._api, code, fallback=buy_price)

            success = force_stop_loss(
                api=self._api,
                code=code,
                name=name,
                quantity=quantity,
                lot_type=lot_type,
            )

            if success:
                multiplier       = 1000 if lot_type == "common" else 1
                estimated_amount = close_price * quantity * multiplier

                if SIMULATION:
                    # Simulation fills are guaranteed — write a confirmed sell trade
                    # with computed PnL so PostMarketJob can report a real daily result.
                    pnl = (close_price - buy_price) * quantity * multiplier
                    try:
                        save_daily_trade({
                            "trade_date": date.today(),
                            "code":       code,
                            "name":       name,
                            "action":     "sell",              # confirmed in simulation
                            "quantity":   quantity,
                            "price":      close_price,
                            "amount":     estimated_amount,
                            "pnl":        pnl,
                            "lot_type":   lot_type,
                            "sector":     pos.get("sector", "未知"),
                            "note":       "force_close_simulation",
                        }, self._db_path)
                    except Exception as db_err:
                        log.error("ForceClose: save simulation sell record failed %s: %s", code, db_err)
                    log.info(
                        "ForceClose: simulation sell %s qty=%d lot=%s pnl=%+.0f",
                        code, quantity, lot_type, pnl,
                    )
                else:
                    # Real mode: order submitted but fill is unconfirmed.
                    # Write a "force_close_requested" record so:
                    #   1. pending_close_codes blocks re-submission on next run
                    #   2. PostMarketJob does NOT count unconfirmed PnL
                    try:
                        save_daily_trade({
                            "trade_date": date.today(),
                            "code":       code,
                            "name":       name,
                            "action":     "force_close_requested",
                            "quantity":   quantity,
                            "price":      close_price,         # snapshot at submission time
                            "amount":     estimated_amount,    # estimated; not a confirmed fill
                            "pnl":        None,                # unknown until fill confirmed
                            "lot_type":   lot_type,
                            "sector":     pos.get("sector", "未知"),
                            "note":       "force_close_requested",
                        }, self._db_path)
                    except Exception as db_err:
                        log.error("ForceClose: save pending record failed %s: %s", code, db_err)
                    log.info(
                        "ForceClose: order submitted %s qty=%d lot=%s (fill unconfirmed)",
                        code, quantity, lot_type,
                    )
            else:
                log.error("ForceClose: order submission failed %s", code)

            results.append({"code": code, "success": success})
        return results


# ── Day-trading auto buy ─────────────────────────────────────────────────────

def _auto_buy_dt_positions(
    api,
    positions: list,
    dt_config: "DaytradingConfig",
    dt_path: str = "data/daytrading_positions.json",
) -> None:
    """DT_MANUAL_CONFIRM=false 時，自動買入所有 watching 當沖持倉。"""
    from daytrading_monitor import (
        load_daytrading_positions, save_daytrading_positions,
        mark_position_entered, fetch_current_price,
    )

    all_positions = load_daytrading_positions(path=dt_path)
    pos_map = {p.code: p for p in all_positions if p.status == "watching"}
    changed = False

    for pos in positions:
        code = pos.code
        if code not in pos_map:
            continue
        try:
            price = fetch_current_price(code, api=api)
            if price is None:
                log.warning("DT auto-buy: 無法取得 %s 報價，跳過", code)
                continue
            result = place_stock_order(
                api=api,
                code=code, name=pos_map[code].name,
                action="buy",
                budget=dt_config.budget_per_stock,
                price=price,
                hard_limit=HARD_LIMIT,
            )
            if result.success:
                mark_position_entered(pos_map[code], result.price, result.quantity, result.lot_type)
                changed = True
                log.info("DT auto-buy: %s qty=%d price=%.2f", code, result.quantity, result.price)
                if TELEGRAM_CHAT_ID:
                    try:
                        from telegram_bot import send_text
                        send_text(
                            TELEGRAM_CHAT_ID,
                            f"🤖 當沖自動買入：<b>{code} {pos_map[code].name}</b>\n"
                            f"買入價 {result.price:,.2f}　"
                            f"數量 {result.quantity}"
                            f"{'張' if result.lot_type == 'common' else '股'}\n"
                            f"金額 {result.amount:,.0f} 元",
                        )
                    except Exception:
                        pass
            else:
                log.warning("DT auto-buy failed %s: %s", code, result.reason)
        except Exception as e:
            log.warning("DT auto-buy exception %s: %s", code, e)

    if changed:
        save_daytrading_positions(all_positions, path=dt_path)


# ── Day-trading sell-signal executor ─────────────────────────────────────────

def _run_dt_sell_alerts(
    api,
    sell_alerts: list,
    dt_config: "DaytradingConfig",
    dt_path: str = "data/daytrading_positions.json",
) -> None:
    """觸發追蹤停利 / 停損 / 強制平倉時，自動執行賣單並更新持倉狀態。

    所有 DT 賣單均自動執行（不需手動確認），確保及時出場。
    `require_manual_confirm` 只管理買入確認，不影響出場。
    """
    from daytrading_monitor import (
        load_daytrading_positions,
        save_daytrading_positions,
        format_alerts_message,
    )

    positions = load_daytrading_positions(path=dt_path)
    pos_map = {p.code: p for p in positions if p.status == "active"}
    changed = False

    for alert in sell_alerts:
        code = alert.code
        pos = pos_map.get(code)
        if pos is None:
            log.warning("DT sell alert for %s but no active position found", code)
            continue

        if pos.quantity <= 0:
            log.warning("DT sell alert for %s but quantity=0, skipping", code)
            continue

        success = force_stop_loss(
            api=api,
            code=pos.code,
            name=pos.name,
            quantity=pos.quantity,
            lot_type=pos.lot_type,
        )
        if success:
            pos.status = "closed"
            changed = True
            log.info(
                "DT 出場：%s %s reason=%s price=%.2f",
                code, pos.name, alert.alert_type, alert.price,
            )
        else:
            log.error("DT 出場失敗：%s %s", code, pos.name)

        if TELEGRAM_CHAT_ID:
            try:
                from telegram_bot import send_text
                status_tag = "✅ 出場已送出" if success else "❌ 出場失敗，請手動處理"
                send_text(
                    TELEGRAM_CHAT_ID,
                    f"{status_tag}\n{format_alerts_message([alert])}",
                )
            except Exception as e:
                log.warning("DT sell notify failed: %s", e)

    if changed:
        save_daytrading_positions(positions, path=dt_path)


# ── DT Opening Confirmation (9:05) ───────────────────────────────────────────

def _opening_confirm_dt_positions(
    api,
    dt_config: "DaytradingConfig",
    dt_path: str = "data/daytrading_positions.json",
) -> None:
    """9:05 開盤再確認：AI 結合 8:30 預測 + 當前大盤氣氛，給出進場或放棄建議。"""
    from daytrading_monitor import (
        load_daytrading_positions, save_daytrading_positions, mark_position_skipped,
    )
    from monitor_agent import get_snapshot
    from daytrading_analyzer import run_opening_reconfirm

    all_positions = load_daytrading_positions(path=dt_path)
    watching      = [p for p in all_positions if p.status == "watching"]
    if not watching:
        log.info("DT 9:05 確認：無 watching 持倉")
        return

    # 大盤氣氛（一次抓，所有股票共用）
    from daytrading_report import _fetch_market
    market = _fetch_market()
    log.info(
        "DT 9:05 大盤：index=%+.2f%% futures=%+.2f%%",
        market.get("index_change_pct", 0),
        market.get("futures_premium_pct", 0),
    )

    confirmed: list = []
    skipped:   list = []
    reasons:   dict = {}   # code → reason string
    snap_cache: dict = {}
    changed = False

    for pos in watching:
        snap = get_snapshot(api, pos.code) if api is not None else None
        snap_cache[pos.code] = snap or {}

        current_price = snap["close"]        if snap else 0.0
        change_price  = snap.get("change_price", 0.0) if snap else 0.0
        volume        = snap.get("volume",       0)   if snap else 0

        result = run_opening_reconfirm(
            code=pos.code, name=pos.name,
            dt_score=pos.dt_score,
            entry_low=pos.entry_low, entry_high=pos.entry_high,
            target_price=pos.target_price, stop_loss=pos.stop_loss,
            ai_summary=pos.ai_summary,
            current_price=current_price,
            change_price=change_price,
            volume=volume,
            market=market,
        )

        if result.proceed:
            # AI 可能調整進場區間
            if result.updated_entry_low is not None:
                pos.entry_low  = result.updated_entry_low
                changed = True
            if result.updated_entry_high is not None:
                pos.entry_high = result.updated_entry_high
                changed = True
            confirmed.append(pos)
            log.info("DT 9:05 確認進場 %s %s: %s", pos.code, pos.name, result.reason)
        else:
            mark_position_skipped(pos)
            skipped.append(pos)
            reasons[pos.code] = result.reason
            changed = True
            log.info("DT 9:05 放棄 %s %s: %s", pos.code, pos.name, result.reason)

    if changed:
        save_daytrading_positions(all_positions, path=dt_path)

    log.info("DT 9:05 確認：%d 繼續 / %d 放棄", len(confirmed), len(skipped))

    if not TELEGRAM_CHAT_ID:
        return

    try:
        from telegram_bot import send_text
        idx_pct = market.get("index_change_pct", 0.0)
        fp_pct  = market.get("futures_premium_pct", 0.0)
        idx_arrow = "📈" if idx_pct > 0 else ("📉" if idx_pct < 0 else "📊")

        lines = [
            "⚡ <b>當沖開盤確認 09:05</b>",
            f"{idx_arrow} 大盤 {idx_pct:+.2f}%　台指期溢貼水 {fp_pct:+.2f}%",
            "━━━━━━━━━━━━━━━━",
        ]

        if confirmed:
            for pos in confirmed:
                s   = snap_cache.get(pos.code, {})
                chg = s.get("change_price", 0.0)
                entry_str = (
                    f"{pos.entry_low:,.1f}–{pos.entry_high:,.1f}"
                    if pos.entry_low and pos.entry_high else "—"
                )
                lines.append(
                    f"✅ <b>{pos.code} {pos.name}</b>\n"
                    f"   現價 {s.get('close', '—')}　漲跌 {chg:+.2f}　進場區間 {entry_str}"
                )

        if skipped:
            lines.append("")
            lines.append("❌ <b>AI 建議放棄</b>")
            for pos in skipped:
                lines.append(
                    f"  · <b>{pos.code} {pos.name}</b>　{reasons.get(pos.code, '')}"
                )

        lines.append("")
        if confirmed:
            action = "送出買入確認" if dt_config.require_manual_confirm else "自動買入"
            lines.append(f"→ 09:10 將{action} {len(confirmed)} 支")
        else:
            lines.append("<i>今日 AI 建議放棄所有當沖候選</i>")

        send_text(TELEGRAM_CHAT_ID, "\n".join(lines))
    except Exception as e:
        log.warning("DT 9:05 確認通知失敗: %s", e)


# ── DT Mid-session Check (10:00) ─────────────────────────────────────────────

def _mid_session_check(
    api,
    dt_path: str = "data/daytrading_positions.json",
) -> None:
    """10:00 盤中檢查：active 損益報告 + 剩餘 watching 候選提示。"""
    from daytrading_monitor import load_daytrading_positions, fetch_current_price

    positions = load_daytrading_positions(path=dt_path)
    active    = [p for p in positions if p.status == "active"]
    watching  = [p for p in positions if p.status == "watching"]

    if not active and not watching:
        return
    if not TELEGRAM_CHAT_ID:
        return

    lines = ["📊 <b>當沖盤中檢查 10:00</b>", "━━━━━━━━━━━━━━━━"]

    if active:
        lines.append("<b>📌 持倉損益</b>")
        for pos in active:
            price = fetch_current_price(pos.code, api=api)
            if price is not None and pos.entry_price:
                multiplier = 1000 if pos.lot_type == "common" else 1
                pnl        = (price - pos.entry_price) * pos.quantity * multiplier
                pnl_pct    = (price - pos.entry_price) / pos.entry_price * 100
                tag        = "🟢" if pnl >= 0 else "🔴"
                lines.append(
                    f"{tag} <b>{pos.code} {pos.name}</b>　"
                    f"買 {pos.entry_price:.2f} → 現 {price:.2f}　"
                    f"損益 {pnl:+,.0f} 元（{pnl_pct:+.1f}%）"
                )
            else:
                lines.append(f"⬜ <b>{pos.code} {pos.name}</b>　無法取得報價")

    if watching:
        lines.append("")
        lines.append("<b>👀 仍在觀察中（可二次進場）</b>")
        for pos in watching:
            price     = fetch_current_price(pos.code, api=api)
            price_str = f"{price:.2f}" if price is not None else "—"
            lines.append(f"  · <b>{pos.code} {pos.name}</b>　現價 {price_str}")

    try:
        from telegram_bot import send_text
        send_text(TELEGRAM_CHAT_ID, "\n".join(lines))
    except Exception as e:
        log.warning("DT 10:00 盤中通知失敗: %s", e)


# ── DT Exit Warning (13:00) ───────────────────────────────────────────────────

def _exit_warning(
    api,
    dt_path: str = "data/daytrading_positions.json",
) -> None:
    """13:00 出場警報：距強制平倉 30 分鐘提醒，附各持倉損益。"""
    from daytrading_monitor import load_daytrading_positions, fetch_current_price

    positions = load_daytrading_positions(path=dt_path)
    active    = [p for p in positions if p.status == "active"]

    if not TELEGRAM_CHAT_ID:
        return

    lines = [
        "⏰ <b>當沖出場警報 13:00</b>",
        "距強制平倉還有 <b>30 分鐘</b>，請評估出場時機",
        "━━━━━━━━━━━━━━━━",
    ]

    if not active:
        lines.append("目前無 active 持倉。")
    else:
        total_pnl = 0.0
        for pos in active:
            price = fetch_current_price(pos.code, api=api)
            if price is not None and pos.entry_price:
                multiplier = 1000 if pos.lot_type == "common" else 1
                pnl        = (price - pos.entry_price) * pos.quantity * multiplier
                pnl_pct    = (price - pos.entry_price) / pos.entry_price * 100
                total_pnl += pnl
                tag        = "🟢" if pnl >= 0 else "🔴"
                lines.append(
                    f"{tag} <b>{pos.code} {pos.name}</b>　"
                    f"買 {pos.entry_price:.2f} → 現 {price:.2f}　"
                    f"損益 {pnl:+,.0f} 元（{pnl_pct:+.1f}%）"
                )
            else:
                lines.append(f"⬜ <b>{pos.code} {pos.name}</b>　無法取得報價")
        lines.append(f"\n💰 合計損益：<b>{total_pnl:+,.0f} 元</b>")
        lines.append("<i>⚠️ 13:25 系統將自動強制平倉</i>")

    try:
        from telegram_bot import send_text
        send_text(TELEGRAM_CHAT_ID, "\n".join(lines))
    except Exception as e:
        log.warning("DT 13:00 出場警報失敗: %s", e)

    log.info("DT 出場警報已送出，%d 個 active 持倉", len(active))


# ── Main loop ─────────────────────────────────────────────────────────────────

_RUNNING = True

# 盤中當沖監控：09:15–13:15 每 5 分鐘掃描一次
from datetime import time as _dtime
_DT_MON_END = _dtime(13, 15)   # tick 訂閱到此時段結束後取消


def _handle_signal(sig, frame):
    global _RUNNING
    log.info("Shutdown signal received")
    _RUNNING = False


def main() -> None:
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    import os
    os.makedirs("data", exist_ok=True)
    init_db(DB_PATH)

    log.info("main.py started — simulation=%s", SIMULATION)

    from notifier import notify_system_start, notify_system_stop, notify_market_open, notify_market_close
    notify_system_start(SIMULATION)

    api = ensure_connected(
        os.getenv("SHIOAJI_API_KEY", ""),
        os.getenv("SHIOAJI_SECRET_KEY", ""),
        simulation=SIMULATION,
    )

    dt_config = load_daytrading_config()
    log.info(
        "DT config: budget=%.0f stop_loss=%.1f%% take_profit=%.1f%% "
        "trailing_start=%.1f%% trailing_gap=%.1f%% force_close=%s manual_confirm=%s",
        dt_config.budget_per_stock,
        dt_config.stop_loss_pct,
        dt_config.take_profit_pct,
        dt_config.trailing_start_pct,
        dt_config.trailing_gap_pct,
        dt_config.force_close_time,
        dt_config.require_manual_confirm,
    )

    monitor: Optional[MonitorAgent] = None
    approved_picks: list[dict] = []
    _dt_agent = None   # MonitorAgent（tick 訂閱，09:05 下單後啟動）

    while _RUNNING:
        now = datetime.now()
        if not is_trading_day(now):
            time.sleep(60)
            continue

        t = now.time()

        from halt import is_halted
        if is_halted():
            time.sleep(30)
            continue

        # ── 13:15 停止 tick 訂閱 ────────────────────────────────────────
        if t >= _DT_MON_END and _dt_agent is not None:
            _dt_agent.stop()
            _dt_agent = None
            log.info("DT tick monitor stopped at 13:15")

        # 08:30 pre-market：波段選股 + 當沖預測報告（同時推播 Telegram）
        if t.hour == 8 and t.minute == 30:
            # 波段選股（原有邏輯）
            job = PremarketJob(
                candidates=None,
                capital=CAPITAL,
                db_path=DB_PATH,
                telegram_chat_id=TELEGRAM_CHAT_ID or None,
                current_positions=None,
                api=api,
            )
            approved_picks = job.run()

            # 當沖預測：在開盤前 30 分鐘推播
            try:
                from daytrading_report import build_daytrading_report
                report = build_daytrading_report(api=api, db_path=DB_PATH)
                if TELEGRAM_CHAT_ID:
                    from telegram_bot import send_text
                    send_text(TELEGRAM_CHAT_ID, report)
            except Exception as e:
                log.warning("DayTrading report push failed: %s", e)

            time.sleep(60)

        # 09:00 market open
        elif t.hour == 9 and t.minute == 0:
            notify_market_open()
            if api and approved_picks:
                job = MarketOpenJob(
                    api=api,
                    approved_picks=approved_picks,
                    db_path=DB_PATH,
                    telegram_chat_id=TELEGRAM_CHAT_ID or None,
                    hard_limit=HARD_LIMIT,
                    prior_orders=None,
                )
                monitor = job.run()
            time.sleep(60)

        # 09:05 開盤確認：量能/方向 OK → 保留 watching；不符合 → skipped
        elif t.hour == 9 and t.minute == 5:
            try:
                _opening_confirm_dt_positions(api, dt_config)
            except Exception as e:
                log.warning("DT 開盤確認失敗: %s", e)
            time.sleep(60)

        # 09:10 當沖下單進場（第一波）+ 啟動 tick 監控
        elif t.hour == 9 and t.minute == 10:
            try:
                from daytrading_monitor import load_daytrading_positions
                dt_watching = [p for p in load_daytrading_positions() if p.status == "watching"]
                if dt_watching:
                    if dt_config.require_manual_confirm:
                        if TELEGRAM_CHAT_ID:
                            send_dt_buy_confirmation(dt_watching, TELEGRAM_CHAT_ID, dt_config.budget_per_stock)
                    else:
                        _auto_buy_dt_positions(api, dt_watching, dt_config)
                else:
                    log.info("DT 9:10 進場：無 watching 持倉（9:05 全數過濾）")
            except Exception as e:
                log.warning("DT 9:10 下單失敗: %s", e)

            # 下單後啟動 tick 訂閱監控
            try:
                from daytrading_monitor import load_daytrading_positions
                from monitor_agent import MonitorAgent
                positions = load_daytrading_positions()
                watchlist = [
                    {
                        "code":            p.code,
                        "name":            p.name,
                        "target_price":    p.target_price,
                        "stop_loss_price": p.stop_loss,
                    }
                    for p in positions if p.status in ("watching", "active")
                ]
                if watchlist and api is not None:
                    _dt_agent = MonitorAgent(
                        api_key="", secret_key="", simulation=False,
                        db_path=DB_PATH, telegram_chat_id=TELEGRAM_CHAT_ID,
                        api=api,
                        trailing_start_pct=dt_config.trailing_start_pct,
                        trailing_gap_pct=dt_config.trailing_gap_pct,
                    )
                    _dt_agent.set_watchlist(watchlist)
                    _dt_agent.start()
                    log.info("DT tick 監控啟動，%d 個持倉", len(watchlist))
            except Exception as e:
                log.warning("DT tick 監控啟動失敗: %s", e)
            time.sleep(60)

        # 10:00 盤中檢查：持倉損益 + 剩餘候選（可選擇二次進場）
        elif t.hour == 10 and t.minute == 0:
            try:
                _mid_session_check(api)
            except Exception as e:
                log.warning("DT 盤中檢查失敗: %s", e)
            time.sleep(60)

        # 13:00 出場警報：30 分鐘倒數提醒
        elif t.hour == 13 and t.minute == 0:
            try:
                _exit_warning(api)
            except Exception as e:
                log.warning("DT 出場警報失敗: %s", e)
            time.sleep(60)

        # 13:25 force-close all positions before market close
        elif t.hour == 13 and t.minute == 25:
            if api:
                ForceCloseJob(api=api, db_path=DB_PATH).run()
            time.sleep(60)

        # 13:35 post-market
        elif t.hour == 13 and t.minute == 35:
            job = PostMarketJob(
                monitor=monitor,
                db_path=DB_PATH,
                execution_id=f"main-{now.date().isoformat()}",
            )
            total_pnl = job.run(trades_summary="自動收盤")
            trades = load_daily_trades(date.today(), DB_PATH)
            notify_market_close(total_pnl=total_pnl, trade_count=len(trades))
            monitor = None
            approved_picks = []

            # 當沖預測複盤：回填今日 OHLC + 判斷是否預測正確
            try:
                from daytrading_review import run_daytrading_review
                review_msg = run_daytrading_review()
                if review_msg:
                    from telegram_bot import send_text
                    send_text(TELEGRAM_CHAT_ID, review_msg)
            except Exception as e:
                log.warning("daytrading_review failed: %s", e)

            time.sleep(60)

        else:
            time.sleep(30)

    if monitor:
        monitor.stop()
    notify_system_stop()
    log.info("main.py stopped")


if __name__ == "__main__":
    main()
