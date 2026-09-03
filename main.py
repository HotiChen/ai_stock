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
  13:15  ForceCloseJob     : 強制平倉當沖部位（config.FORCE_CLOSE_TIME）
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
from executor import place_stock_order, ExecutionResult, force_stop_loss, _normalize_action, calc_risk_quantity
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
PAPER_TRADING    = os.getenv("PAPER_TRADING", "true").lower() != "false"


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
                paper_trading=PAPER_TRADING,
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


# ── PlaybookUpdateJob ─────────────────────────────────────────────────────────

class PlaybookUpdateJob:
    """
    13:50 job（PostMarketJob 13:35 之後）：
      呼叫 playbook_updater.run_daily_update() 更新 research_playbook.md 自適應區。
    任何失敗只記 log，不影響排程器繼續運行。
    """

    def run(self) -> None:
        try:
            import playbook_updater
            playbook_updater.run_daily_update()
        except Exception as e:
            log.warning("PlaybookUpdateJob: playbook updater 失敗（已忽略）：%s", e)


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
    收盤強平 job（config.FORCE_CLOSE_TIME，預設 13:15——落在連續交易時段內，
    市價單有效；13:25 起為收盤集合競價，市價單會被退單）:
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
                paper_trading=PAPER_TRADING,
            )

            if success:
                # 若為當沖持倉，原子標記 closed：強平後（至輪詢時段結束前）
                # 的 5 分鐘輪詢不得對已強平的持倉再送第二筆賣單。
                try:
                    from daytrading_monitor import claim_for_close
                    if claim_for_close(code):
                        log.info("ForceClose: DT 持倉 %s 已標記 closed", code)
                except Exception as dt_err:
                    log.warning("ForceClose: DT claim_for_close(%s) 失敗: %s", code, dt_err)

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

def _save_dt_buy_trade(result, db_path: str = DB_PATH, chat_id: Optional[str] = None) -> None:
    """當沖買單成交後寫入 daily_trades（action="buy"），讓收盤 ForceCloseJob /
    load_current_positions 看得見當沖持倉。

    下單已成功，DB 寫入失敗不得中斷流程：log.error + Telegram 告警即可。
    """
    try:
        save_daily_trade({
            "trade_date": date.today(),
            "code":       result.code,
            "name":       result.name,
            "action":     "buy",
            "quantity":   result.quantity,
            "price":      result.price,
            "amount":     result.amount,
            "pnl":        None,
            "lot_type":   result.lot_type,
            "sector":     "當沖",
            "note":       "daytrade_buy",
        }, db_path)
    except Exception as db_err:
        log.error("DT buy save_daily_trade failed for %s: %s", getattr(result, "code", "?"), db_err)
        if chat_id:
            try:
                from telegram_bot import send_text
                send_text(
                    chat_id,
                    f"⚠️ 當沖買入 {getattr(result, 'code', '?')} 已成交，"
                    f"但持倉記錄寫入失敗：{db_err}\n請確認收盤強平能看到此持倉！",
                )
            except Exception:
                pass


def _auto_buy_dt_positions(
    api,
    positions: list,
    dt_config: "DaytradingConfig",
    dt_path: str = "data/daytrading_positions.json",
    db_path: str = DB_PATH,
) -> None:
    """DT_MANUAL_CONFIRM=false 時，自動買入所有 watching 當沖持倉。

    當日虧損熔斷觸發後（dt_risk.is_circuit_breaker_active）拒絕所有新買單。
    有停損價時改用風險額倉位法（calc_risk_quantity）決定下單金額；缺停損價
    或算出 0 股時退回固定 budget_per_stock 預算法（行為相容）。
    """
    from daytrading_monitor import (
        load_daytrading_positions, mark_entered, fetch_current_price,
    )
    import dt_risk

    # HALT：提早跳過。executor 的 Guard 0 已保證買單會被擋，但不在這裡攔的話
    # 仍會照常推播「要買嗎」的 Telegram 確認，使用者按下確認之後才發現訂單被擋。
    try:
        from halt import is_halted
        if is_halted():
            log.warning("系統緊急暫停中，跳過自動買入（%d 檔候選）", len(positions))
            return
    except Exception as e:
        log.warning("HALT 狀態讀取失敗（繼續，仍有 executor Guard 0 把關）: %s", e)

    if dt_risk.is_circuit_breaker_active():
        log.warning("DT 熔斷中，跳過本輪自動買入（%d 檔候選）", len(positions))
        if TELEGRAM_CHAT_ID:
            try:
                from telegram_bot import send_text
                flag = dt_risk.get_circuit_breaker_flag()
                send_text(
                    TELEGRAM_CHAT_ID,
                    f"🚫 當日虧損熔斷已觸發，本日不再自動買入。\n{flag.get('message', '')}",
                )
            except Exception:
                pass
        return

    all_positions = load_daytrading_positions(path=dt_path)
    pos_map = {p.code: p for p in all_positions if p.status == "watching"}

    for pos in positions:
        code = pos.code
        if code not in pos_map:
            continue
        try:
            price = fetch_current_price(code, api=api)
            if price is None:
                log.warning("DT auto-buy: 無法取得 %s 報價，跳過", code)
                continue

            stop_loss_price = pos_map[code].stop_loss
            budget = dt_config.budget_per_stock
            risk_shares, risk_reason = calc_risk_quantity(
                total_budget=CAPITAL,
                risk_pct=dt_config.risk_per_trade_pct,
                entry_price=price,
                stop_loss_price=stop_loss_price,
                budget_cap=dt_config.budget_per_stock,
                hard_limit=HARD_LIMIT,
            )
            if risk_shares > 0:
                budget = risk_shares * price
                log.info("DT auto-buy risk sizing: %s shares=%d budget=%.0f",
                         code, risk_shares, budget)
            elif risk_reason:
                log.debug("DT auto-buy risk sizing fallback %s: %s", code, risk_reason)

            result = place_stock_order(
                api=api,
                code=code, name=pos_map[code].name,
                action="buy",
                budget=budget,
                price=price,
                hard_limit=HARD_LIMIT,
                paper_trading=PAPER_TRADING,
            )
            if result.success:
                # 原子單筆進場標記（不覆蓋其他持倉；跨 process 安全）
                entered = mark_entered(code, result.price, result.quantity,
                                       result.lot_type, path=dt_path)
                if not entered:
                    # 券商已成交但持倉狀態機沒有這筆 → 監控/強平都看不到，必須告警
                    log.error("DT auto-buy: %s 不在今日持倉庫，狀態未更新（券商已成交）", code)
                    if TELEGRAM_CHAT_ID:
                        try:
                            from telegram_bot import send_text
                            send_text(
                                TELEGRAM_CHAT_ID,
                                f"⚠️ <b>持倉狀態未更新</b>\n"
                                f"{code} 券商已成交，但不在今日持倉庫中，"
                                f"系統將無法監控/強平此部位，請人工確認！",
                            )
                        except Exception:
                            pass
                log.info("DT auto-buy: %s qty=%d price=%.2f", code, result.quantity, result.price)
                _save_dt_buy_trade(result, db_path=db_path, chat_id=TELEGRAM_CHAT_ID or None)
                if TELEGRAM_CHAT_ID:
                    try:
                        from telegram_bot import send_text
                        risk_line = ""
                        if risk_shares > 0 and stop_loss_price is not None:
                            risk_amt = CAPITAL * dt_config.risk_per_trade_pct / 100.0
                            per_share_risk = price - stop_loss_price
                            risk_line = (
                                f"\n風險額 {risk_amt:,.0f} 元"
                                f"（總資金 {dt_config.risk_per_trade_pct:.1f}%）"
                                f"／每股風險 {per_share_risk:,.2f} 元"
                            )
                        send_text(
                            TELEGRAM_CHAT_ID,
                            f"🤖 當沖自動買入：<b>{code} {pos_map[code].name}</b>\n"
                            f"買入價 {result.price:,.2f}　"
                            f"數量 {result.quantity}"
                            f"{'張' if result.lot_type == 'common' else '股'}\n"
                            f"金額 {result.amount:,.0f} 元"
                            f"{risk_line}",
                        )
                    except Exception:
                        pass
            else:
                log.warning("DT auto-buy failed %s: %s", code, result.reason)
        except Exception as e:
            log.warning("DT auto-buy exception %s: %s", code, e)


