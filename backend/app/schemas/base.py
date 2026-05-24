from enum import Enum
from pydantic import BaseModel


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Signal(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class LotType(str, Enum):
    COMMON = "common"
    INTRADAY_ODD = "intraday_odd"


class ThreadState(str, Enum):
    PENDING = "pending"
    MONITORING = "monitoring"
    CLOSED_TP = "closed_tp"
    CLOSED_SL = "closed_sl"
    CLOSED_FORCE = "closed_force"
    REJECTED = "rejected"


class AlertLevel(str, Enum):
    HIGH = "high"
    MED = "med"
    LOW = "low"


class AlertKind(str, Enum):
    TARGET_HIT = "target_hit"
    STOP_LOSS = "stop_loss"
    STOP_WARN = "stop_warn"
    TP = "tp"
    NOTE = "note"
    SKIP = "skip"


class AppMode(str, Enum):
    SIMULATION = "simulation"
    LIVE = "live"


class ConfidenceTier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Money(BaseModel):
    amount: int
    formatted: str
