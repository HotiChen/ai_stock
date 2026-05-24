import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.journal import ChatMessage, ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])

_MOCK_RESPONSE_TOKENS = [
    "台積電",
    "今日",
    "技術面",
    "強勁，",
    "外資",
    "連續",
    "5日",
    "買超，",
    "AI",
    "晶片",
    "需求",
    "持續",
    "強勁。",
    "建議",
    "持續",
    "持有，",
    "目標價",
    "1185",
    "仍有",
    "4.4%",
    "上漲",
    "空間。",
]


async def _mock_sse_stream(req: ChatRequest):
    """Simulate SSE streaming response from chat agent."""
    for i, token in enumerate(_MOCK_RESPONSE_TOKENS):
        msg = ChatMessage(
            id=f"chunk-{i:03d}",
            role="assistant",
            content=token,
            ts="2026-05-24T10:00:03+08:00",
            model="claude-sonnet-4-6",
        )
        yield f"data: {json.dumps(msg.model_dump(), ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.05)
    yield "data: [DONE]\n\n"


@router.post("/")
async def chat(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    return StreamingResponse(
        _mock_sse_stream(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