# ── Paper-trading（紙上追蹤，DT_PAPER_ONLY=true 時啟用）────────────────────────

def _paper_enter_dt_positions(
    api,
    dt_config: "DaytradingConfig",
    dt_path: str = "data/daytrading_positions.json",
) -> None:
    """9:10 紙上進場：把 watching 持倉以當前市價設為 active，不下任何委託。"""
    import paper_trader
    from daytrading_monitor import load_daytrading_positions, save_daytrading_positions

    positions = load_daytrading_positions(path=dt_path)
    watching  = [p for p in positions if p.status == "watching"]
    if not watching:
        log.info("Paper 9:10 進場：無 watching 持倉")
        return

    entered = paper_trader.paper_enter(watching, api=api, quantity=1)
    if not entered:
        log.info("Paper 9:10 進場：全數無法取得報價，跳過")
        return

    save_daytrading_positions(positions, path=dt_path)
    log.info("Paper 9:10 進場 %d 檔（紙上）", len(entered))
    if TELEGRAM_CHAT_ID:
        try:
            from telegram_bot import send_text
            send_text(TELEGRAM_CHAT_ID, paper_trader.build_entry_message(entered))
        except Exception as e:
            log.warning("Paper 進場通知失敗: %s", e)


def _paper_monitor_tick(
    api,
    dt_config: "DaytradingConfig",
    dt_path: str = "data/daytrading_positions.json",
) -> None:
    """每輪呼叫：對 active 紙上持倉跑出場檢查（複用真實出場邏輯）。"""
    import paper_trader
    from daytrading_monitor import load_daytrading_positions, save_daytrading_positions

    positions = load_daytrading_positions(path=dt_path)
    if not any(p.status == "active" for p in positions):
        return

    changed, closed = paper_trader.paper_monitor_pass(positions, api, dt_config)
    if changed:
        save_daytrading_positions(positions, path=dt_path)
    if closed and TELEGRAM_CHAT_ID:
        try:
            from telegram_bot import send_text
            send_text(TELEGRAM_CHAT_ID, paper_trader.build_exit_message(closed))
        except Exception as e:
            log.warning("Paper 出場通知失敗: %s", e)


# ── Day-trading sell-signal executor ─────────────────────────────────────────

def _record_dt_exit(alert, pos, db_path: str = DB_PATH) -> None:
    """輪詢出場成功後寫入 daily_trades 的 sell 記錄（含 pnl）。

    與 monitor_agent.AlertWorker 的 tick 路徑共用 note="auto_exit" 語意，讓
    dt_risk.get_today_dt_realized_pnl（熔斷）與 PostMarketJob 看得到所有當沖
    已實現損益。

    防重複：同一檔同一天 tick 路徑（AlertWorker）與輪詢路徑都可能執行——
    寫入前檢查今日是否已有該 code 的 auto_exit sell 記錄，已存在則跳過。

    pnl 以「股數」計：common 的 quantity 是「張」（1 張 = 1000 股）須乘 1000，
    intraday_odd 的 quantity 是「股」。DB 失敗不得中斷出場流程（log.error）。
    """
    try:
        today = date.today()
        already_recorded = any(
            t.get("code") == pos.code
            and t.get("action") == "sell"
            and t.get("note") == "auto_exit"
            for t in load_daily_trades(today, db_path)
        )
        if already_recorded:
            log.info("DT 出場記錄：%s 今日已有 auto_exit sell（tick 路徑先寫入），跳過", pos.code)
            return
        multiplier = 1000 if pos.lot_type == "common" else 1
        exit_price = alert.price
        if pos.entry_price is not None and exit_price is not None:
            pnl = (exit_price - pos.entry_price) * pos.quantity * multiplier
        else:
            pnl = None
        save_daily_trade({
            "trade_date":  today,
            "code":        pos.code,
            "name":        pos.name,
            "action":      "sell",
            "quantity":    pos.quantity,
            "price":       exit_price,
            "amount":      (exit_price or 0.0) * pos.quantity * multiplier,
            "pnl":         pnl,
            "lot_type":    pos.lot_type,
            "sector":      "當沖",
            "note":        "auto_exit",
            "exit_reason": alert.alert_type,
        }, db_path)
    except Exception as e:
        log.error("DT 出場記錄寫入失敗 %s: %s", pos.code, e)


