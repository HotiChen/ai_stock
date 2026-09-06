"""journal.py — Wave 2-D upgrade.

Thin wrapper around learning_db with mock fallback.
Every endpoint always returns 200.
"""
from __future__ import annotations

import logging

import os
import sys

from fastapi import APIRouter, Depends, HTTPException

log = logging.getLogger(__name__)

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
    取不到就回空陣列——原本回四筆寫死的檢討紀錄（含編造的操作心得）。
    """
    try:
        return _load_real_entries(limit=50)
    except Exception as e:
        log.warning("journal entries failed: %s", e)
        return []


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
    """GET /api/journal/chat/history → ChatMessage[]

    對話歷史尚未持久化，回空陣列。原本回一段寫死的問答，看起來像
    AI 已經跟你談過這些交易。
    """
    return []
