from typing import Literal
from pydantic import BaseModel

from .base import Side, LotType, AppMode
from .predict import RiskCheck


class OrderSource(BaseModel):
    type: Literal["ai", "manual"]
    run_id: str | None = None
    confidence: float | None = None
    reason: str | None = None
    model: str | None = None


class OrderTicket(BaseModel):
    code: str
    name: str
    last_price: float
    side: Side
    lot: LotType
    price_type: Literal["LMT", "MKT"]
    price: float
    quantity: int
    amount: float
    target_price: float
    stop_loss_price: float
    source: OrderSource
    risk_checks: list[RiskCheck]
    mode: AppMode
    dry_run_preview: str


class OrderResult(BaseModel):
    order_id: str
    status: Literal["submitted", "filled", "rejected", "cancelled"]
    filled_at: str | None = None
    filled_price: float | None = None
    filled_amount: float | None = None
    rejection_reason: str | None = None
    telegram_message_id: str | None = None


class Trade(BaseModel):
    id: int
    time: str
    date: str
    code: str
    name: str
    side: Side
    quantity: int
    price: float
    amount: float
    lot: LotType
    status: Literal["filled", "cancelled", "partial"]
    pnl: float | None = None
    reason: str | None = None
    order_id: str
    sector: str
