from __future__ import annotations

"""
Main scheduler — three daily jobs:
  08:30  PremarketJob  : AI analysis + risk filter + Telegram confirmation
  09:00  MarketOpenJob : execute orders + start MonitorAgent
  13:35  PostMarketJob : stop monitor + save daily summary

Run: python3 main.py
"""

import os
import signal
import time
from datetime import datetime, date
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from deep_analyzer import run_deep_analysis
from executor import place_stock_order, ExecutionResult, force_stop_loss
from logger import get_logger
from market_scan import batch_fetch_snapshots
from market_scanner import ScanCriteria, get_all_stock_codes, screen_candidates
from monitor_agent import MonitorAgent, ensure_connected
from research_db import (
    init_db, save_daily_plan, load_daily_plan, save_daily_trade,
    save_daily_summary, DailySummaryRow, load_daily_trades,
)
from risk_guard import validate_plan
from user_confirm import send_confirmation

log = get_logger(__name__)

DB_PATH          = os.getenv("DB_PATH", "data/research.db")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CAPITAL          = float(os.getenv("BUDGET", "100000"))
HARD_LIMIT       = float(os.getenv("ORDER_HARD_LIMIT", "150000"))
SIMULATION       = os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"


# ── Pure helpers ──────────────────────────────────────────────────────────────

def is_trading_day(dt: datetime) -> bool:
    """Return True for Mon–Fri (weekday)."""
    return dt.weekday() < 5


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
    Returns [] when api is None or on any error."""
    if api is None:
        return []
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
        log.warning("scan_candidates failed: %s", e)
        return []


def load_prior_orders(api) -> list[dict]:
    """Return today's already-placed orders from Shioaji as plain dicts.
    Used to prevent duplicate orders at 09:00."""
    if api is None:
        return []
    try:
        trades = api.list_trades()
        return [
            {
                "code":     t.contract.code,
                "action":   str(t.order.action),
                "quantity": t.order.quantity,
                "price":    float(t.order.price),
            }
            for t in (trades or [])
        ]
    except Exception as e:
        log.warning("load_prior_orders failed: %s", e)
        return []


def load_current_positions(trade_date: date, db_path: str) -> list[dict]:
    """Return today's executed buy trades from DB as position dicts.
    Used by PremarketJob to pass current exposure to risk_guard."""
    try:
        trades = load_daily_trades(trade_date, db_path)
        return [t for t in trades if t.get("action") == "buy"]
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

    def run(self) -> MonitorAgent:
        # Always read from DB — user may have rejected picks via Telegram between
        # 08:30 (premarket) and 09:00 (open), so in-memory list may be stale.
        try:
            picks_to_execute = load_daily_plan(date.today(), self._db_path) or self._picks
        except Exception:
            picks_to_execute = self._picks

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
                        "note":       f"id={result.order_id} lot={result.lot_type}",
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

    def run(self, total_pnl: float, trades_summary: str) -> None:
        if self._monitor is not None:
            self._monitor.stop()

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
        results = []
        for pos in positions:
            code     = pos["code"]
            name     = pos.get("name", code)
            quantity = pos.get("quantity", 0)
            success  = force_stop_loss(
                api=self._api,
                code=code,
                name=name,
                quantity=quantity,
            )
            results.append({"code": code, "success": success})
            if success:
                log.info("ForceClose: sold %s qty=%d", code, quantity)
            else:
                log.error("ForceClose: failed to sell %s", code)
        return results


# ── Main loop ─────────────────────────────────────────────────────────────────

_RUNNING = True


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

    monitor: Optional[MonitorAgent] = None
    approved_picks: list[dict] = []

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

        # 08:30 pre-market
        if t.hour == 8 and t.minute == 30:
            job = PremarketJob(
                candidates=None,
                capital=CAPITAL,
                db_path=DB_PATH,
                telegram_chat_id=TELEGRAM_CHAT_ID or None,
                current_positions=None,
                api=api,
            )
            approved_picks = job.run()
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

        # 13:25 force-close all positions before market close
        elif t.hour == 13 and t.minute == 25:
            if api:
                ForceCloseJob(api=api, db_path=DB_PATH).run()
            time.sleep(60)

        # 13:35 post-market
        elif t.hour == 13 and t.minute == 35:
            from research_db import load_daily_trades
            trades = load_daily_trades(date.today(), DB_PATH)
            total_pnl = sum(t.get("pnl") or 0 for t in trades)
            job = PostMarketJob(
                monitor=monitor,
                db_path=DB_PATH,
                execution_id=f"main-{now.date().isoformat()}",
            )
            job.run(total_pnl=total_pnl, trades_summary="自動收盤")
            notify_market_close(total_pnl=total_pnl, trade_count=len(trades))
            monitor = None
            approved_picks = []
            time.sleep(60)

        else:
            time.sleep(30)

    if monitor:
        monitor.stop()
    notify_system_stop()
    log.info("main.py stopped")


if __name__ == "__main__":
    main()
