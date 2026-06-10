import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..deps import authenticate_ws_token
from ..schemas.market import FxQuote, IndexQuote, MarketSnapshot

router = APIRouter(tags=["ws-market"])


def _build_snapshot() -> dict:
    now = datetime.now(timezone.utc)
    tw_hour = (now.hour + 8) % 24
    tw_minute = now.minute
    tw_minutes = tw_hour * 60 + tw_minute
    if 9 * 60 <= tw_minutes < 13 * 60 + 30:
        session = "open"
    elif 8 * 60 <= tw_minutes < 9 * 60:
        session = "pre"
    elif 13 * 60 + 30 <= tw_minutes < 14 * 60:
        session = "post"
    else:
        session = "closed"

    force_close_tw = 13 * 60 + 25
    countdown = max(0.0, float((force_close_tw - tw_minutes) * 60 - now.second))

    snap = MarketSnapshot(
        session=session,
        server_time=now.isoformat(),
        countdown_to_close_seconds=countdown,
        taiex=IndexQuote(value=21845.32, change=127.45, change_pct=0.0059),
        otc=IndexQuote(value=248.73, change=1.82, change_pct=0.0074),
        usd_twd=FxQuote(value=31.82, change=-0.05),
        api_status="ok",
        db_size_mb=42.5,
        load_ms=8.3,
    )
    return snap.model_dump()


@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket) -> None:
    # Auth via ?token= query param (WS cannot send Authorization header).
    user = authenticate_ws_token(websocket.query_params.get("token"))
    if user is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            data = _build_snapshot()
            await websocket.send_json(data)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
