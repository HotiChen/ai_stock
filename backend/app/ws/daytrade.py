import asyncio

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


def _mock_live_payload() -> dict:
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
        countdown_seconds=9900,
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
        await websocket.send_json(_mock_live_payload())
        while True:
            await asyncio.sleep(30)
            await websocket.send_json(_mock_live_payload())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
