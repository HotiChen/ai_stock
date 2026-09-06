"""chat.py — Wave 4-I upgrade.

POST /api/chat   SSE streaming response using chat_agent.call_anthropic_chat
                 失敗時把錯誤訊息串回去，不回假答案。
GET  /api/chat/history  → ChatMessage[] from learning_db（取不到回空陣列）。
"""
from __future__ import annotations

import logging

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends

log = logging.getLogger(__name__)
from fastapi.responses import StreamingResponse

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.journal import ChatMessage, ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])

# ── Path setup ────────────────────────────────────────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)

# ── Helpers ───────────────────────────────────────────────────────────────────

_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat()


def _make_id() -> str:
    return f"msg-{uuid.uuid4().hex[:8]}"


# ── Mock fallback ─────────────────────────────────────────────────────────────

# ── Real SSE stream ───────────────────────────────────────────────────────────

async def _real_stream(req: ChatRequest):
    """Call chat_agent.call_anthropic_chat in a thread, then stream result char-by-char."""
    import chat_agent as _ca

    # Build chat_agent.ChatMessage list from request
    ca_messages: list[_ca.ChatMessage] = []
    for m in req.messages:
        if m.role in ("user", "assistant", "system"):
            ca_messages.append(_ca.ChatMessage(role=m.role, content=m.content))

    # Ensure there's at least a system prompt if no system message provided
    has_system = any(m.role == "system" for m in ca_messages)
    if not has_system:
        system_prompt = _ca.build_trading_advisor_prompt()
        ca_messages.insert(0, _ca.ChatMessage(role="system", content=system_prompt))

    # Call synchronous Anthropic chat in a thread to avoid blocking
    reply_text: str = await asyncio.to_thread(
        _ca.call_anthropic_chat, ca_messages
    )

    if not reply_text:
        raise RuntimeError("empty reply from chat_agent")

    msg_id = _make_id()
    ts = _now_iso()
    model = req.model or "claude-sonnet-4-6"

    # Stream the reply character by character
    for i, char in enumerate(reply_text):
        chunk = ChatMessage(
            id=f"{msg_id}-{i:04d}",
            role="assistant",
            content=char,
            ts=ts,
            model=model,
        )
        yield f"data: {json.dumps(chunk.model_dump(), ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.02)

    yield "data: [DONE]\n\n"


async def _sse_generator(req: ChatRequest):
    """真實 agent 失敗時把錯誤說出來。

    原本降級到 _mock_stream，於是 AI 顧問會回一段寫好的假答案——
    使用者以為 AI 讀過他的交易紀錄，實際上沒有。
    """
    try:
        async for chunk in _real_stream(req):
            yield chunk
    except Exception as e:
        log.warning("chat stream failed: %s", e)
        msg = f"AI 顧問目前不可用：{e}"
        yield f"data: {json.dumps({'delta': msg}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"


# ── Real chat history ─────────────────────────────────────────────────────────

def _load_real_history(limit: int = 20) -> list[ChatMessage]:
    """Load recent prediction logs from learning_db as pseudo chat history."""
    import learning_db
    db = learning_db.LearningDB()
    from datetime import date
    preds = db.get_predictions_by_date(date.today())
    if not preds:
        # Fall back to last 3 days if today empty
        from datetime import timedelta
        end = date.today()
        start = end - timedelta(days=3)
        preds = db.get_predictions_by_date_range(start, end)

    history: list[ChatMessage] = []
    for i, p in enumerate(preds[:limit]):
        history.append(ChatMessage(
            id=f"msg-pred-{i:03d}",
            role="assistant",
            content=(
                f"[{p.date}] {p.name}（{p.code}）"
                f"動作：{p.action}　信心：{p.confidence}/10　"
                f"預期報酬：{p.expected_return_pct:+.1f}%"
                + (f"　實際：{p.actual_return_pct:+.1f}%" if p.actual_return_pct is not None else "")
            ),
            ts=str(p.date) + "T08:30:00+08:00",
            model="learning_db",
        ))
    if not history:
        raise RuntimeError("no history")
    return history


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/")
async def chat(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """
    POST /api/chat → SSE streaming ChatMessage chunks.

    try: chat_agent.call_anthropic_chat (in asyncio.to_thread)
    except: 串回錯誤訊息
    """
    return StreamingResponse(
        _sse_generator(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history", response_model=list[ChatMessage])
async def chat_history(
    current_user: User = Depends(get_current_user),
) -> list[ChatMessage]:
    """
    GET /api/chat/history → ChatMessage[]

    取不到就回空陣列——原本回一段寫死的問答。
    """
    try:
        return _load_real_history(limit=20)
    except Exception as e:
        log.warning("chat history failed: %s", e)
        return []
