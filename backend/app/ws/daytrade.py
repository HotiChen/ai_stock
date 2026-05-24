"""ws/daytrade.py — Wave 2-D upgrade.

WebSocket /ws/daytrade: pushes DaytradeLive snapshot every 30 s.
Real countdown_seconds is always computed; data falls back to mock when
ai_stock modules are unavailable.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..schemas.base import AlertKind, AlertLevel, LotType, Side, ThreadState
from ..schemas.daytrade import (
    Alert,
    DaytradeLive,
    Position,
    RiskCockpit,
    SingleMax,
    StrategyThread,
)
from ..schemas.predict import SectorAllocation

router = APIRouter(tags=["ws-daytrade"])

# ── Path setup ────────────────────────────────────────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)


# ── Countdown ─────────────────────────────────────────────────────────────────

def _calc_countdown_seconds() -> int:
    """Return seconds until 13:25 Taipei time (force close). Always correct."""
    try:
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        force_close = now.replace(hour=13, minute=25, second=0, microsecond=0)
        return max(0, int((force_close - now).total_seconds()))
    except Exception:
        return 9900


# ── Live snapshot ─────────────────────────────────────────────────────────────

async def get_live_snapshot() -> dict:
    """
    Build a DaytradeLive snapshot dict.

    try: real data from ai_stock portfolio module
    except: mock data (countdown_seconds still real)
    """
    try:
        import portfolio as pf_mod

        portfolio_obj = pf_mod.SimulatedPortfolio()
        raw_positions = portfolio_obj.get_positions()

        positions: list[Position] = []
        unrealized = 0
        for p in raw_positions:
            cur = p.current_price if p.current_price else p.avg_cost
            mv = p.market_value(cur)
            pnl = p.unrealized_pnl(cur)
            unrealized += pnl
            positions.append(Position(
                code=p.code,
                name=p.name,
                sector="未知",
                side=Side.BUY,
                entry_price=p.avg_cost,
                last_price=cur,
                quantity=p.quantity,
                lot=LotType.INTRADAY_ODD if p.is_fractional else LotType.COMMON,
                cost=int(p.total_cost),
                market_value=int(mv),
                pnl=int(pnl),
                pnl_pct=round(p.unrealized_pnl_pct(cur) / 100.0, 4),
                target_price=round(p.avg_cost * 1.05, 0),
                stop_loss_price=round(p.avg_cost * 0.97, 0),
                distance_to_tp_pct=0.05,
                distance_to_sl_pct=0.03,
                thread_state=ThreadState.MONITORING,
                confidence=0.70,
                opened_at=p.entry_date.isoformat() + "T09:00:00+08:00",
            ))

        capital = float(os.getenv("BUDGET", "1000000"))
        used = sum(pos.cost for pos in positions)
        risk = RiskCockpit(
            budget=int(capital),
            used=int(used),
            free=max(0, int(capital - used)),
            utilization=round(used / capital, 4) if capital else 0,
            intraday_pnl=int(unrealized),
            intraday_pnl_pct=round(unrealized / capital, 4) if capital else 0,
            daily_max_dd_limit=-0.03,
            sector_allocation=[],
            blacklist=[],
            single_max=SingleMax(
                value=max((pos.market_value for pos in positions), default=0),
                ratio=0.0,
                limit=0.2,
                ok=True,
            ),
        )

        live = DaytradeLive(
            countdown_seconds=_calc_countdown_seconds(),
            force_close_at="13:25:00",
            monitoring_count=len(positions),
            closed_count=0,
            unrealized_pnl=int(unrealized),
            realized_pnl=0,
            net_pnl=int(unrealized),
            net_value=int(capital + unrealized),
            positions=positions,
            alerts=[],
            threads=[],
            risk=risk,
            next_poll_in_seconds=30,
        )
        return live.model_dump()

    except Exception:
        # Fallback mock — countdown_seconds is always real
        positions = [
            Position(
                code="2330",
                name="台積電",
                sector="半導體",
                side=Side.BUY,
                entry_price=1120,
                last_price=1135,
                quantity=1,
                lot=LotType.COMMON,
                cost=112_000,
                market_value=113_500,
                pnl=1500,
                pnl_pct=0.0134,
                target_price=1185,
                stop_loss_price=1085,
                distance_to_tp_pct=0.044,
                distance_to_sl_pct=0.044,
                thread_state=ThreadState.MONITORING,
                confidence=0.82,
                opened_at="2026-05-24T09:05:32+08:00",
            ),
        ]
        alerts = [
            Alert(
                id="alert-001",
                time="10:41:55",
                level=AlertLevel.HIGH,
                code="2330",
                name="台積電",
                text="台積電達到目標價 1185，建議評估獲利了結",
                kind=AlertKind.TARGET_HIT,
                resolved=False,
                source="MonitorAgent",
                telegram_sent=True,
            ),
        ]
        threads = [
            StrategyThread(
                code="2330",
                name="台積電",
                state=ThreadState.MONITORING,
                last_tick_at="2026-05-24T10:45:00+08:00",
                age_seconds=5988,
                target_price=1185,
                stop_loss_price=1085,
                distance_label="+4.4% / -4.4%",
                poll_count=200,
                alert_count=1,
            ),
        ]
        risk = RiskCockpit(
            budget=1_000_000,
            used=245_000,
            free=755_000,
            utilization=0.245,
            intraday_pnl=3500,
            intraday_pnl_pct=0.0035,
            daily_max_dd_limit=-0.03,
            sector_allocation=[
                SectorAllocation(name="半導體", ratio=0.245, limit=0.4, value=245_000),
            ],
            blacklist=[],
            single_max=SingleMax(value=131_500, ratio=0.1315, limit=0.2, ok=True),
        )
        live = DaytradeLive(
            countdown_seconds=_calc_countdown_seconds(),  # always real
            force_close_at="13:25:00",
            monitoring_count=1,
            closed_count=0,
            unrealized_pnl=1500,
            realized_pnl=0,
            net_pnl=1500,
            net_value=1_001_500,
            positions=positions,
            alerts=alerts,
            threads=threads,
            risk=risk,
            next_poll_in_seconds=30,
        )
        return live.model_dump()


@router.websocket("/ws/daytrade")
async def ws_daytrade(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        # Send initial snapshot immediately
        await websocket.send_json(await get_live_snapshot())
        while True:
            await asyncio.sleep(30)
            await websocket.send_json(await get_live_snapshot())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
