"""ws/daytrade.py — Wave 2-D upgrade.

WebSocket /ws/daytrade: pushes DaytradeLive snapshot every 30 s.
取不到真實資料時推 {"error": "no_data"}，不以假快照充數。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..deps import authenticate_ws_token
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

log = logging.getLogger(__name__)

# ── Path setup ────────────────────────────────────────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)


# ── Countdown ─────────────────────────────────────────────────────────────────

def _calc_countdown_seconds() -> int:
    """Return seconds until 13:15 Taipei time (force close). Always correct."""
    try:
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        force_close = now.replace(hour=13, minute=15, second=0, microsecond=0)
        return max(0, int((force_close - now).total_seconds()))
    except Exception:
        return 9900


# ── Live snapshot ─────────────────────────────────────────────────────────────

async def get_live_snapshot() -> dict:
    """
    Build a DaytradeLive snapshot dict.

    真實資料取自 ai_stock portfolio；取不到就回 error payload。
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
            force_close_at="13:15:00",
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

    except Exception as e:
        # 原本這裡 fallback 到一整份寫死的快照：2330 台積電、+1,500 未實現
        # 損益、淨值 1,001,500、假的警報與策略執行緒。REST 端點改成誠實
        # 之後，畫面上的數字仍然不對——因為 WebSocket 每 30 秒把這份假的
        # 推上去蓋掉。
        log.warning("daytrade snapshot failed: %s", e)
        return {
            "error": "no_data",
            "detail": f"取不到當沖實況：{e}。main.py 是否在執行？",
        }


@router.websocket("/ws/daytrade")
async def ws_daytrade(websocket: WebSocket) -> None:
    # Auth: browsers cannot set headers on WS, so the JWT access token is passed
    # as a ?token= query parameter. Reject invalid/missing tokens with 4401.
    user = authenticate_ws_token(websocket.query_params.get("token"))
    if user is None:
        await websocket.close(code=4401)
        return
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
