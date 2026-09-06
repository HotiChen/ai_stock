"""scanner.py — Wave 2-D upgrade.

Thin wrapper around market_scan + news_agent with mock fallback.
Every endpoint always returns 200.
"""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Depends, HTTPException

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

    # 缺的區塊留空，不用假資料補齊——原本補完之後畫面看起來一應俱全，
    # 卻分不出哪一塊是真的。
    if not any(result.values()):
        raise RuntimeError("所有資料源都沒有回應")

    return result


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/")
async def scanner(current_user: User = Depends(get_current_user)) -> dict:
    """
    GET /api/scanner → MarketScan

    資料取自 market_scan + news_agent + market_index。
    """
    try:
        return _build_real_scan()
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"取不到大盤掃描資料：{e}",
        ) from e
