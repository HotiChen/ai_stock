from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.market import MarketSnapshot, IndexQuote, FxQuote

router = APIRouter(prefix="/api/market", tags=["market"])


def _mock_snapshot() -> MarketSnapshot:
    now = datetime.now(timezone.utc)
    server_time = now.isoformat()
    # Determine session: Taiwan market 09:00-13:30 CST (UTC+8) = 01:00-05:30 UTC
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

    # Seconds until 13:15 force-close
    force_close_tw = 13 * 60 + 25
    countdown = max(0.0, float((force_close_tw - tw_minutes) * 60 - now.second))

    return MarketSnapshot(
        session=session,
        server_time=server_time,
        countdown_to_close_seconds=countdown,
        taiex=IndexQuote(value=21845.32, change=127.45, change_pct=0.0059),
        otc=IndexQuote(value=248.73, change=1.82, change_pct=0.0074),
        usd_twd=FxQuote(value=31.82, change=-0.05),
        api_status="ok",
        db_size_mb=42.5,
        load_ms=8.3,
    )


@router.get("/snapshot", response_model=MarketSnapshot)
async def snapshot(current_user: User = Depends(get_current_user)) -> MarketSnapshot:
    return _mock_snapshot()
