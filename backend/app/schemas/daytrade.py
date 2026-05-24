from pydantic import BaseModel

from .base import Side, LotType, ThreadState, AlertLevel, AlertKind
from .predict import SectorAllocation, Tick


class Position(BaseModel):
    code: str
    name: str
    sector: str
    side: Side
    entry_price: float
    last_price: float
    quantity: int
    lot: LotType
    cost: float
    market_value: float
    pnl: float
    pnl_pct: float
    target_price: float
    stop_loss_price: float
    distance_to_tp_pct: float
    distance_to_sl_pct: float
    thread_state: ThreadState
    confidence: float
    opened_at: str


class Alert(BaseModel):
    id: str
    time: str
    level: AlertLevel
    code: str
    name: str
    text: str
    kind: AlertKind
    resolved: bool
    source: str
    telegram_sent: bool


class StrategyThread(BaseModel):
    code: str
    name: str
    state: ThreadState
    last_tick_at: str
    age_seconds: float
    target_price: float
    stop_loss_price: float
    distance_label: str
    poll_count: int
    alert_count: int


class SingleMax(BaseModel):
    value: float
    ratio: float
    limit: float
    ok: bool


class RiskCockpit(BaseModel):
    budget: int
    used: int
    free: int
    utilization: float
    intraday_pnl: float
    intraday_pnl_pct: float
    daily_max_dd_limit: float
    sector_allocation: list[SectorAllocation]
    blacklist: list[str]
    single_max: SingleMax


class DaytradeLive(BaseModel):
    countdown_seconds: float
    force_close_at: str
    monitoring_count: int
    closed_count: int
    unrealized_pnl: float
    realized_pnl: float
    net_pnl: float
    net_value: float
    positions: list[Position]
    alerts: list[Alert]
    threads: list[StrategyThread]
    risk: RiskCockpit
    next_poll_in_seconds: float


class AIMark(BaseModel):
    index: int
    time: str
    kind: str
    label: str
    confidence: float
    reasoning: str | None = None


class NextActionSuggestion(BaseModel):
    kind: str
    text: str
    confidence: float
    suggested_value: float | None = None


class ChartView(BaseModel):
    code: str
    ticks: list[Tick]
    ma20: list[float]
    ma5: list[float]
    bollinger: dict
    rsi: list[float]
    ai_marks: list[AIMark]
    next_action_suggestion: NextActionSuggestion | None = None