def _run_dt_sell_alerts(
    api,
    sell_alerts: list,
    dt_config: "DaytradingConfig",
    dt_path: str = "data/daytrading_positions.json",
    db_path: str = DB_PATH,
) -> None:
    """觸發追蹤停利 / 停損 / 強制平倉時，自動執行賣單並更新持倉狀態。

    所有 DT 賣單均自動執行（不需手動確認），確保及時出場。
    `require_manual_confirm` 只管理買入確認，不影響出場。
    賣單成功後寫入 daily_trades 的 sell 記錄（含 pnl，_record_dt_exit）。

    重複下單防護（CAS claim）：下賣單前先原子搶佔 active→closed
    （claim_for_close），沒搶到代表另一條出場路徑（tick AlertWorker /
    收盤強平）已在處理，本路徑跳過；賣單失敗則回滾 active 供下輪重試。
    """
    from daytrading_monitor import (
        load_daytrading_positions,
        claim_for_close,
        revert_to_active,
        record_sell_attempt,
        format_alerts_message,
    )

    positions = load_daytrading_positions(path=dt_path)
    pos_map = {p.code: p for p in positions if p.status == "active"}

    for alert in sell_alerts:
        code = alert.code
        pos = pos_map.get(code)
        if pos is None:
            log.warning("DT sell alert for %s but no active position found", code)
            continue

        if pos.quantity <= 0:
            log.warning("DT sell alert for %s but quantity=0, skipping", code)
            continue

        # CAS：搶不到 = 另一路徑已處理，絕不下第二筆賣單
        if not claim_for_close(code, path=dt_path):
            log.info("DT 出場：%s 已由其他路徑 claim，跳過（避免重複下單）", code)
            continue

        success = force_stop_loss(
            api=api,
            code=pos.code,
            name=pos.name,
            quantity=pos.quantity,
            lot_type=pos.lot_type,
            paper_trading=PAPER_TRADING,
        )
        if success:
            # claim 時已原子轉 closed，這裡不需再標記
            log.info(
                "DT 出場：%s %s reason=%s price=%.2f",
                code, pos.name, alert.alert_type, alert.price,
            )
            # 出場已實現損益寫入 daily_trades（熔斷 / PostMarketJob 依據）
            _record_dt_exit(alert, pos, db_path=db_path)
            if TELEGRAM_CHAT_ID:
                try:
                    from telegram_bot import send_text
                    send_text(
                        TELEGRAM_CHAT_ID,
                        f"✅ 出場已送出\n{format_alerts_message([alert])}",
                    )
                except Exception as e:
                    log.warning("DT sell notify failed: %s", e)
        else:
            # 賣單失敗：回滾 claim（closed→active）＋記錄重試次數，
            # 下一輪同規則會再次觸發賣單重試。
            revert_to_active(code, path=dt_path)
            attempts = record_sell_attempt(
                code, f"force_stop_loss returned False ({alert.alert_type})",
                path=dt_path,
            )
            log.error("DT 出場失敗（第 %d 次）：%s %s", attempts, code, pos.name)
            # 告警節流：第 1–2 次發重試訊息；第 3 次發一次升級告警；
            # 之後只記 log（避免券商長時間中斷時每 5 分鐘轟炸同一則告警）。
            if TELEGRAM_CHAT_ID and attempts <= 3:
                try:
                    from telegram_bot import send_text
                    if attempts == 3:
                        # 連續失敗達門檻 → 升級人工介入告警（只發這一次）
                        send_text(
                            TELEGRAM_CHAT_ID,
                            f"🚨 <b>當沖出場連續失敗 {attempts} 次，需人工介入</b>\n"
                            f"{pos.code} {pos.name}　數量 {pos.quantity}"
                            f"{'張' if pos.lot_type == 'common' else '股'}\n"
                            f"原因：{alert.alert_type}\n"
                            f"請立即手動平倉！（後續重試只記 log，不再重發此告警）",
                        )
                    else:
                        send_text(
                            TELEGRAM_CHAT_ID,
                            f"❌ 出場失敗（第 {attempts} 次重試中）\n"
                            f"{format_alerts_message([alert])}",
                        )
                except Exception as e:
                    log.warning("DT sell notify failed: %s", e)


# ── DT Opening Confirmation (9:05) ───────────────────────────────────────────

