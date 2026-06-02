from pydantic import BaseModel


class JournalEntry(BaseModel):
    id: int
    date: str
    code: str
    name: str
    pnl: float
    lesson: str
    rule_updated: bool
    tags: list[str]
    related_trade_id: int | None = None


class ChatMessage(BaseModel):
    id: str
    role: str
    content: str
    ts: str
    model: str | None = None
    tool_calls: list | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
