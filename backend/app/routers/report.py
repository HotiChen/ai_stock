"""report.py — Wave 2-D upgrade.

Thin wrapper around weekly_report. 沒有資料回 404，不以假資料充數。
Every endpoint always returns 200.
"""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Depends, HTTPException

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

    週報由 13:35 收盤複盤累積產生。沒有足夠資料就回 404——原本回一份
    寫死的週報（含編好的敘事文字與 +18,420 損益）。
    """
    try:
        return _build_real_weekly(week)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"尚無週報資料：{e}",
        ) from e
