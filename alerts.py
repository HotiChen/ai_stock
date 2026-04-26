from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AlertDirection(Enum):
    ABOVE = "above"
    BELOW = "below"


@dataclass
class Alert:
    stock_code: str
    price: float
    direction: AlertDirection
    enabled: bool = True
    note: str = ""


def check_price_alert(alert: Alert, current_price: float) -> bool:
    """Return True if the alert condition is met."""
    if not alert.enabled:
        return False
    if alert.direction == AlertDirection.ABOVE:
        return current_price >= alert.price
    return current_price <= alert.price


def filter_triggered_alerts(
    alerts: list[Alert], prices: dict[str, float]
) -> list[Alert]:
    """Return alerts whose conditions are currently met."""
    triggered = []
    for alert in alerts:
        current = prices.get(alert.stock_code)
        if current is not None and check_price_alert(alert, current):
            triggered.append(alert)
    return triggered