def _opening_confirm_dt_positions(
    api,
    dt_config: "DaytradingConfig",
    dt_path: str = "data/daytrading_positions.json",
) -> None:
    """9:05 開盤再確認。

    llm_mode="decider"（預設）：AI 結合 8:30 預測 + 當前大盤氣氛，給出進場或
    放棄建議（現狀行為，零回歸）。
    llm_mode="advisor"：改由 dt_rules.rule_opening_reconfirm 的確定性規則決定
    proceed，省略本次 LLM 呼叫（省成本），Telegram 訊息標明「規則決策」。
    兩種 mode 都會把決策落庫到 ai_decision_log（stage="reconfirm"），供
    dt_counterfactual.py 之類的反事實分析工具比較「規則 vs LLM」。
    """
    import dt_rules
    from daytrading_monitor import (
        load_daytrading_positions, mark_skipped, update_entry_range,
    )
    from monitor_agent import get_snapshot
    from daytrading_analyzer import run_opening_reconfirm, OpeningReconfirm

    llm_mode = getattr(dt_config, "llm_mode", "decider")

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

    for pos in watching:
        snap = get_snapshot(api, pos.code) if api is not None else None
        snap_cache[pos.code] = snap or {}

        current_price = snap["close"]        if snap else 0.0
        change_price  = snap.get("change_price", 0.0) if snap else 0.0
        volume        = snap.get("volume",       0)   if snap else 0

        # 規則決策一律算出來（兩種 mode 都要），供落庫做「規則 vs LLM」反事實比較。
        rule_result = dt_rules.rule_opening_reconfirm(
            pos, current_price, change_price, volume, market,
        )

        capture: dict = {}
        if llm_mode == "advisor":
            result = OpeningReconfirm(
                code=pos.code, name=pos.name,
                proceed=rule_result.proceed,
                reason=f"[規則決策] {rule_result.reason}",
                updated_entry_low=None, updated_entry_high=None,
            )
        else:
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
                capture=capture,
            )

        # AI 決策全落庫（task 4）：不論 llm_mode、成功與否都要記錄，失敗不得
        # 影響交易流程。
        try:
            from daytrading_db import DaytradingDB
            now = datetime.now()
            DaytradingDB().log_ai_decision(
                date=date.today().isoformat(),
                time=now.strftime("%H:%M"),
                code=pos.code,
                stage="reconfirm",
                llm_mode=llm_mode,
                dt_score=pos.dt_score,
                prompt=capture.get("prompt"),
                raw_response=capture.get("raw"),
                parsed_action=capture.get("parsed_action"),
                rule_action="proceed" if rule_result.proceed else "skip",
                final_action="proceed" if result.proceed else "skip",
                features={
                    "current_price": current_price,
                    "change_price": change_price,
                    "volume": volume,
                    "market": market,
                    "entry_low": pos.entry_low,
                    "entry_high": pos.entry_high,
                },
            )
        except Exception as e:
            log.debug("log_ai_decision(reconfirm, %s) failed: %s", pos.code, e)

        if result.proceed:
            # AI 可能調整進場區間 → 原子單欄更新（不 bulk 回存整列快照，
            # 避免覆蓋並發的 status/sell_attempts 原子變更）
            pos_changed = False
            if result.updated_entry_low is not None:
                pos.entry_low  = result.updated_entry_low
                pos_changed = True
            if result.updated_entry_high is not None:
                pos.entry_high = result.updated_entry_high
                pos_changed = True
            if pos_changed:
                update_entry_range(pos.code, pos.entry_low, pos.entry_high,
                                   path=dt_path)
            confirmed.append(pos)
            log.info("DT 9:05 確認進場 %s %s: %s", pos.code, pos.name, result.reason)
        else:
            # 原子單筆放棄標記（不覆蓋其他持倉；跨 process 安全）
            mark_skipped(pos.code, path=dt_path)
            skipped.append(pos)
            reasons[pos.code] = result.reason
            log.info("DT 9:05 放棄 %s %s: %s", pos.code, pos.name, result.reason)

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
        ]
        if llm_mode == "advisor":
            lines.append("🤖 <i>本次為規則決策（advisor 模式，LLM 僅供評論）</i>")
        lines.append("━━━━━━━━━━━━━━━━")

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
        import config as _cfg_fc
        lines.append(f"<i>⚠️ {_cfg_fc.FORCE_CLOSE_TIME} 系統將自動強制平倉</i>")

    try:
        from telegram_bot import send_text
        send_text(TELEGRAM_CHAT_ID, "\n".join(lines))
    except Exception as e:
        log.warning("DT 13:00 出場警報失敗: %s", e)

    log.info("DT 出場警報已送出，%d 個 active 持倉", len(active))


# ── Main loop ─────────────────────────────────────────────────────────────────

_RUNNING = True

from datetime import time as _dtime, timedelta as _timedelta

# 收盤強制平倉時刻：config.FORCE_CLOSE_TIME（預設 13:15）。
# 必須落在連續交易時段（09:00–13:24:59）內——13:25 起為收盤集合競價，
# 市價單會被交易所退單，強平會靜默失敗。
def _parse_force_close_time() -> _dtime:
    import config as _cfg
    try:
        h, m = map(int, _cfg.FORCE_CLOSE_TIME.split(":"))
        t = _dtime(h, m)
        if t >= _dtime(13, 25):
            log.warning(
                "FORCE_CLOSE_TIME=%s 落在收盤集合競價（市價單會被退單），"
                "改用 13:15", _cfg.FORCE_CLOSE_TIME,
            )
            return _dtime(13, 15)
        return t
    except Exception as e:
        log.warning("FORCE_CLOSE_TIME 解析失敗（%s），改用 13:15", e)
        return _dtime(13, 15)


_FORCE_CLOSE_T = _parse_force_close_time()

# tick 訂閱到強平時刻結束（強平後由 claim 機制保證不重複下單）
_DT_MON_END = _FORCE_CLOSE_T

# 5 分鐘輪詢出場路徑（非紙上模式）：09:15 起、強平前 1 分鐘結束（避免重疊）
_DT_POLL_START    = _dtime(9, 15)
_DT_POLL_END      = (
    datetime.combine(date.min, _FORCE_CLOSE_T) - _timedelta(minutes=1)
).time()
_DT_POLL_INTERVAL = 300   # 秒（5 分鐘節流）


