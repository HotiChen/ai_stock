"""order_confirm.py — order confirmation state machine (security-critical).

Project rule (CLAUDE.md §二): "每筆下單必經 Telegram 二次確認" — every order MUST
pass Telegram second confirmation BEFORE it can be executed. This service enforces
that with an explicit state machine so an order can never reach the broker without
an affirmative approval.

State machine
-------------
    pending_confirmation ──confirm(true)──▶ confirmed ──execute──▶ (result stored)
                         ──confirm(false)─▶ rejected
                         ──timeout────────▶ expired
                         ──send fails─────▶ failed   (set at submit time)

Transitions are performed under a single threading.Lock; the executor is invoked
AT MOST ONCE per order, guarded by an `executed` flag, so concurrent/duplicate
confirm requests can never double-place an order.

Storage limitation
------------------
The store is a plain in-process dict guarded by a Lock. This is acceptable ONLY
because the app is a single-process single-user workstation (see CLAUDE.md). It
does NOT survive a restart and is NOT shared across workers — if this ever runs
under multiple uvicorn workers, pending orders must move to a shared store
(Redis / sqlite). Documented here so the limitation is explicit.

Telegram integration gap
------------------------
The real Telegram callback (the user pressing the inline approve/reject button in
Telegram) is handled by the separately-running `telegram_bot.py` process, which we
must NOT modify. That process currently has no channel back into this in-process
store. The `/api/order/{id}/confirm` HTTP endpoint backed by this service is the
*web-side* approval path. Wiring the Telegram bot's callback to this store (e.g. by
having the bot POST to /confirm, or by sharing a persistent store) is a known
follow-up integration item, intentionally out of scope for this change.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

# OrderTicket is a pydantic model; we only store/forward it, never mutate ai_stock.
from ..schemas.order import OrderTicket

OrderState = Literal[
    "pending_confirmation",
    "confirmed",
    "rejected",
    "expired",
    "failed",
]

DEFAULT_TIMEOUT_SECONDS = 60


def _timeout_seconds() -> int:
    try:
        return int(os.getenv("ORDER_CONFIRM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


@dataclass
class PendingOrder:
    id: str
    ticket: OrderTicket
    state: OrderState = "pending_confirmation"
    created_at: float = field(default_factory=time.time)
    telegram_message_id: Optional[str] = None
    # `executed` guards the executor call so it happens at most once, ever.
    executed: bool = False
    # Cached terminal result payload (dict) for idempotent re-reads.
    result: Optional[dict] = None


class _OrderStore:
    """In-process, thread-safe store of pending/terminal orders."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._orders: dict[str, PendingOrder] = {}

    # ── creation ────────────────────────────────────────────────────────────
    def create(self, ticket: OrderTicket, telegram_message_id: Optional[str]) -> PendingOrder:
        order_id = f"ord-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        rec = PendingOrder(id=order_id, ticket=ticket, telegram_message_id=telegram_message_id)
        with self._lock:
            self._orders[order_id] = rec
        return rec

    def mark_failed(self, ticket: OrderTicket, reason: str) -> PendingOrder:
        order_id = f"ord-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        rec = PendingOrder(id=order_id, ticket=ticket, state="failed")
        rec.result = {"rejection_reason": reason}
        with self._lock:
            self._orders[order_id] = rec
        return rec

    # ── reads ───────────────────────────────────────────────────────────────
    def get_order(self, order_id: str) -> Optional[PendingOrder]:
        with self._lock:
            return self._orders.get(order_id)

    def find_pending_by_code(self, code: str) -> Optional[PendingOrder]:
        """Return the single most-recent *pending_confirmation* order for `code`.

        Used by the Telegram bridge: the inline approve/reject buttons only carry
        a stock code, so we resolve it back to the order awaiting confirmation.
        Only orders still in `pending_confirmation` are eligible (already-terminal
        orders are ignored). Most-recent wins on the (rare) chance of duplicates.
        """
        with self._lock:
            candidates = [
                rec for rec in self._orders.values()
                if rec.ticket.code == code and rec.state == "pending_confirmation"
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda r: r.created_at)

    # ── transitions ─────────────────────────────────────────────────────────
    def reject(self, order_id: str) -> Optional[PendingOrder]:
        with self._lock:
            rec = self._orders.get(order_id)
            if rec is None:
                return None
            if rec.state == "pending_confirmation":
                rec.state = "rejected"
            return rec

    def claim_for_execution(self, order_id: str) -> tuple[Optional[PendingOrder], str]:
        """Atomically validate a pending order and claim it for execution.

        Returns (record, outcome) where outcome is one of:
            "not_found"  — no such order
            "expired"    — timed out (record state set to expired)
            "claimed"    — caller now owns the single execution slot
            "already"    — already in a terminal state (idempotent no-op)
        Only the "claimed" outcome permits the caller to invoke the executor.
        """
        with self._lock:
            rec = self._orders.get(order_id)
            if rec is None:
                return None, "not_found"

            if rec.state == "pending_confirmation":
                if time.time() - rec.created_at > _timeout_seconds():
                    rec.state = "expired"
                    return rec, "expired"
                # Move to confirmed and claim the (single) execution slot.
                rec.state = "confirmed"
                rec.executed = True
                return rec, "claimed"

            if rec.state == "expired":
                return rec, "expired"

            # confirmed / rejected / failed / (or already executed) → idempotent.
            return rec, "already"

    def store_result(self, order_id: str, result: dict) -> None:
        with self._lock:
            rec = self._orders.get(order_id)
            if rec is not None:
                rec.result = result

    # ── test/maintenance ──────────────────────────────────────────────────────
    def reset(self) -> None:
        with self._lock:
            self._orders.clear()


# Module-level singleton store.
_store = _OrderStore()


# ── module-level convenience API (used by router + tests) ───────────────────
def create_order(ticket: OrderTicket, telegram_message_id: Optional[str]) -> PendingOrder:
    return _store.create(ticket, telegram_message_id)


def create_failed_order(ticket: OrderTicket, reason: str) -> PendingOrder:
    return _store.mark_failed(ticket, reason)


def get_order(order_id: str) -> Optional[PendingOrder]:
    return _store.get_order(order_id)


def find_pending_by_code(code: str) -> Optional[PendingOrder]:
    return _store.find_pending_by_code(code)


def reject_order(order_id: str) -> Optional[PendingOrder]:
    return _store.reject(order_id)


def claim_for_execution(order_id: str) -> tuple[Optional[PendingOrder], str]:
    return _store.claim_for_execution(order_id)


def store_result(order_id: str, result: dict) -> None:
    _store.store_result(order_id, result)


def reset_store() -> None:
    """Clear all state. Intended for tests only."""
    _store.reset()
