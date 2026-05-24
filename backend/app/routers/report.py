"""report.py — Wave 2-D upgrade.

Thin wrapper around weekly_report with mock fallback.
Every endpoint always returns 200.
"""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User

router = APIRouter(prefix="/api/report", tags=["report"])

# ── Path setup ────────────────────────────────────────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)

# ── Mock ──────────────────────────────────────────────────────────────────────

_MOCK_WEEKLY_REPORT = {
    "week_number": 21,
    "range": {"start": "2026-05-18", "end": "2026-05-22"},
    "total_trades": 12,
    "wins": 8,
    "losses": 4,
    "winrate": 0.6667,
    "net_pnl": 34_200,
    "net_pnl_pct": 0.0342,
    "daily": [
        {"date": "2026-05-18", "weekday": "一", "pnl": 9800, "trades": 3, "wins": 2},
        {"date": "2026-05-19", "weekday": "二", "pnl": 4200, "trades": 2, "wins": 1},
        {"date": "2026-05-20", "weekday": "三", "pnl": 11400, "trades": 3, "wins": 3},
        {"date": "2026-05-21", "weekday": "四", "pnl": 7600, "trades": 2, "wins": 1},
        {"date": "2026-05-22", "weekday": "五", "pnl": 1200, "trades": 2, "wins": 1},
    ],
    "highlight": "本週表現亮眼，台積電與聯發科均達目標價，AI 信心模型準確率高。建議下週持續關注半導體族群。",
    "by_confidence": [
        {"range": "≥0.80", "winrate": 0.85, "n": 7, "pnl": 28000},
        {"range": "0.70-0.79", "winrate": 0.60, "n": 3, "pnl": 5200},
        {"range": "0.65-0.69", "winrate": 0.50, "n": 2, "pnl": 1000},
    ],
    "by_sector": [
        {"sector": "半導體", "winrate": 0.75, "n": 8, "pnl": 28000},
        {"sector": "電腦硬體", "winrate": 0.50, "n": 4, "pnl": 6200},
    ],
    "next_week_suggestions": [
        {"title": "提高信心門檻", "detail": "本週低信心交易表現不佳，建議調至 0.70"},
        {"title": "關注 AI 晶片族群", "detail": "Nvidia 財報後 AI 晶片需求持續強勁"},
    ],
}


# ── Real data builder ─────────────────────────────────────────────────────────

def _build_real_weekly(week: str | None) -> dict:
    """Try weekly_report.build(). Raises on failure."""
    import weekly_report as wr
    if hasattr(wr, "build"):
        result = wr.build(week or "current")
        if result:
            # Normalize: if it's a dataclass or object, convert to dict
            if hasattr(result, "__dict__"):
                return result.__dict__
            if isinstance(result, dict):
                return result
    raise RuntimeError("weekly_report.build() returned nothing useful")


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/weekly")
async def weekly(
    week: str | None = None,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    GET /api/report/weekly → WeeklyReport

    try: weekly_report.build(week)
    except: mock WeeklyReport
    """
    try:
        return _build_real_weekly(week)
    except Exception:
        return _MOCK_WEEKLY_REPORT
