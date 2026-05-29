"""paper_trade.py — GET /api/paper-trade/*

Exposes paper_trading.db data (written by dt_paper_trade.py) as JSON.

GET /api/paper-trade/summary  → PaperTradeSummary
GET /api/paper-trade/history  → list[PaperTradeRecord]
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User

router = APIRouter(prefix="/api/paper-trade", tags=["paper-trade"])

_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock", "data", "paper_trading.db")
)
_INIT_CAPITAL = 30_000.0


def _conn(path: str = _DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _get_trades() -> list[dict]:
    if not os.path.exists(_DB_PATH):
        return []
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT * FROM trades ORDER BY date ASC"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_config() -> dict:
    if not os.path.exists(_DB_PATH):
        return {"initial_capital": _INIT_CAPITAL, "start_date": date.today().isoformat()}
    try:
        with _conn() as c:
            rows = c.execute("SELECT key, value FROM config").fetchall()
        cfg = {r["key"]: r["value"] for r in rows}
        return {
            "initial_capital": float(cfg.get("initial_capital", _INIT_CAPITAL)),
            "start_date": cfg.get("start_date", date.today().isoformat()),
        }
    except Exception:
        return {"initial_capital": _INIT_CAPITAL, "start_date": date.today().isoformat()}


def _build_summary(trades: list[dict], cfg: dict) -> dict:
    init_capital = cfg["initial_capital"]
    start_date = cfg["start_date"]

    real = [t for t in trades if t["outcome"] not in ("no_pick", "no_capital", None)]
    hits = sum(1 for t in real if t["outcome"] == "hit_target")
    stops = sum(1 for t in real if t["outcome"] == "hit_stop")
    neutrals = sum(1 for t in real if t["outcome"] == "neutral")
    no_trades = sum(1 for t in trades if t["outcome"] in ("no_pick", "no_capital"))

    win_rate = hits / len(real) if real else 0.0
    last_capital = trades[-1]["capital_after"] if trades else init_capital
    total_pnl = last_capital - init_capital
    total_pnl_pct = total_pnl / init_capital if init_capital else 0.0

    # max drawdown
    peak = init_capital
    max_dd = 0.0
    running = init_capital
    for t in trades:
        running = t["capital_after"]
        if running > peak:
            peak = running
        dd = (peak - running) / peak if peak else 0.0
        if dd > max_dd:
            max_dd = dd

    # streaks
    streak_win = streak_loss = cur = 0
    last_o: Optional[str] = None
    for t in real:
        o = t["outcome"]
        cur = (cur + 1) if o == last_o else 1
        if o == "hit_target":
            streak_win = max(streak_win, cur)
        elif o == "hit_stop":
            streak_loss = max(streak_loss, cur)
        last_o = o

    avg_win = (
        sum(t["pnl"] for t in real if t["outcome"] == "hit_target") / hits
        if hits else 0.0
    )
    avg_loss = (
        sum(t["pnl"] for t in real if t["outcome"] == "hit_stop") / stops
        if stops else 0.0
    )
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss if real else 0.0

    # equity curve: [{date, capital}]
    equity: list[dict] = []
    cap = init_capital
    for t in trades:
        cap = t["capital_after"]
        equity.append({"date": t["date"], "capital": cap})

    return {
        "start_date": start_date,
        "total_days": len(trades),
        "real_trades": len(real),
        "no_trade_days": no_trades,
        "initial_capital": init_capital,
        "current_capital": last_capital,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "win_rate": win_rate,
        "hits": hits,
        "stops": stops,
        "neutrals": neutrals,
        "max_drawdown": max_dd,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "streak_win": streak_win,
        "streak_loss": streak_loss,
        "equity_curve": equity,
    }


@router.get("/summary")
async def get_summary(
    current_user: User = Depends(get_current_user),
) -> dict:
    trades = _get_trades()
    cfg = _get_config()
    return _build_summary(trades, cfg)


@router.get("/history")
async def get_history(
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    return _get_trades()
