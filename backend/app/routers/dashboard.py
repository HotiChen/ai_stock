"""
dashboard.py — Dashboard aggregate Router (Wave 2-C)

聚合：KPI（daily_tracker）、top_picks（predict/today）、recent_alerts（research_db.alerts）。
若 ai_stock 模組不可用，降級回 mock 資料並加 X-Data-Source: mock header。
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
    os.path.join(os.path.dirname(__file__), "../../../../")
)
if _AI_STOCK_ROOT not in sys.path:
    sys.path.insert(0, _AI_STOCK_ROOT)

_DB_PATH = os.path.join(_AI_STOCK_ROOT, "ai_stock", "data", "research.db")
_TRACK_DIR = os.path.join(_AI_STOCK_ROOT, "ai_stock", "data", "daily_tracking")

# ── Mock 資料 ────────────────────────────────────────────────────────────────

_TODAY = date.today().isoformat()

_MOCK_KPI = {
    "net_value": 1_034_200,
    "net_pnl": 34_200,
    "net_pnl_pct": 0.0342,
    "realized_pnl": 12_500,
    "unrealized_pnl": 21_700,
    "win_rate": 0.68,
    "trades_today": 3,
}

_MOCK_CHART_DATA = {
    "dates": [_TODAY],
    "equity": [1_034_200],
    "index": [21845],
}

_MOCK_ALERTS = [
    {
        "id": "mock-001",
        "time": "09:32:11",
        "level": "high",
        "code": "2330",
        "name": "台積電",
        "text": "突破前高，量能放大",
        "kind": "note",
        "resolved": False,
        "source": "mock",
        "telegram_sent": False,
    }
]

_MOCK_MARKET = {
    "taiex": {"value": 21845.32, "change": 127.45, "change_pct": 0.0059},
    "otc": {"value": 248.73, "change": 1.82, "change_pct": 0.0074},
}


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
    is_mock = False

    # 1. KPI
    kpi = _load_kpi_from_tracker()
    if kpi is None:
        kpi = _MOCK_KPI
        is_mock = True

    # 2. Top picks（輕量版）
    top_picks = _load_top_picks()
    if not top_picks:
        # 用 mock picks 摘要
        top_picks = [
            {"code": "2330", "name": "台積電", "signal": "buy", "confidence": 0.82, "action": "approved"},
            {"code": "2454", "name": "聯發科", "signal": "buy", "confidence": 0.76, "action": "approved"},
            {"code": "2382", "name": "廣達", "signal": "buy", "confidence": 0.71, "action": "pending"},
        ]
        is_mock = True

    # 3. Recent alerts
    recent_alerts = _load_recent_alerts()
    if recent_alerts is None:
        recent_alerts = _MOCK_ALERTS
        is_mock = True

    # 4. Mode
    mode = _load_mode()

    if is_mock:
        response.headers["X-Data-Source"] = "mock"

    return {
        "date": _TODAY,
        "mode": mode,
        "kpi": kpi,
        "chart_data": _MOCK_CHART_DATA,   # 週期性圖表資料由 portfolio router 提供
        "top_picks": top_picks,
        "recent_alerts": recent_alerts,
        "market": _MOCK_MARKET,
        "alerts_count": len(recent_alerts),
        "positions_count": 0,
    }
