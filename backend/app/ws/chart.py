"""ws/chart.py — Wave 2-D upgrade.

WebSocket /ws/daytrade/{code}/chart: push ChartView with ticks every 5 s.
Tries to use daytrading_analyzer for real data; falls back to mock tick stream.
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import datetime, timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

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

_BASE_TICKS = [
    Tick(t="09:00", open=1120, high=1122, low=1118, close=1121, volume=1250),
    Tick(t="09:30", open=1121, high=1128, low=1120, close=1126, volume=1480),
    Tick(t="10:00", open=1126, high=1135, low=1125, close=1132, volume=1820),
    Tick(t="10:30", open=1132, high=1142, low=1130, close=1135, volume=2100),
]


def _next_tick(last_tick: Tick, idx: int) -> Tick:
    """Generate the next mock tick based on the last one."""
    base_time = datetime.strptime(last_tick.t, "%H:%M")
    next_time = base_time + timedelta(minutes=5)
    if next_time.hour >= 14:
        next_time = datetime.strptime("09:00", "%H:%M")

    last_close = last_tick.close
    random.seed(idx)
    delta = random.uniform(-8, 10)
    new_close = round(last_close + delta, 0)
    new_open = last_close
    new_high = max(new_open, new_close) + random.uniform(0, 5)
    new_low = min(new_open, new_close) - random.uniform(0, 5)
    return Tick(
        t=next_time.strftime("%H:%M"),
        open=new_open,
        high=round(new_high, 0),
        low=round(new_low, 0),
        close=new_close,
        volume=random.randint(800, 2500),
    )


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
    await websocket.accept()
    ticks = list(_BASE_TICKS)
    tick_idx = len(ticks)
    try:
        # Try to seed with real ticks
        real = _fetch_real_ticks(code)
        if real:
            ticks = real
            tick_idx = len(ticks)

        # Send initial chart view
        await websocket.send_json(_build_chart_view(code, ticks))

        while True:
            await asyncio.sleep(5)
            # Try to refresh from real source each cycle
            real = _fetch_real_ticks(code)
            if real:
                ticks = real
            else:
                new_tick = _next_tick(ticks[-1], tick_idx)
                ticks.append(new_tick)
                tick_idx += 1
                # Keep only last 60 ticks for memory
                if len(ticks) > 60:
                    ticks = ticks[-60:]

            await websocket.send_json(_build_chart_view(code, ticks))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
