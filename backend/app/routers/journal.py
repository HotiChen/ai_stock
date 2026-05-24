from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.journal import JournalEntry, ChatMessage, ChatRequest

router = APIRouter(prefix="/api/journal", tags=["journal"])

_MOCK_ENTRIES = [
    JournalEntry(
        id=1,
        date="2026-05-23",
        code="2330",
        name="台積電",
        pnl=8900,
        lesson="台積電突破季線後持有至目標價，策略執行良好。下次可考慮提前在突破點建倉。",
        rule_updated=False,
        tags=["突破策略", "台積電", "成功案例"],
        related_trade_id=100,
    ),
    JournalEntry(
        id=2,
        date="2026-05-21",
        code="2303",
        name="聯電",
        pnl=-3400,
        lesson="聯電停損觸發，進場信心不足，次日反彈。應更嚴格確認信心門檻。",
        rule_updated=True,
        tags=["停損", "反省", "信心門檻"],
    ),
]

_MOCK_CHAT_HISTORY = [
    ChatMessage(
        id="msg-001",
        role="user",
        content="今天台積電走勢如何？",
        ts="2026-05-24T10:00:00+08:00",
    ),
    ChatMessage(
        id="msg-002",
        role="assistant",
        content="台積電今日表現強勁，突破季線後量能放大，外資持續買超...",
        ts="2026-05-24T10:00:03+08:00",
        model="claude-sonnet-4-6",
    ),
]


@router.get("/", response_model=list[JournalEntry])
async def list_journal(current_user: User = Depends(get_current_user)) -> list[JournalEntry]:
    return _MOCK_ENTRIES


@router.post("/", response_model=JournalEntry)
async def create_journal(
    entry: JournalEntry,
    current_user: User = Depends(get_current_user),
) -> JournalEntry:
    return entry


@router.get("/chat/history", response_model=list[ChatMessage])
async def chat_history(current_user: User = Depends(get_current_user)) -> list[ChatMessage]:
    return _MOCK_CHAT_HISTORY
