from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/")
async def dashboard(current_user: User = Depends(get_current_user)) -> dict:
    """Dashboard aggregate — mock data (Wave 2 will wire real modules)."""
    return {
        "date": "2026-05-24",
        "mode": "simulation",
        "kpi": {
            "net_value": 1_034_200,
            "net_pnl": 34_200,
            "net_pnl_pct": 0.0342,
            "realized_pnl": 12_500,
            "unrealized_pnl": 21_700,
            "win_rate": 0.68,
            "trades_today": 3,
        },
        "market": {
            "taiex": {"value": 21845.32, "change": 127.45, "change_pct": 0.0059},
            "otc": {"value": 248.73, "change": 1.82, "change_pct": 0.0074},
        },
        "alerts_count": 2,
        "positions_count": 2,
    }
