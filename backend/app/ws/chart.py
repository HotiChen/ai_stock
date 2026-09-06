"""ws/chart.py — Wave 2-D upgrade.

WebSocket /ws/daytrade/{code}/chart: push ChartView with ticks every 5 s.
只推真實 tick。取不到就明說，不生成隨機走勢。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..deps import authenticate_ws_token
from ..schemas.daytrade import AIMark, ChartView
from ..schemas.predict import Tick

router = APIRouter(tags=["ws-chart"])

# ── Path setup ────────────────────────────────────────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)

# ── Mock tick helpers ─────────────────────────────────────────────────────────

def _build_chart_view(code: str, ticks: list[Tick]) -> dict:
    closes = [t.close for t in ticks]
    n = len(closes)
    ma5 = [sum(closes[max(0, i - 4):i + 1]) / min(i + 1, 5) for i in range(n)]
    ma20 = [sum(closes[max(0, i - 19):i + 1]) / min(i + 1, 20) for i in range(n)]

    view = ChartView(
        code=code,
        ticks=ticks,
        ma20=ma20,
        ma5=ma5,
        bollinger={
            "upper": [c + 20 for c in closes],
            "mid": closes[:],
            "lower": [c - 20 for c in closes],
        },
        rsi=[52.0 + i * 0.5 for i in range(n)],
        ai_marks=[
            AIMark(
                index=0,
                time=ticks[0].t,
                kind="buy",
                label="AI 買進信號",
                confidence=0.82,
                reasoning="突破前高，量能放大",
            )
        ],
        next_action_suggestion={
            "kind": "hold",
            "text": "持續持有，目標仍有空間",
            "confidence": 0.75,
        },
    )
    return view.model_dump()


def _fetch_real_ticks(code: str) -> list[Tick] | None:
    """Try to fetch real ticks from daytrading_analyzer. Returns None on failure."""
    try:
        import daytrading_analyzer
        result = daytrading_analyzer.analyze(code)
        if not isinstance(result, dict):
            return None
        ticks_raw = result.get("ticks", [])
        if not ticks_raw:
            return None
        ticks = [
            Tick(
                t=r.get("t", "09:00"),
                open=float(r.get("open", 0)),
                high=float(r.get("high", 0)),
                low=float(r.get("low", 0)),
                close=float(r.get("close", 0)),
                volume=int(r.get("volume", 0)),
            )
            for r in ticks_raw
        ]
        return ticks if ticks else None
    except Exception:
        return None


@router.websocket("/ws/daytrade/{code}/chart")
async def ws_chart(websocket: WebSocket, code: str) -> None:
    # Auth via ?token= query param (WS cannot send Authorization header).
    user = authenticate_ws_token(websocket.query_params.get("token"))
    if user is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    # 原本沒有真實 tick 時會用 _next_tick() 以 random.uniform(-8, 10) 生成
    # 下一根 K 棒，每 5 秒推一次——畫面上是一條會動的、完全隨機的假走勢。
    try:
        real = _fetch_real_ticks(code)
        if not real:
            await websocket.send_json({
                "error": "no_data",
                "detail": f"取不到 {code} 的當日 tick。盤前或非交易日不會有。",
            })
            return
        await websocket.send_json(_build_chart_view(code, real))

        while True:
            await asyncio.sleep(5)
            real = _fetch_real_ticks(code)
            if not real:
                await websocket.send_json({
                    "error": "no_data",
                    "detail": f"{code} 的 tick 串流中斷。",
                })
                continue
            await websocket.send_json(_build_chart_view(code, real))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