def _build_dt_watchlist(positions: list) -> list[dict]:
    """把 DaytradingPosition 清單轉成 MonitorAgent.set_watchlist 需要的 dict 清單。

    只保留 watching / active 狀態（closed / skipped 不監控）。
    """
    return [
        {
            "code":            p.code,
            "name":            p.name,
            "target_price":    p.target_price,
            "stop_loss_price": p.stop_loss,
            "entry_price":     p.entry_price,
            "quantity":        p.quantity,
            "lot_type":        p.lot_type,
        }
        for p in positions if p.status in ("watching", "active")
    ]


def _refresh_dt_watchlist(dt_agent, dt_path: str = "data/daytrading_positions.json") -> list[dict]:
    """從 JSON 重新載入持倉並更新 tick 監控 agent 的 watchlist。

    解決手動確認模式下 09:10 快照過期（entry_price=None/quantity=0）問題：
    Telegram process 成交後改寫 JSON，主迴圈輪詢時刷新即可帶入實際進場價/數量。
    set_watchlist 內部會重建 dict 並自 sidecar seed peaks（peak 持久化不受影響）。
    """
    from daytrading_monitor import load_daytrading_positions
    positions = load_daytrading_positions(path=dt_path)
    watchlist = _build_dt_watchlist(positions)
    if watchlist:
        dt_agent.set_watchlist(watchlist)
    return watchlist


def _maybe_trigger_circuit_breaker(
    api,
    dt_config: "DaytradingConfig",
    dt_path: str = "data/daytrading_positions.json",
    db_path: str = DB_PATH,
) -> None:
    """當日虧損熔斷檢查：一天只觸發一次（旗標已存在時直接略過）。

    觸發時：
      1. 對所有 active 當沖持倉構造 force_close 類型警報，複用
         _run_dt_sell_alerts 既有機制全平倉（成功者由其內部 mark_closed）。
      2. 寫入當日熔斷旗標（dt_risk.set_circuit_breaker_flag）—— 讓
         _auto_buy_dt_positions / telegram_bot._handle_dt_buy 之後拒絕新買單。
      3. 發送 Telegram 告警。

    旗標先寫入再嘗試全平倉，確保即使全平倉過程拋例外，「本日不再觸發 / 停止
    進場」的效果仍然生效（force_stop_loss 失敗時既有的 5 分鐘輪詢出場路徑仍會
    持續嘗試出場，見 _run_dt_sell_alerts 的重試機制）。
    """
    import dt_risk
    from daytrading_monitor import load_daytrading_positions, DaytradingAlert

    if dt_risk.is_circuit_breaker_active():
        return

    result = dt_risk.check_circuit_breaker(dt_config, db_path)
    if not result.triggered:
        return

    log.error("DT 熔斷觸發：%s", result.message)
    dt_risk.set_circuit_breaker_flag(result.realized_pnl, result.message)

    positions = [p for p in load_daytrading_positions(path=dt_path) if p.status == "active"]
    if positions:
        force_alerts = [
            DaytradingAlert(
                code=p.code, name=p.name, alert_type="force_close",
                price=p.entry_price or 0.0,
                message=f"熔斷全平倉：{result.message}",
                time=datetime.now(), sell_required=True,
            )
            for p in positions
        ]
        _run_dt_sell_alerts(api, force_alerts, dt_config, dt_path=dt_path, db_path=db_path)

    if TELEGRAM_CHAT_ID:
        try:
            from telegram_bot import send_text
            send_text(
                TELEGRAM_CHAT_ID,
                f"🚨 <b>當日虧損熔斷觸發</b>\n{result.message}\n"
                f"已對所有當沖持倉送出強制平倉，本日不再新進場。",
            )
        except Exception as e:
            log.warning("熔斷告警發送失敗: %s", e)


def _dt_poll_tick(
    api,
    dt_config: "DaytradingConfig",
    dt_agent=None,
    dt_path: str = "data/daytrading_positions.json",
    db_path: str = DB_PATH,
) -> list:
    """單次輪詢：熔斷檢查 + 刷新 tick watchlist（若 agent 存在）+ 跑 5 分鐘輪詢出場掃描。

    回傳本次已執行的 sell 警報清單（供測試斷言）。
    """
    from daytrading_monitor import run_daytrading_monitor

    # 當日虧損熔斷檢查（一天只觸發一次）；失敗不得阻擋既有輪詢出場路徑。
    try:
        _maybe_trigger_circuit_breaker(api, dt_config, dt_path=dt_path, db_path=db_path)
    except Exception as e:
        log.warning("DT 熔斷檢查失敗: %s", e)

    # Task 3：刷新 tick 監控 watchlist（帶入 Telegram process 成交後的實際持倉）
    if dt_agent is not None:
        try:
            _refresh_dt_watchlist(dt_agent, dt_path=dt_path)
        except Exception as e:
            log.warning("DT watchlist 刷新失敗: %s", e)

    # Task 1：5 分鐘輪詢出場路徑（每次都從 JSON 重新載入，天然跨 process 同步）
    alerts = run_daytrading_monitor(api=api, path=dt_path, config=dt_config)
    sell_alerts = [a for a in alerts if getattr(a, "sell_required", False)]
    if sell_alerts:
        _run_dt_sell_alerts(api, sell_alerts, dt_config, dt_path=dt_path, db_path=db_path)
    return sell_alerts


def _maybe_dt_poll(
    now: datetime,
    api,
    dt_config: "DaytradingConfig",
    state: dict,
    dt_agent=None,
    dt_path: str = "data/daytrading_positions.json",
    db_path: str = DB_PATH,
):
    """非紙上模式的 5 分鐘節流輪詢包裝。

    state = {"last_poll": datetime | None}（呼叫端持有，跨迴圈保存）。
    - 紙上模式：不走此路徑（回 None）
    - 交易時段外（非 09:15–13:30）：不執行（回 None）
    - 距上次執行未滿 5 分鐘：跳過（回 None）
    否則更新 state["last_poll"] 並執行 _dt_poll_tick，回傳已執行的 sell 警報清單。
    tick watchlist 刷新只在 13:15 前（tick 訂閱時段）進行。
    """
    if dt_config.paper_trade_only:
        return None
    t = now.time()
    if not (_DT_POLL_START <= t <= _DT_POLL_END):
        return None
    last = state.get("last_poll")
    if last is not None and (now - last).total_seconds() < _DT_POLL_INTERVAL:
        return None
    state["last_poll"] = now
    return _dt_poll_tick(
        api, dt_config,
        dt_agent=(dt_agent if t <= _DT_MON_END else None),
        dt_path=dt_path,
        db_path=db_path,
    )


