from pydantic import BaseModel

from .order import Trade


class BacktestParams(BaseModel):
    start: str
    end: str
    strategy: str
    initial_capital: int = 1_000_000
    slippage: float = 0.001
    params: dict | None = None


class BacktestResult(BaseModel):
    id: str
    range: dict
    initial_capital: int
    slippage: float
    strategy: str
    trades: int
    wins: int
    losses: int
    winrate: float
    total_pnl: float
    total_pnl_pct: float
    avg_win: float
    avg_loss: float
    sharpe: float
    max_dd: float
    beat_index_pct: float
    monthly_returns: list[float]
    equity_curve: list[dict]
    trades_sample: list[Trade]
    ai_conclusion: str
