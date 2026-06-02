"""journal.py — Wave 2-D upgrade.

Thin wrapper around learning_db with mock fallback.
Every endpoint always returns 200.
"""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.journal import JournalEntry, ChatMessage, ChatRequest

router = APIRouter(prefix="/api/journal", tags=["journal"])

# ── Path setup ────────────────────────────────────────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)

# ── Mock ──────────────────────────────────────────────────────────────────────

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


# ── Real data builder ─────────────────────────────────────────────────────────

def _load_real_entries(limit: int = 50) -> list[JournalEntry]:
    """Try learning_db.recent_entries(). Raises on failure."""
    import learning_db
    if hasattr(learning_db, "recent_entries"):
        raw = learning_db.recent_entries(limit=limit)
        if not raw:
            raise RuntimeError("no entries")
        entries = []
        for i, r in enumerate(raw):
            if isinstance(r, dict):
                entries.append(JournalEntry(
                    id=r.get("id", i + 1),
                    date=str(r.get("date", "2026-01-01")),
                    code=r.get("code", ""),
                    name=r.get("name", ""),
                    pnl=r.get("pnl", 0),
                    lesson=r.get("lesson", ""),
                    rule_updated=bool(r.get("rule_updated", False)),
                    tags=r.get("tags", []),
                    related_trade_id=r.get("related_trade_id"),
                ))
            elif hasattr(r, "__dict__"):
                d = r.__dict__
                entries.append(JournalEntry(
                    id=d.get("id", i + 1),
                    date=str(d.get("date", "2026-01-01")),
                    code=d.get("code", ""),
                    name=d.get("name", ""),
                    pnl=d.get("pnl", 0),
                    lesson=d.get("lesson", ""),
                    rule_updated=bool(d.get("rule_updated", False)),
                    tags=d.get("tags", []),
                    related_trade_id=d.get("related_trade_id"),
                ))
        return entries
    raise RuntimeError("learning_db.recent_entries not found")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/", response_model=list[JournalEntry])
async def list_journal(current_user: User = Depends(get_current_user)) -> list[JournalEntry]:
    """
    GET /api/journal → JournalEntry[]

    try: learning_db.recent_entries()
    except: mock entries
    """
    try:
        return _load_real_entries(limit=50)
    except Exception:
        return _MOCK_ENTRIES


@router.post("/", response_model=JournalEntry)
async def create_journal(
    entry: JournalEntry,
    current_user: User = Depends(get_current_user),
) -> JournalEntry:
    """POST /api/journal → JournalEntry (新增)"""
    try:
        import learning_db
        if hasattr(learning_db, "save_entry"):
            learning_db.save_entry(entry.model_dump())
    except Exception:
        pass
    return entry


@router.get("/chat/history", response_model=list[ChatMessage])
async def chat_history(current_user: User = Depends(get_current_user)) -> list[ChatMessage]:
    """GET /api/journal/chat/history → ChatMessage[]"""
    return _MOCK_CHAT_HISTORY