def _run_dt_reconcile(api, dt_config: "DaytradingConfig", tag: str) -> None:
    """券商對帳（10:00 / 13:20）：比對 DB active 持倉與券商實際持倉，有差異發告警。

    只在「非紙上、非模擬」且有 api 時執行。對帳失敗（券商查詢例外）不告警，只 log。
    """
    if dt_config.paper_trade_only or SIMULATION or api is None:
        return
    try:
        import dt_position_store
        report = dt_position_store.reconcile_with_broker(
            api, chat_id=(TELEGRAM_CHAT_ID or None),
        )
        if report is None:
            log.warning("DT 對帳（%s）：無法取得券商持倉，略過", tag)
        else:
            log.info(
                "DT 對帳（%s）：matched=%d db_only=%d qty_mismatch=%d broker_only=%d",
                tag, len(report["matched"]), len(report["db_only"]),
                len(report["qty_mismatch"]), len(report["broker_only"]),
            )
    except Exception as e:
        log.warning("DT 對帳（%s）失敗: %s", tag, e)


def _halt_notice_lines() -> list[str]:
    """HALT 狀態的說明文字。集中一處，避免各處講法不一致。"""
    return [
        "⚠️ <b>系統處於緊急暫停狀態</b>",
        "今日<b>不會執行任何買進</b>。",
        "停損、停利、強制平倉<b>照常運作</b>，持倉仍受保護。",
        "傳 <code>恢復系統</code> 可解除。",
    ]


def _announce_halt_state_on_startup() -> None:
    """啟動時若處於 HALT，大聲宣告。

    2026-08-21 有人按了緊急暫停，接下來 12 天系統每天靜靜跳過所有工作——
    不寫 log、不告警、不過期。process 活著、log 乾淨、Telegram 無異常，
    完全沒有任何跡象。任何會改變系統行為的持久狀態，都必須自我宣告。
    """
    try:
        from halt import is_halted
        if not is_halted():
            return
    except Exception as e:
        log.warning("啟動時讀取 HALT 狀態失敗: %s", e)
        return

    log.warning("啟動時偵測到 HALT 旗標：今日不會執行買進（賣出與平倉正常）")
    if not TELEGRAM_CHAT_ID:
        return
    try:
        from telegram_bot import send_text
        send_text(TELEGRAM_CHAT_ID, "\n".join(_halt_notice_lines()))
    except Exception as e:
        log.warning("HALT 啟動告警發送失敗: %s", e)


def _maybe_halt_reminder(now: datetime, state: dict) -> None:
    """HALT 期間每小時提醒一次（不洗版）。state 由呼叫端跨迴圈保存。"""
    try:
        from halt import is_halted
        if not is_halted():
            state.pop("last_halt_reminder", None)
            return
    except Exception:
        return

    slot = now.strftime("%Y-%m-%d %H")
    if state.get("last_halt_reminder") == slot:
        return
    state["last_halt_reminder"] = slot

    log.warning("系統仍處於緊急暫停狀態（不執行買進）")
    if not TELEGRAM_CHAT_ID:
        return
    try:
        from telegram_bot import send_text
        send_text(TELEGRAM_CHAT_ID, "\n".join(_halt_notice_lines()))
    except Exception as e:
        log.warning("HALT 定期提醒發送失敗: %s", e)


def _run_force_close_job(api, db_path: str = DB_PATH, chat_id: Optional[str] = None) -> None:
    """收盤強制平倉（預設 13:15），包 try/except。強平失敗是資金安全事件 → 另發 Telegram 告警。"""
    try:
        ForceCloseJob(api=api, db_path=db_path).run()
    except Exception as e:
        log.error("ForceCloseJob 失敗: %s", e)
        if chat_id:
            try:
                from telegram_bot import send_text
                send_text(
                    chat_id,
                    f"🚨 <b>{_FORCE_CLOSE_T.strftime('%H:%M')} 強制平倉失敗</b>\n{e}\n請立即手動檢查並平倉所有當沖持倉！",
                )
            except Exception as te:
                log.warning("ForceClose 告警發送失敗: %s", te)


