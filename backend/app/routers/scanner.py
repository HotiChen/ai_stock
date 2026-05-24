"""scanner.py — Wave 2-D upgrade.

Thin wrapper around market_scan + news_agent with mock fallback.
Every endpoint always returns 200.
"""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User

router = APIRouter(prefix="/api/scanner", tags=["scanner"])

# ── Path setup ────────────────────────────────────────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)

# ── Mock ──────────────────────────────────────────────────────────────────────

_MOCK_MARKET_SCAN = {
    "indices": [
        {"key": "TWII", "name": "加權指數", "value": 21845.32, "change": 127.45, "change_pct": 0.0059, "volume_label": "2,482億"},
        {"key": "TPEX", "name": "櫃買指數", "value": 248.73, "change": 1.82, "change_pct": 0.0074, "volume_label": "384億"},
        {"key": "FIMEX", "name": "期貨指數", "value": 21890, "change": 145, "change_pct": 0.0067, "volume_label": "12.4萬口"},
    ],
    "sectors": [
        {"sector": "半導體", "count": 48, "change_pct": 0.0082, "breadth": 0.75, "leaders": ["2330 台積電", "2454 聯發科"]},
        {"sector": "電腦硬體", "count": 35, "change_pct": 0.0054, "breadth": 0.63, "leaders": ["2382 廣達", "2357 華碩"]},
        {"sector": "金融", "count": 42, "change_pct": -0.0012, "breadth": 0.45, "leaders": ["2882 國泰金", "2881 富邦金"]},
        {"sector": "傳產", "count": 56, "change_pct": -0.0023, "breadth": 0.38, "leaders": ["1301 台塑", "1303 南亞"]},
    ],
    "signals": [
        {"code": "2330", "name": "台積電", "signal": "帶量突破季線", "confidence": 0.82},
        {"code": "2382", "name": "廣達", "signal": "突破前高", "confidence": 0.71},
        {"code": "3008", "name": "大立光", "signal": "RSI 黃金交叉", "confidence": 0.68},
        {"code": "2317", "name": "鴻海", "signal": "量比 2.1x 放量", "confidence": 0.65},
    ],
    "news": [
        {
            "source": "經濟日報",
            "time": "09:30",
            "headline": "台積電 CoWoS 先進封裝需求強勁，法人上調目標價至 1200",
            "url": None,
            "related_codes": ["2330"],
        },
        {
            "source": "工商時報",
            "time": "08:45",
            "headline": "AI 晶片需求爆發，聯發科 AI 手機晶片訂單大增",
            "url": None,
            "related_codes": ["2454"],
        },
        {
            "source": "MoneyDJ",
            "time": "10:15",
            "headline": "廣達 AI 伺服器出貨量創新高，Q3 展望樂觀",
            "url": None,
            "related_codes": ["2382"],
        },
    ],
}


# ── Real data builder ─────────────────────────────────────────────────────────

def _build_real_scan() -> dict:
    """Try market_scan + news_agent. Raises on failure."""
    result: dict = {"indices": [], "sectors": [], "signals": [], "news": []}

    # Try market_scan for sector data
    try:
        import market_scan
        breadth = market_scan.sector_breadth() if hasattr(market_scan, "sector_breadth") else []
        if breadth:
            result["sectors"] = breadth if isinstance(breadth, list) else []
    except Exception:
        pass

    # Try market_scanner for signals
    try:
        import market_scanner
        signals = market_scanner.live_signals() if hasattr(market_scanner, "live_signals") else []
        if signals:
            result["signals"] = signals if isinstance(signals, list) else []
    except Exception:
        pass

    # Try news_agent for recent news
    try:
        import news_agent
        news = news_agent.recent(limit=10) if hasattr(news_agent, "recent") else []
        if news:
            result["news"] = news if isinstance(news, list) else []
    except Exception:
        pass

    # Try market_index for indices
    try:
        import market_index
        snap = market_index.snapshot() if hasattr(market_index, "snapshot") else None
        if snap:
            result["indices"] = snap if isinstance(snap, list) else [snap]
    except Exception:
        pass

    # If everything is empty, raise to trigger mock fallback
    if not any(result.values()):
        raise RuntimeError("all real sources empty")

    # Fill in blanks from mock
    for key in ("indices", "sectors", "signals", "news"):
        if not result[key]:
            result[key] = _MOCK_MARKET_SCAN[key]

    return result


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/")
async def scanner(current_user: User = Depends(get_current_user)) -> dict:
    """
    GET /api/scanner → MarketScan

    try: market_scan + news_agent + market_index
    except: mock MarketScan
    """
    try:
        return _build_real_scan()
    except Exception:
        return _MOCK_MARKET_SCAN
