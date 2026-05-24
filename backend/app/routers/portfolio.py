"""portfolio.py — Wave 2-D upgrade.

Wraps ai_stock modules (portfolio, daily_tracker) with try/except fallback to mock.
Every endpoint always returns 200.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone, timedelta

from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.base import LotType, Side, ThreadState
from ..schemas.daytrade import Position
from ..schemas.portfolio import PortfolioSummary
from ..schemas.predict import SectorAllocation

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# ── Path setup ────────────────────────────────────────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)

# ── Countdown helper ──────────────────────────────────────────────────────────

def _calc_countdown_seconds() -> int:
    try:
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        force_close = now.replace(hour=13, minute=25, second=0, microsecond=0)
        return max(0, int((force_close - now).total_seconds()))
    except Exception:
        return 9900

# ── Mock ──────────────────────────────────────────────────────────────────────

def _mock_portfolio() -> PortfolioSummary:
    positions = [
        Position(
            code="2330",
            name="台積電",
            sector="半導體",
            side=Side.BUY,
            entry_price=1120,
            last_price=1135,
            quantity=1,
            lot=LotType.COMMON,
            cost=112_000,
            market_value=113_500,
            pnl=1500,
            pnl_pct=0.0134,
            target_price=1185,
            stop_loss_price=1085,
            distance_to_tp_pct=0.044,
            distance_to_sl_pct=0.044,
            thread_state=ThreadState.MONITORING,
            confidence=0.82,
            opened_at="2026-05-24T09:05:32+08:00",
        ),
        Position(
            code="2454",
            name="聯發科",
            sector="半導體",
            side=Side.BUY,
            entry_price=1295,
            last_price=1315,
            quantity=1,
            lot=LotType.COMMON,
            cost=129_500,
            market_value=131_500,
            pnl=2000,
            pnl_pct=0.0154,
            target_price=1380,
            stop_loss_price=1260,
            distance_to_tp_pct=0.049,
            distance_to_sl_pct=0.042,
            thread_state=ThreadState.MONITORING,
            confidence=0.76,
            opened_at="2026-05-24T09:12:18+08:00",
        ),
    ]
    return PortfolioSummary(
        net_value=1_034_200,
        budget=1_000_000,
        free_cash=755_000,
        cash_ratio=0.755,
        unrealized_pnl=3_500,
        realized_pnl=12_500,
        net_pnl=16_000,
        net_pnl_pct=0.016,
        position_count=2,
        closed_today=1,
        countdown_seconds=_calc_countdown_seconds(),
        positions=positions,
        sector_breakdown=[
            SectorAllocation(name="半導體", ratio=0.245, limit=0.4, value=245_000),
        ],
        recent_pnl_days=[
            {"date": "2026-05-10", "pnl": 8200},
            {"date": "2026-05-11", "pnl": -3400},
            {"date": "2026-05-12", "pnl": 12000},
            {"date": "2026-05-13", "pnl": 5600},
            {"date": "2026-05-14", "pnl": -1200},
            {"date": "2026-05-17", "pnl": 9800},
            {"date": "2026-05-18", "pnl": 4200},
            {"date": "2026-05-19", "pnl": -2800},
            {"date": "2026-05-20", "pnl": 11400},
            {"date": "2026-05-21", "pnl": 7600},
            {"date": "2026-05-22", "pnl": 3200},
            {"date": "2026-05-23", "pnl": 8900},
            {"date": "2026-05-24", "pnl": 16000},
        ],
        cumulative_vs_index={
            "dates": ["2026-05-10", "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14"],
            "me": [1.0, 0.997, 1.009, 1.015, 1.014],
            "index": [1.0, 0.998, 1.005, 1.008, 1.007],
        },
        alpha_mtd=0.009,
    )


# ── Real data builder ─────────────────────────────────────────────────────────

def _build_real_portfolio() -> PortfolioSummary:
    """Try to load real data from portfolio + daily_tracker. Raises on failure."""
    import portfolio as pf_mod
    import daily_tracker

    capital = float(os.getenv("BUDGET", "1000000"))
    portfolio_obj = pf_mod.SimulatedPortfolio(initial_capital=capital)
    raw_positions = portfolio_obj.get_positions()

    positions: list[Position] = []
    total_unrealized = 0.0
    total_cost = 0.0
    sector_map: dict[str, float] = {}

    for p in raw_positions:
        cur_price = p.current_price if p.current_price else p.avg_cost
        market_val = p.market_value(cur_price)
        pnl = p.unrealized_pnl(cur_price)
        pnl_pct = p.unrealized_pnl_pct(cur_price) / 100.0
        total_unrealized += pnl
        total_cost += p.total_cost

        positions.append(Position(
            code=p.code,
            name=p.name,
            sector="未知",
            side=Side.BUY,
            entry_price=p.avg_cost,
            last_price=cur_price,
            quantity=p.quantity,
            lot=LotType.INTRADAY_ODD if p.is_fractional else LotType.COMMON,
            cost=int(p.total_cost),
            market_value=int(market_val),
            pnl=int(pnl),
            pnl_pct=round(pnl_pct, 4),
            target_price=round(p.avg_cost * 1.05, 0),
            stop_loss_price=round(p.avg_cost * 0.97, 0),
            distance_to_tp_pct=0.05,
            distance_to_sl_pct=0.03,
            thread_state=ThreadState.MONITORING,
            confidence=0.70,
            opened_at=p.entry_date.isoformat() + "T09:00:00+08:00",
        ))

    # Realized PnL from daily_tracker
    records = daily_tracker.list_day_records()
    recent_14 = records[:14]  # newest first
    recent_pnl_days = []
    realized_pnl = 0.0
    for r in reversed(recent_14):  # oldest first for the chart
        day_pnl = 0.0
        for res in r.results:
            if res.pnl is not None and res.is_actual:
                day_pnl += res.pnl
                realized_pnl += res.pnl
        recent_pnl_days.append({"date": r.date.isoformat(), "pnl": day_pnl})

    free_cash = max(0.0, capital - total_cost)
    net_value = capital + total_unrealized + realized_pnl
    net_pnl = total_unrealized + realized_pnl
    net_pnl_pct = net_pnl / capital if capital else 0.0

    return PortfolioSummary(
        net_value=int(net_value),
        budget=int(capital),
        free_cash=int(free_cash),
        cash_ratio=round(free_cash / capital, 4) if capital else 0.0,
        unrealized_pnl=int(total_unrealized),
        realized_pnl=int(realized_pnl),
        net_pnl=int(net_pnl),
        net_pnl_pct=round(net_pnl_pct, 4),
        position_count=len(positions),
        closed_today=0,
        countdown_seconds=_calc_countdown_seconds(),
        positions=positions,
        sector_breakdown=[],
        recent_pnl_days=recent_pnl_days or [{"date": date.today().isoformat(), "pnl": 0}],
        cumulative_vs_index={
            "dates": [date.today().isoformat()],
            "me": [1.0],
            "index": [1.0],
        },
        alpha_mtd=0.0,
    )


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/", response_model=PortfolioSummary)
async def portfolio(current_user: User = Depends(get_current_user)) -> PortfolioSummary:
    """
    GET /api/portfolio → PortfolioSummary

    try: portfolio.load_current_positions() + daily_tracker.last_n_days(14)
    except: mock PortfolioSummary with real countdown_seconds
    """
    try:
        return _build_real_portfolio()
    except Exception:
        return _mock_portfolio()
