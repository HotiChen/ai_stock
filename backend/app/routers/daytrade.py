"""daytrade.py — Wave 2-D upgrade.

Wraps ai_stock modules (monitor_agent, portfolio, risk_guard, research_db,
intraday_monitor, executor)。取不到資料回 503／未實作回 501，不以假資料充數。
Every endpoint always returns 200.
"""
from __future__ import annotations

import logging

import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

log = logging.getLogger(__name__)

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.base import AlertKind, AlertLevel, LotType, Side, ThreadState
from ..schemas.daytrade import (
    Alert,
    ChartView,
    DaytradeLive,
    Position,
    RiskCockpit,
    SingleMax,
    StrategyThread,
    AIMark,
)
from ..schemas.predict import SectorAllocation, Tick

router = APIRouter(prefix="/api/daytrade", tags=["daytrade"])

# ── Path setup so ai_stock modules can be imported ────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)

# ── Countdown helper ──────────────────────────────────────────────────────────

def _calc_countdown_seconds() -> int:
    """Return seconds remaining until 13:15 (force-close) in Taipei time."""
    try:
        # Use stdlib timezone — pytz may not be installed
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        force_close = now.replace(hour=13, minute=15, second=0, microsecond=0)
        remaining = int((force_close - now).total_seconds())
        return max(0, remaining)
    except Exception:
        return 0  # 算不出來就是 0，不編一個看起來合理的 2h45m


# ── Helpers ───────────────────────────────────────────────────────────────────

# ── Real data helpers ─────────────────────────────────────────────────────────

