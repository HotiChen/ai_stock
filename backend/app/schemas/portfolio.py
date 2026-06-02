from pydantic import BaseModel

from .daytrade import Position
from .predict import SectorAllocation


class PortfolioSummary(BaseModel):
    net_value: float
    budget: int
    free_cash: float
    cash_ratio: float
    unrealized_pnl: float
    realized_pnl: float
    net_pnl: float
    net_pnl_pct: float
    position_count: int
    closed_today: int
    countdown_seconds: float
    positions: list[Position]
    sector_breakdown: list[SectorAllocation]
    recent_pnl_days: list[dict]
    cumulative_vs_index: dict
    alpha_mtd: float
