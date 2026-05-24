from typing import Literal
from pydantic import BaseModel


class IndexQuote(BaseModel):
    value: float
    change: float
    change_pct: float


class FxQuote(BaseModel):
    value: float
    change: float


class MarketSnapshot(BaseModel):
    session: Literal["pre", "open", "post", "closed"]
    server_time: str
    countdown_to_close_seconds: float
    taiex: IndexQuote
    otc: IndexQuote
    usd_twd: FxQuote
    api_status: Literal["ok", "degraded", "down"]
    db_size_mb: float
    load_ms: float
