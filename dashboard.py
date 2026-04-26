from __future__ import annotations

from dataclasses import dataclass

from strategies import calc_position_pnl
from trades import TradeRecord, calc_win_rate


@dataclass
class DashboardData:
    total_cost: float
    total_market_value: float
    total_pnl: float
    total_pnl_pct: float
    position_count: int
    active_alerts: int
    latest_ai_action: str
    latest_ai_reason: str
    win_rate: float
    cash: float


def get_portfolio_summary(positions: list[dict]) -> dict:
    """Aggregate P&L across all positions."""
    if not positions:
        return {
            "total_cost": 0.0, "total_market_value": 0.0,
            "total_pnl": 0.0, "total_pnl_pct": 0.0, "position_count": 0,
        }

    total_cost = total_market = 0.0
    for pos in positions:
        pnl = calc_position_pnl(pos["cost"], pos["current"], pos["quantity"])
        total_cost += pnl["cost_value"]
        total_market += pnl["market_value"]

    total_pnl = total_market - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0

    return {
        "total_cost": total_cost,
        "total_market_value": total_market,
        "total_pnl": total_pnl,
        "total_pnl_pct": round(total_pnl_pct, 2),
        "position_count": len(positions),
    }


def build_dashboard(
    positions: list[dict],
    trades: list[TradeRecord],
    alert_count: int,
    ai_log_entry: dict | None,
    cash: float,
) -> DashboardData:
    portfolio = get_portfolio_summary(positions)
    win_rate = calc_win_rate(trades)

    if ai_log_entry:
        d = ai_log_entry.get("decision", {})
        latest_action = d.get("action", "—")
        latest_reason = d.get("reason", "—")
    else:
        latest_action = "—"
        latest_reason = "尚無記錄"

    return DashboardData(
        total_cost=portfolio["total_cost"],
        total_market_value=portfolio["total_market_value"],
        total_pnl=portfolio["total_pnl"],
        total_pnl_pct=portfolio["total_pnl_pct"],
        position_count=portfolio["position_count"],
        active_alerts=alert_count,
        latest_ai_action=latest_action,
        latest_ai_reason=latest_reason,
        win_rate=win_rate,
        cash=cash,
    )
