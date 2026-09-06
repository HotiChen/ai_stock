"""
dashboard.py — Dashboard aggregate Router (Wave 2-C)

聚合：KPI（daily_tracker）、top_picks（predict/today）、recent_alerts（research_db.alerts）。
沒有資料的欄位回 None／空陣列，不以假資料充數。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime

from fastapi import APIRouter, Depends, Response

from ..deps import get_current_user
from ..schemas.auth import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# ── 路徑注入 ────────────────────────────────────────────────────────────────

_AI_STOCK_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../")
)
if _AI_STOCK_ROOT not in sys.path:
    sys.path.insert(0, _AI_STOCK_ROOT)

_DB_PATH = os.path.join(_AI_STOCK_ROOT, "data", "research.db")
_TRACK_DIR = os.path.join(_AI_STOCK_ROOT, "data", "daily_tracking")

# ── Mock 資料 ────────────────────────────────────────────────────────────────

_TODAY = date.today().isoformat()

# ── 真實資料輔助 ─────────────────────────────────────────────────────────────

def _load_kpi_from_tracker() -> dict | None:
    """從 daily_tracker 讀取今日 KPI。"""
    try:
        from daily_tracker import load_day_record

        record = load_day_record(date.today(), track_dir=_TRACK_DIR)
        if record is None:
            return None
        total_pnl = sum(
            (r.pnl or 0.0) for r in record.results if r.pnl is not None
        )
        win_trades = sum(r.win_trades for r in record.results)
        loss_trades = sum(r.loss_trades for r in record.results)
        total_trades = win_trades + loss_trades
        win_rate = win_trades / total_trades if total_trades > 0 else 0.0
        net_value = record.starting_capital + total_pnl
        return {
            "net_value": round(net_value),
            "net_pnl": round(total_pnl),
            "net_pnl_pct": round(total_pnl / record.starting_capital, 6) if record.starting_capital else 0.0,
            "realized_pnl": round(total_pnl),
            "unrealized_pnl": 0,
            "win_rate": round(win_rate, 4),
            "trades_today": total_trades,
        }
    except Exception as e:
        log.warning("_load_kpi_from_tracker() failed: %s", e)
        return None


def _load_recent_alerts(limit: int = 10) -> list[dict] | None:
    """從 research_db 讀取最近 alerts。"""
    try:
        from research_db import load_pending_alerts

        alerts = load_pending_alerts(_DB_PATH)
        result = []
        for a in alerts[-limit:]:
            result.append(
                {
                    "id": str(a.get("id", "")),
                    "time": str(a.get("created_at", ""))[-8:] if a.get("created_at") else "",
                    "level": str(a.get("severity", "low")),
                    "code": str(a.get("code", "")),
                    "name": str(a.get("name", "")),
                    "text": str(a.get("message", "")),
                    "kind": str(a.get("alert_type", "note")),
                    "resolved": False,
                    "source": "research_db",
                    "telegram_sent": False,
                }
            )
        return result if result else None
    except Exception as e:
        log.warning("_load_recent_alerts() failed: %s", e)
        return None


def _load_top_picks() -> list[dict]:
    """從 research_db 讀取今日 daily_plan picks（輕量版，只取前 3 欄位）。"""
    try:
        from research_db import load_daily_plan

        rows = load_daily_plan(date.today(), _DB_PATH)
        result = []
        for r in rows[:8]:  # 最多 8 筆
            result.append(
                {
                    "code": str(r.get("code", "")),
                    "name": str(r.get("name", "")),
                    "signal": str(r.get("signal", "hold")).lower(),
                    "confidence": float(r.get("confidence", 0)) / 10.0
                    if float(r.get("confidence", 0)) > 1.0
                    else float(r.get("confidence", 0)),
                    "action": str(r.get("action", "pending")),
                }
            )
        return result
    except Exception as e:
        log.warning("_load_top_picks() failed: %s", e)
        return []


def _load_mode() -> str:
    """讀取 config.SHIOAJI_SIMULATION 或 env 決定模式。"""
    try:
        import os as _os
        sim = _os.environ.get("SHIOAJI_SIMULATION", "true")
        return "simulation" if sim.lower() in ("true", "1", "yes") else "live"
    except Exception:
        return "simulation"


# ── Endpoint ─────────────────────────────────────────────────────────────────


@router.get("/")
async def dashboard(
    response: Response,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Dashboard aggregate：KPI + chart_data + top_picks + recent_alerts。"""
    # 每一項各自可能沒有資料。原本任一項缺就換上寫死的假值（台積電 82%、
    # 假的 KPI、假的走勢圖），而且只在 header 標記——前端從來沒讀那個 header。
    # 現在缺的就是 None／空陣列，由前端顯示「—」或空狀態。
    kpi = _load_kpi_from_tracker()
    top_picks = _load_top_picks() or []
    recent_alerts = _load_recent_alerts() or []
    mode = _load_mode()

    return {
        "date": _TODAY,
        "mode": mode,
        "kpi": kpi,
        # 走勢圖後端尚未實作（spec/BACKEND_MAPPING 未定義這個端點）
        "chart_data": None,
        "top_picks": top_picks,
        "recent_alerts": recent_alerts,
        "market": None,
        "alerts_count": len(recent_alerts),
        "positions_count": 0,
    }
