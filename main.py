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
from executor import place_stock_order, ExecutionResult
from logger import get_logger
from monitor_agent import MonitorAgent, ensure_connected
from research_db import (
    init_db, save_daily_plan, save_daily_trade,
    save_daily_summary, DailySummaryRow,
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
        candidates: list[dict],
        capital: float,
        db_path: str,
        telegram_chat_id: Optional[str],
        current_positions: list[dict],
    ) -> None:
        self._candidates       = candidates
        self._capital          = capital
        self._db_path          = db_path
        self._telegram_chat_id = telegram_chat_id
        self._current_positions = current_positions

    def run(self, market_summary: str = "", theme_info: str = "") -> list[dict]:
        buy_picks: list[dict] = []

        for cand in self._candidates:
            code = cand["code"]
            name = cand.get("name", code)
            try:
                analysis = run_deep_analysis(
                    code=code, name=name, news=[],
                    fundamentals_text="", market_summary=market_summary,
                    theme_info=theme_info,
                )
                if analysis.signal != "buy":
                    continue
                buy_picks.append({
                    "code":             code,
                    "name":             name,
                    "budget":           self._capital * 0.05,
                    "sector":           cand.get("sector", "未知"),
                    "signal":           analysis.signal,
                    "confidence":       analysis.confidence,
                    "target_price":     analysis.target_price,
                    "stop_loss_price":  analysis.stop_loss_price,
                })
            except Exception as e:
                log.warning("PremarketJob analysis failed for %s: %s", code, e)

        result = validate_plan(buy_picks, self._capital, self._current_positions)
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
        prior_orders: list[dict],
    ) -> None:
        self._api             = api
        self._picks           = approved_picks
        self._db_path         = db_path
        self._telegram_chat_id = telegram_chat_id
        self._hard_limit      = hard_limit
        self._prior_orders    = prior_orders
        init_db(db_path)

    def run(self) -> MonitorAgent:
        executed_picks: list[dict] = []

        for pick in self._picks:
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
                prior_orders=self._prior_orders,
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

        # 08:30 pre-market
        if t.hour == 8 and t.minute == 30:
            job = PremarketJob(
                candidates=[],
                capital=CAPITAL,
                db_path=DB_PATH,
                telegram_chat_id=TELEGRAM_CHAT_ID or None,
                current_positions=[],
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
                    prior_orders=[],
                )
                monitor = job.run()
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
