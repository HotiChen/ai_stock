from __future__ import annotations

"""
Emergency halt mechanism.

halt()        → write HALT flag, stop MonitorAgent if running
resume()      → clear HALT flag
is_halted()   → check flag
cancel_all()  → cancel all today's open orders via Shioaji
"""

import os
from datetime import date
from pathlib import Path
from typing import Optional

from logger import get_logger

log = get_logger(__name__)

_HALT_FILE = Path(os.getenv("HALT_FILE", "data/HALT"))


# ── HALT flag ─────────────────────────────────────────────────────────────────

def halt(reason: str = "manual") -> None:
    _HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HALT_FILE.write_text(f"{date.today().isoformat()} {reason}")
    log.warning("HALT flag set: %s", reason)


def resume() -> None:
    if _HALT_FILE.exists():
        _HALT_FILE.unlink()
    log.info("HALT flag cleared, system resumed")


def is_halted() -> bool:
    return _HALT_FILE.exists()


# ── Cancel all open orders ────────────────────────────────────────────────────

def cancel_all_orders(api) -> dict:
    """
    Cancel all open orders via Shioaji.
    Returns {"cancelled": [order_ids], "failed": [order_ids]}.
    """
    cancelled = []
    failed = []

    try:
        api.update_status()
        trades = api.list_trades()
    except Exception as e:
        log.error("cancel_all: failed to fetch trades: %s", e)
        return {"cancelled": [], "failed": [], "error": str(e)}

    for trade in trades:
        order = trade.order
        status = trade.status.status if hasattr(trade.status, "status") else ""
        if status in ("Submitted", "PendingSubmit", "PreSubmitted"):
            try:
                api.cancel_order(trade)
                cancelled.append(order.id)
                log.warning("Cancelled order %s %s", order.id, getattr(order, 'code', ''))
            except Exception as e:
                failed.append(order.id)
                log.error("Failed to cancel %s: %s", order.id, e)

    log.warning("cancel_all: cancelled=%d failed=%d", len(cancelled), len(failed))
    return {"cancelled": cancelled, "failed": failed}
