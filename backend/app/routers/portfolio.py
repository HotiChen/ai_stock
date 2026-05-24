from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.base import LotType, Side, ThreadState
from ..schemas.daytrade import Position
from ..schemas.portfolio import PortfolioSummary
from ..schemas.predict import SectorAllocation

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _mock_portfolio() -> PortfolioSummary:
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
        Position(
            code="2454",
            name="聯發科",
            sector="半導體",
            side=Side.BUY,
            entry_price=1295,
            last_price=1315,
            quantity=1,
            lot=LotType.COMMON,
            cost=129_500,
            market_value=131_500,
            pnl=2000,
            pnl_pct=0.0154,
            target_price=1380,
            stop_loss_price=1260,
            distance_to_tp_pct=0.049,
            distance_to_sl_pct=0.042,
            thread_state=ThreadState.MONITORING,
            confidence=0.76,
            opened_at="2026-05-24T09:12:18+08:00",
        ),
    ]
    return PortfolioSummary(
        net_value=1_034_200,
        budget=1_000_000,
        free_cash=755_000,
        cash_ratio=0.755,
        unrealized_pnl=3_500,
        realized_pnl=12_500,
        net_pnl=16_000,
        net_pnl_pct=0.016,
        position_count=2,
        closed_today=1,
        countdown_seconds=9900,
        positions=positions,
        sector_breakdown=[
            SectorAllocation(name="半導體", ratio=0.245, limit=0.4, value=245_000),
        ],
        recent_pnl_days=[
            {"date": "2026-05-10", "pnl": 8200},
            {"date": "2026-05-11", "pnl": -3400},
            {"date": "2026-05-12", "pnl": 12000},
            {"date": "2026-05-13", "pnl": 5600},
            {"date": "2026-05-14", "pnl": -1200},
            {"date": "2026-05-17", "pnl": 9800},
            {"date": "2026-05-18", "pnl": 4200},
            {"date": "2026-05-19", "pnl": -2800},
            {"date": "2026-05-20", "pnl": 11400},
            {"date": "2026-05-21", "pnl": 7600},
            {"date": "2026-05-22", "pnl": 3200},
            {"date": "2026-05-23", "pnl": 8900},
            {"date": "2026-05-24", "pnl": 16000},
        ],
        cumulative_vs_index={
            "dates": ["2026-05-10", "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14"],
            "me": [1.0, 0.997, 1.009, 1.015, 1.014],
            "index": [1.0, 0.998, 1.005, 1.008, 1.007],
        },
        alpha_mtd=0.009,
    )


@router.get("/", response_model=PortfolioSummary)
async def portfolio(current_user: User = Depends(get_current_user)) -> PortfolioSummary:
    return _mock_portfolio()