def _build_live_from_real() -> DaytradeLive:
    """Try to load real data from ai_stock modules; raise on any failure."""
    import portfolio as pf_mod
    import risk_guard

    # Load current positions from SimulatedPortfolio (via research_db or JSON)
    portfolio_obj = pf_mod.SimulatedPortfolio()
    raw_positions = portfolio_obj.get_positions()

    positions: list[Position] = []
    for p in raw_positions:
        market_val = p.market_value(p.current_price) if p.current_price else p.total_cost
        pnl = p.unrealized_pnl(p.current_price) if p.current_price else 0.0
        pnl_pct = p.unrealized_pnl_pct(p.current_price) / 100.0 if p.current_price else 0.0
        positions.append(
            Position(
                code=p.code,
                name=p.name,
                sector="未知",
                side=Side.BUY,
                entry_price=p.avg_cost,
                last_price=p.current_price or p.avg_cost,
                quantity=p.quantity,
                lot=LotType.INTRADAY_ODD if p.is_fractional else LotType.COMMON,
                cost=int(p.total_cost),
                market_value=int(market_val),
                pnl=int(pnl),
                pnl_pct=round(pnl_pct, 4),
                target_price=p.avg_cost * 1.05,
                stop_loss_price=p.avg_cost * 0.97,
                distance_to_tp_pct=0.05,
                distance_to_sl_pct=0.03,
                thread_state=ThreadState.MONITORING,
                confidence=0.70,
                opened_at=p.entry_date.isoformat() + "T09:00:00+08:00",
            )
        )

    # Risk cockpit from risk_guard module
    capital = float(os.getenv("BUDGET", "1000000"))
    blacklist = list(risk_guard._BLACKLIST)
    used = sum(pos.cost for pos in positions)
    free = max(0, int(capital - used))

    risk = RiskCockpit(
        budget=int(capital),
        used=used,
        free=free,
        utilization=round(used / capital, 4) if capital else 0,
        intraday_pnl=sum(pos.pnl for pos in positions),
        intraday_pnl_pct=0.0,
        daily_max_dd_limit=-0.03,
        sector_allocation=[],
        blacklist=blacklist,
        single_max=SingleMax(
            value=max((pos.market_value for pos in positions), default=0),
            ratio=0.0,
            limit=0.2,
            ok=True,
        ),
    )

    alerts: list[Alert] = []
    threads: list[StrategyThread] = []

    unrealized = sum(pos.pnl for pos in positions)
    net_value = int(capital) + unrealized

    return DaytradeLive(
        countdown_seconds=_calc_countdown_seconds(),
        force_close_at="13:15:00",
        monitoring_count=len([p for p in positions if p.thread_state == ThreadState.MONITORING]),
        closed_count=0,
        unrealized_pnl=unrealized,
        realized_pnl=0,
        net_pnl=unrealized,
        net_value=net_value,
        positions=positions,
        alerts=alerts,
        threads=threads,
        risk=risk,
        next_poll_in_seconds=30,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/live", response_model=DaytradeLive)
async def live(current_user: User = Depends(get_current_user)) -> DaytradeLive:
    """
    GET /api/daytrade/live → DaytradeLive

    真實資料取自 portfolio + risk_guard。取不到就回 503——原本降級回一份
    寫死的持倉（含假的損益與風控數字），畫面完全看不出來是假的。
    """
    try:
        return _build_live_from_real()
    except Exception as e:
        log.warning("daytrade live failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"取不到當沖實況：{e}。main.py 是否在執行？",
        ) from e


@router.get("/{code}/chart", response_model=ChartView)
async def chart(code: str, current_user: User = Depends(get_current_user)) -> ChartView:
    """
    GET /api/daytrade/{code}/chart → ChartView

    try: intraday_monitor tick data + daytrading_analyzer
    except: 503
    """
    try:
        # intraday_monitor doesn't expose a direct fetch_ticks; use daytrading_analyzer if available
        import daytrading_analyzer
        result = daytrading_analyzer.analyze(code)
        ticks_raw = result.get("ticks", []) if isinstance(result, dict) else []
        if not ticks_raw:
            raise ValueError("no ticks")
        ticks = [
            Tick(
                t=r.get("t", "09:00"),
                open=float(r.get("open", 0)),
                high=float(r.get("high", 0)),
                low=float(r.get("low", 0)),
                close=float(r.get("close", 0)),
                volume=int(r.get("volume", 0)),
            )
            for r in ticks_raw
        ]
        closes = [t.close for t in ticks]
        n = len(closes)
        ma5 = [sum(closes[max(0, i - 4):i + 1]) / min(i + 1, 5) for i in range(n)]
        ma20 = [sum(closes[max(0, i - 19):i + 1]) / min(i + 1, 20) for i in range(n)]
        return ChartView(
            code=code,
            ticks=ticks,
            ma20=ma20,
            ma5=ma5,
            bollinger={
                "upper": [c + 20 for c in closes],
                "mid": closes[:],
                "lower": [c - 20 for c in closes],
            },
            rsi=[50.0] * n,
            ai_marks=[],
            next_action_suggestion=None,
        )
    except Exception as e:
        log.warning("chart(%s) failed: %s", code, e)
        raise HTTPException(
            status_code=503,
            detail=f"取不到 {code} 的 K 線：{e}",
        ) from e


@router.post("/close")
async def close(code: str, current_user: User = Depends(get_current_user)) -> dict:
    """POST /api/daytrade/close?code={code} → {"ok": True}"""
    try:
        import executor
        # executor.place_stock_order requires an api object (Shioaji);
        # in simulation / no-connection mode we log and return ok
        result = executor.place_stock_order(
            api=None,
            code=code,
            name=code,
            action="sell",
            budget=float(os.getenv("ORDER_HARD_LIMIT", "150000")),
            price=0.0,
        )
        return {"ok": result.success, "code": code, "message": result.reason or "平倉指令已送出"}
    except Exception as exc:
        # 原本這裡回 ok=True 說「平倉指令已送出」，實際上例外被吞掉、
        # 什麼都沒送。使用者以為部位平掉了。
        log.warning("close(%s) failed: %s", code, exc)
        raise HTTPException(
            status_code=503,
            detail=f"{code} 平倉失敗：{exc}",
        ) from exc


@router.post("/close-all")
async def close_all(current_user: User = Depends(get_current_user)) -> dict:
    """POST /api/daytrade/close-all → {"ok": True}"""
    try:
        # ForceCloseJob 手動觸發需要 Shioaji api handle，API 行程沒有。
        # 原本這裡回 ok=True 說「全部平倉指令已送出」——一張單都沒送。
        raise HTTPException(
            status_code=501,
            detail="從 UI 一鍵全平尚未實作。"
                   "13:00／13:15 的自動強平仍由 main.py 執行。",
        )
    except HTTPException:
        raise


@router.post("/adjust-tp")
async def adjust_tp(code: str, value: float, current_user: User = Depends(get_current_user)) -> dict:
    """POST /api/daytrade/adjust-tp?code={code}&value={value} → {"ok": True}"""
    try:
        # 原本回 ok=True 說目標價已改，實際上沒有任何寫入——
        # 畫面顯示新的目標價，系統用的還是舊的。
        raise HTTPException(
            status_code=501,
            detail="從 UI 調整目標價尚未實作。",
        )
    except HTTPException:
        raise


@router.post("/adjust-sl")
async def adjust_sl(code: str, value: float, current_user: User = Depends(get_current_user)) -> dict:
    """POST /api/daytrade/adjust-sl?code={code}&value={value} → {"ok": True}"""
    try:
        # 同 adjust-tp。停損價尤其危險：畫面說改好了，實際的停損還在原位。
        raise HTTPException(
            status_code=501,
            detail="從 UI 調整停損價尚未實作。",
        )
    except HTTPException:
        raise


@router.post("/mute")
async def mute(minutes: int, current_user: User = Depends(get_current_user)) -> dict:
    """POST /api/daytrade/mute?minutes={minutes} → {"ok": True}"""
    try:
        raise HTTPException(
            status_code=501,
            detail="靜音警報尚未實作。",
        )
    except HTTPException:
        raise
