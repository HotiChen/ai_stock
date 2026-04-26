from __future__ import annotations

from datetime import date


def trailing_stop_triggered(
    entry: float, current: float, peak: float, stop_pct: float
) -> bool:
    """Return True if trailing stop should fire.

    Only activates when peak > entry (position is profitable).
    Triggers when current drops stop_pct% from peak.
    """
    if peak <= entry:
        return False
    drop_from_peak = (peak - current) / peak * 100
    return drop_from_peak >= stop_pct


def check_dip_buy(
    current: float, target: float, threshold_pct: float = 0
) -> bool:
    """Return True if current price is at or below target (with optional tolerance)."""
    upper = target * (1 + threshold_pct / 100)
    return current <= upper


def should_periodic_buy(
    last_buy: date, today: date, interval_days: int
) -> bool:
    """Return True if enough days have passed since last periodic buy."""
    return (today - last_buy).days >= interval_days


def calc_position_pnl(
    cost: float, current: float, quantity: float
) -> dict[str, float]:
    """Calculate P&L metrics for a position."""
    cost_value = cost * quantity
    market_value = current * quantity
    pnl_amount = market_value - cost_value
    pnl_pct = (current - cost) / cost * 100 if cost else 0.0
    return {
        "cost_value": cost_value,
        "market_value": market_value,
        "pnl_amount": pnl_amount,
        "pnl_pct": pnl_pct,
    }