def _run_postmarket_job(monitor, db_path: str, execution_id: str):
    """13:35 收盤結算，包 try/except。回傳 total_pnl，失敗回 None。"""
    try:
        from notifier import notify_market_close
        job = PostMarketJob(monitor=monitor, db_path=db_path, execution_id=execution_id)
        total_pnl = job.run(trades_summary="自動收盤")
        trades = load_daily_trades(date.today(), db_path)
        notify_market_close(total_pnl=total_pnl, trade_count=len(trades))
        return total_pnl
    except Exception as e:
        log.warning("PostMarketJob 失敗: %s", e)
        return None


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

    try:
        import preflight
        preflight.run_preflight()
    except Exception as e:
        log.warning("preflight: 啟動前檢查失敗（已忽略）：%s", e)

    from notifier import notify_system_start, notify_system_stop, notify_market_open, notify_market_close
    notify_system_start(SIMULATION)
    _announce_halt_state_on_startup()

    api = ensure_connected(
        os.getenv("SHIOAJI_API_KEY", ""),
        os.getenv("SHIOAJI_SECRET_KEY", ""),
        simulation=SIMULATION,
    )

    # 把這條連線註冊為全專案共用。yfinance 全面換成 Shioaji 之後，十幾個
    # 模組都需要 api；不註冊的話它們會各自 login，開出多條 session（券商有
    # 連線數上限，且每次登入要數秒）。
    try:
        import shioaji_session
        shioaji_session.set_api(api)
    except Exception as e:
        log.warning("註冊共用 Shioaji session 失敗（不影響主流程）: %s", e)

    dt_config = load_daytrading_config()
    log.info(
        "DT config: budget=%.0f stop_loss=%.1f%% take_profit=%.1f%% "
        "trailing_start=%.1f%% trailing_gap=%.1f%% force_close=%s manual_confirm=%s "
        "paper_trade_only=%s",
        dt_config.budget_per_stock,
        dt_config.stop_loss_pct,
        dt_config.take_profit_pct,
        dt_config.trailing_start_pct,
        dt_config.trailing_gap_pct,
        dt_config.force_close_time,
        dt_config.require_manual_confirm,
        dt_config.paper_trade_only,
    )

    monitor: Optional[MonitorAgent] = None
    approved_picks: list[dict] = []
    _dt_agent = None   # MonitorAgent（tick 訂閱，09:05 下單後啟動）
    _fired_today: set[str] = set()  # 防止同一 job 在同一天重複執行
    _dt_poll_state: dict = {"last_poll": None}  # 5 分鐘輪詢出場路徑節流狀態

    while _RUNNING:
        now = datetime.now()
        if not is_trading_day(now):
            time.sleep(60)
            continue

        t = now.time()
        today_prefix = now.strftime("%Y-%m-%d")

        # 註：這裡曾經有 `if is_halted(): sleep; continue` 的全域擋。
        # 已移除——它會連 5 分鐘出場輪詢、13:15 強制平倉、13:35 複盤一起跳過，
        # 等於「按下緊急暫停就鎖住逃生門」。HALT 現在是禁買閘門，實作在
        # executor.place_stock_order 的 Guard 0，只擋 action=="buy"。
        # 詳見 tests/test_halt_semantics.py。

        # 跨日清除已執行紀錄
        _fired_today = {k for k in _fired_today if k.startswith(today_prefix)}

        # HALT 期間每小時提醒一次。不會 continue——HALT 只擋買進，
        # 其餘 job（監控、出場、強平、複盤）照常執行。
        try:
            _maybe_halt_reminder(now, _dt_poll_state)
        except Exception as e:
            log.debug("HALT 提醒失敗（忽略）: %s", e)

        # ── 13:15 停止 tick 訂閱 ────────────────────────────────────────
        if t >= _DT_MON_END and _dt_agent is not None:
            _dt_agent.stop()
            _dt_agent = None
            log.info("DT tick monitor stopped at 13:15")

        # ── 紙上追蹤監控（每輪檢查；DT_PAPER_ONLY 關閉時零行為變更）──────
        if dt_config.paper_trade_only:
            try:
                _paper_monitor_tick(api, dt_config)
            except Exception as e:
                log.warning("Paper monitor tick 失敗: %s", e)
        else:
            # 非紙上模式：09:15–13:30 每 5 分鐘輪詢一次出場路徑（run_daytrading_monitor）
            # + 刷新 tick 監控 watchlist。每次都從 JSON 重新載入，天然跨 process 同步。
            try:
                _maybe_dt_poll(now, api, dt_config, _dt_poll_state, dt_agent=_dt_agent)
            except Exception as e:
                log.warning("DT 輪詢監控失敗: %s", e)

        # 08:30 pre-market：波段選股 + 當沖預測報告（同時推播 Telegram）
        if t.hour == 8 and t.minute == 30 and f"{today_prefix}-0830" not in _fired_today:
            # 先確認 Shioaji 行情就緒再選股。login 成功 ≠ 報價 session 就緒，
            # 8:30 常剛登入沒暖機；真實模式全市場掃描（batch_fetch_snapshots）
            # 無外部備援，報價未就緒就抓到空清單 → 零候選。就緒探測失敗
            # 不阻擋執行（降級續跑，行為不比修復前差），但對外告警。
            if api is not None:
                try:
                    from monitor_agent import wait_for_market_data
                    if not wait_for_market_data(api):
                        log.warning("8:30 選股：Shioaji 行情未就緒，仍嘗試執行（候選可能偏少）")
                        if TELEGRAM_CHAT_ID:
                            try:
                                from telegram_bot import send_text
                                send_text(
                                    TELEGRAM_CHAT_ID,
                                    "⚠️ 8:30 選股：Shioaji 行情未就緒（報價暖機逾時），"
                                    "今日候選可能偏少，請留意。",
                                )
                            except Exception:
                                pass
                except Exception as e:
                    log.warning("行情就緒探測失敗（忽略，續跑）: %s", e)

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
                # 這裡原本只 log.warning，導致 build_daytrading_report 的
                # TypeError（None >= float）靜默失敗數週：當沖預測一筆都沒
                # 產生、dt_prediction_log 全空，但沒有任何人知道。
                # 當沖預測失敗＝當日整套當沖停擺，必須立刻讓人看見。
                log.error("當沖預測產生失敗（今日無候選）: %s", e, exc_info=True)
                if TELEGRAM_CHAT_ID:
                    try:
                        from telegram_bot import send_text
                        send_text(
                            TELEGRAM_CHAT_ID,
                            f"🚨 <b>當沖預測產生失敗</b>\n"
                            f"今日不會有任何當沖候選，請檢查 logs/main.log。\n"
                            f"錯誤：{e}",
                        )
                    except Exception:
                        pass

            _fired_today.add(f"{today_prefix}-0830")
            time.sleep(60)

        # 09:00 market open
        elif t.hour == 9 and t.minute == 0 and f"{today_prefix}-0900" not in _fired_today:
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
            _fired_today.add(f"{today_prefix}-0900")
            time.sleep(60)

        # 09:05 開盤確認：量能/方向 OK → 保留 watching；不符合 → skipped
        elif t.hour == 9 and t.minute == 5 and f"{today_prefix}-0905" not in _fired_today:
            # 先傳 Shioaji 連線狀態
            try:
                if TELEGRAM_CHAT_ID:
                    from telegram_bot import send_text
                    sj_ok = api is not None
                    sj_mode = "模擬" if SIMULATION else "真實"
                    sj_status = f"✅ 已連線（{sj_mode}模式）" if sj_ok else "❌ 未連線（報價可能為 0.0）"
                    send_text(TELEGRAM_CHAT_ID,
                        f"🔌 <b>Shioaji 連線狀態</b>\n{sj_status}\n"
                        f"SHIOAJI_SIMULATION={SIMULATION}\nPAPER_TRADING={PAPER_TRADING}")
            except Exception as e:
                log.warning("Shioaji 狀態通知失敗: %s", e)

            try:
                _opening_confirm_dt_positions(api, dt_config)
            except Exception as e:
                log.warning("DT 開盤確認失敗: %s", e)
            _fired_today.add(f"{today_prefix}-0905")
            time.sleep(60)

        # 09:10 當沖進場（第一波）+ 啟動 tick 監控
        elif t.hour == 9 and t.minute == 10:
            if dt_config.paper_trade_only:
                # 紙上模式：不下任何委託，只以市價紙上進場並交由 _paper_monitor_tick 追蹤
                try:
                    _paper_enter_dt_positions(api, dt_config)
                except Exception as e:
                    log.warning("Paper 9:10 進場失敗: %s", e)
            else:
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
                    watchlist = _build_dt_watchlist(positions)
                    if watchlist and api is not None:
                        _dt_agent = MonitorAgent(
                            api_key="", secret_key="", simulation=False,
                            db_path=DB_PATH, telegram_chat_id=TELEGRAM_CHAT_ID,
                            api=api,
                            trailing_start_pct=dt_config.trailing_start_pct,
                            trailing_gap_pct=dt_config.trailing_gap_pct,
                            auto_execute=True,
                        )
                        _dt_agent.set_watchlist(watchlist)
                        _dt_agent.start()
                        log.info("DT tick 監控啟動，%d 個持倉（auto_execute=True）", len(watchlist))
                except Exception as e:
                    log.warning("DT tick 監控啟動失敗: %s", e)
            time.sleep(60)

        # 10:00 盤中檢查：持倉損益 + 剩餘候選（可選擇二次進場）
        elif t.hour == 10 and t.minute == 0:
            try:
                _mid_session_check(api)
            except Exception as e:
                log.warning("DT 盤中檢查失敗: %s", e)
            # 10:00 券商對帳（非紙上、非模擬）
            if f"{today_prefix}-1000-reconcile" not in _fired_today:
                _run_dt_reconcile(api, dt_config, "10:00")
                _fired_today.add(f"{today_prefix}-1000-reconcile")
            time.sleep(60)

        # 13:20 券商對帳（強平前最後一次核對持倉一致性）
        elif t.hour == 13 and t.minute == 20 and f"{today_prefix}-1320-reconcile" not in _fired_today:
            _run_dt_reconcile(api, dt_config, "13:20")
            _fired_today.add(f"{today_prefix}-1320-reconcile")
            time.sleep(60)

        # 13:00 出場警報：30 分鐘倒數提醒
        elif t.hour == 13 and t.minute == 0:
            try:
                _exit_warning(api)
            except Exception as e:
                log.warning("DT 出場警報失敗: %s", e)
            time.sleep(60)

        # 收盤強制平倉（config.FORCE_CLOSE_TIME，預設 13:15——必須在連續
        # 交易時段內，13:25 起的集合競價不收市價單）
        elif t.hour == _FORCE_CLOSE_T.hour and t.minute == _FORCE_CLOSE_T.minute:
            if api:
                _run_force_close_job(api, DB_PATH, TELEGRAM_CHAT_ID or None)
            time.sleep(60)

        # 13:35 post-market
        elif t.hour == 13 and t.minute == 35:
            # 紙上模式：先把仍 active 的持倉強制平倉一次，再結算當日損益
            if dt_config.paper_trade_only:
                try:
                    _paper_monitor_tick(api, dt_config)
                    from paper_trader import build_paper_summary
                    summary = build_paper_summary()
                    if summary and TELEGRAM_CHAT_ID:
                        from telegram_bot import send_text
                        send_text(TELEGRAM_CHAT_ID, summary)
                except Exception as e:
                    log.warning("Paper 收盤結算失敗: %s", e)

            _run_postmarket_job(
                monitor, DB_PATH, f"main-{now.date().isoformat()}",
            )
            monitor = None
            approved_picks = []

            # 當沖預測複盤：回填今日 OHLC + 判斷是否預測正確
            try:
                from daytrading_review import run_daytrading_review
                review_msg = run_daytrading_review()
                if review_msg and TELEGRAM_CHAT_ID:
                    from telegram_bot import send_text
                    send_text(TELEGRAM_CHAT_ID, review_msg)
            except Exception as e:
                log.warning("daytrading_review failed: %s", e)

            # 老薑收盤檢討：分析錯誤原因 + 生成 Gemini 明日補充請求
            try:
                from super_trader import run_postmarket_review
                postmarket_msg = run_postmarket_review()
                if postmarket_msg and TELEGRAM_CHAT_ID:
                    from telegram_bot import send_text
                    send_text(TELEGRAM_CHAT_ID, postmarket_msg)
            except Exception as e:
                log.warning("postmarket_review failed: %s", e)

            time.sleep(60)

        # 13:50 playbook update（PostMarketJob 之後）
        elif t.hour == 13 and t.minute == 50 and f"{today_prefix}-1350" not in _fired_today:
            PlaybookUpdateJob().run()
            _fired_today.add(f"{today_prefix}-1350")
            time.sleep(60)

        else:
            time.sleep(30)

    if monitor:
        monitor.stop()
    notify_system_stop()
    log.info("main.py stopped")


if __name__ == "__main__":
    main()
