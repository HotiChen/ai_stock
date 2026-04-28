from __future__ import annotations

"""
Emergency halt mechanism.

halt()        → write HALT flag, stop MonitorAgent if running
resume()      → clear HALT flag
is_halted()   → check flag
cancel_all()  → cancel all today's open orders via Shioaji
liquidate_all_positions() → sell all current positions at market price
"""

import os
from datetime import date
from pathlib import Path
from typing import Optional

import shioaji.constant as sc

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


# ── Liquidate all positions ───────────────────────────────────────────────────

def liquidate_all_positions(api) -> dict:
    """
    Fetch all current positions and sell them at market price.
    Returns {"liquidated": [{"code": ..., "quantity": ...}], "failed": [{"code": ..., "error": ...}]}.
    """
    liquidated = []
    failed = []

    try:
        api.update_status()
        positions = api.list_positions(api.stock_account)
    except Exception as e:
        log.error("liquidate_all: failed to fetch positions: %s", e)
        return {"liquidated": [], "failed": [], "error": str(e)}

    for pos in positions:
        try:
            contract = api.Contracts.Stocks.get(pos.code)
            if not contract:
                raise ValueError(f"Contract not found for {pos.code}")

            order = api.Order(
                price=0,
                quantity=pos.quantity,
                action=sc.Action.Sell,
                price_type=sc.StockPriceType.MKT,
                order_type=sc.OrderType.ROD,
                account=api.stock_account
            )
            trade = api.place_order(contract, order)
            log.warning("Liquidated position %s %d shares, trade id: %s", pos.code, pos.quantity, trade.order.id if hasattr(trade, 'order') else '')
            liquidated.append({"code": pos.code, "quantity": pos.quantity})
        except Exception as e:
            failed.append({"code": pos.code, "error": str(e)})
            log.error("Failed to liquidate %s: %s", pos.code, e)

    log.warning("liquidate_all: liquidated=%d failed=%d", len(liquidated), len(failed))
    return {"liquidated": liquidated, "failed": failed}
