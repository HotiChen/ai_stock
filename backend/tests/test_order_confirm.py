"""Tests for the order confirmation state machine (security-critical).

Every order MUST pass Telegram second confirmation before execution.
These tests are written TDD-style: they encode the required behaviour of
the /api/order/submit + /api/order/{id}/confirm + /api/order/{id}/status flow.

Run with:
    cd /home/user/ai_stock && python -m pytest backend/tests/test_order_confirm.py -q
"""
from __future__ import annotations

import os
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Make `backend` importable as a package root.
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_BACKEND_DIR, ".."))
for p in (_REPO_ROOT, _BACKEND_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def order_env():
    """Import the FastAPI app + service + router fresh each test."""
    from backend.app.main import app
    from backend.app.schemas.auth import User
    from backend.app.services import order_confirm
    from backend.app.routers import order as order_router
    import backend.app.deps as deps_mod

    # Reset the in-process store between tests.
    order_confirm.reset_store()

    async def _fake_user() -> User:
        return User(
            id="single-user",
            email="test@quant.ai",
            display_name="test",
            shioaji_bound=False,
            telegram_bound=False,
            two_factor_enabled=False,
        )

    # NOTE: another test module (test_auth.py) calls importlib.reload on
    # backend.app.deps, which replaces the get_current_user function object.
    # The order router captured its own reference at import time, so we override
    # *both* the router's bound reference and the (possibly reloaded) deps one to
    # be robust regardless of test execution order.
    keys = {order_router.get_current_user, deps_mod.get_current_user}
    for key in keys:
        app.dependency_overrides[key] = _fake_user
    try:
        yield {
            "app": app,
            "order_router": order_router,
            "order_confirm": order_confirm,
        }
    finally:
        for key in keys:
            app.dependency_overrides.pop(key, None)


@pytest.fixture()
def oc_client(order_env):
    return TestClient(order_env["app"])


def _ticket(**overrides: Any) -> dict:
    base = {
        "code": "2330",
        "name": "台積電",
        "last_price": 1135,
        "side": "buy",
        "lot": "common",
        "price_type": "LMT",
        "price": 1135,
        "quantity": 1,
        "amount": 113500,
        "target_price": 1185,
        "stop_loss_price": 1085,
        "source": {"type": "manual"},
        "risk_checks": [],
        "mode": "simulation",
        "dry_run_preview": "place_order(...) [SIMULATION]",
    }
    base.update(overrides)
    return base


class _FakeExecResult:
    def __init__(self, success: bool = True):
        self.success = success
        self.order_id = "exec-001"
        self.price = 1135.0
        self.amount = 113500.0
        self.reason = "ok" if success else "rejected by broker"


# ── 1. Telegram not configured → 503, executor never called ─────────────────


def test_submit_without_telegram_chat_id_returns_503(oc_client, monkeypatch, order_env):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    send = pytest.importorskip("user_confirm")
    calls = {"send": 0, "exec": 0}
    monkeypatch.setattr(send, "send_confirmation", lambda *a, **k: calls.__setitem__("send", calls["send"] + 1) or 1)
    import executor
    monkeypatch.setattr(executor, "place_stock_order", lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult())

    resp = oc_client.post("/api/order/submit", json=_ticket())
    assert resp.status_code == 503, resp.text
    assert "telegram" in resp.text.lower() or "confirm" in resp.text.lower()
    assert calls["exec"] == 0  # executor NEVER called


# ── 2. Telegram configured → pending, send once, no execute ─────────────────


def test_submit_creates_pending_and_sends_once(oc_client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    import user_confirm
    import executor
    calls = {"send": 0, "exec": 0}
    monkeypatch.setattr(user_confirm, "send_confirmation",
                        lambda *a, **k: calls.__setitem__("send", calls["send"] + 1) or 999)
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult())

    resp = oc_client.post("/api/order/submit", json=_ticket())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending_confirmation"
    assert body["order_id"]
    assert calls["send"] == 1   # exactly once
    assert calls["exec"] == 0   # NOT executed yet


# ── 3. send_confirmation raises → failed, 502, executor never called ────────


def test_submit_send_failure_marks_failed_and_does_not_execute(oc_client, monkeypatch, order_env):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    import user_confirm
    import executor
    calls = {"exec": 0}

    def _boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(user_confirm, "send_confirmation", _boom)
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult())

    resp = oc_client.post("/api/order/submit", json=_ticket())
    assert resp.status_code == 502, resp.text
    assert calls["exec"] == 0


# ── 4. confirm=true → executes; confirm=false → rejected, no execute ────────


def test_confirm_true_executes_order(oc_client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("PAPER_TRADING", "false")  # force real executor path

    import user_confirm
    import executor
    calls = {"exec": 0}
    monkeypatch.setattr(user_confirm, "send_confirmation", lambda *a, **k: 999)
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult(True))

    sub = oc_client.post("/api/order/submit", json=_ticket())
    oid = sub.json()["order_id"]

    conf = oc_client.post(f"/api/order/{oid}/confirm", json={"confirmed": True})
    assert conf.status_code == 200, conf.text
    body = conf.json()
    assert body["status"] in ("filled", "confirmed")
    assert calls["exec"] == 1


def test_confirm_false_rejects_without_executing(oc_client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    import user_confirm
    import executor
    calls = {"exec": 0}
    monkeypatch.setattr(user_confirm, "send_confirmation", lambda *a, **k: 999)
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult())

    sub = oc_client.post("/api/order/submit", json=_ticket())
    oid = sub.json()["order_id"]

    conf = oc_client.post(f"/api/order/{oid}/confirm", json={"confirmed": False})
    assert conf.status_code == 200, conf.text
    assert conf.json()["status"] == "rejected"
    assert calls["exec"] == 0


# ── 5. Timeout → 410 Gone, expired, no execute ──────────────────────────────


def test_confirm_after_timeout_returns_410(oc_client, monkeypatch, order_env):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("ORDER_CONFIRM_TIMEOUT_SECONDS", "60")

    import user_confirm
    import executor
    calls = {"exec": 0}
    monkeypatch.setattr(user_confirm, "send_confirmation", lambda *a, **k: 999)
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult())

    sub = oc_client.post("/api/order/submit", json=_ticket())
    oid = sub.json()["order_id"]

    # Force the pending order to look old.
    store = order_env["order_confirm"]
    rec = store.get_order(oid)
    rec.created_at -= 999  # seconds in the past

    conf = oc_client.post(f"/api/order/{oid}/confirm", json={"confirmed": True})
    assert conf.status_code == 410, conf.text
    assert calls["exec"] == 0
    # state should now be expired
    assert store.get_order(oid).state == "expired"


# ── 6. Idempotency — execute at most once ───────────────────────────────────


def test_double_confirm_executes_at_most_once(oc_client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("PAPER_TRADING", "false")

    import user_confirm
    import executor
    calls = {"exec": 0}
    monkeypatch.setattr(user_confirm, "send_confirmation", lambda *a, **k: 999)
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult(True))

    sub = oc_client.post("/api/order/submit", json=_ticket())
    oid = sub.json()["order_id"]

    first = oc_client.post(f"/api/order/{oid}/confirm", json={"confirmed": True})
    assert first.status_code == 200
    second = oc_client.post(f"/api/order/{oid}/confirm", json={"confirmed": True})
    # second must NOT re-execute: either 409 or returns prior result
    assert second.status_code in (200, 409)
    assert calls["exec"] == 1


def test_confirm_after_reject_does_not_execute(oc_client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    import user_confirm
    import executor
    calls = {"exec": 0}
    monkeypatch.setattr(user_confirm, "send_confirmation", lambda *a, **k: 999)
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult())

    sub = oc_client.post("/api/order/submit", json=_ticket())
    oid = sub.json()["order_id"]

    oc_client.post(f"/api/order/{oid}/confirm", json={"confirmed": False})
    again = oc_client.post(f"/api/order/{oid}/confirm", json={"confirmed": True})
    assert again.status_code in (200, 409)
    assert calls["exec"] == 0


# ── 7. Status endpoint for polling ──────────────────────────────────────────


def test_status_endpoint_returns_state(oc_client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    import user_confirm
    monkeypatch.setattr(user_confirm, "send_confirmation", lambda *a, **k: 999)

    sub = oc_client.post("/api/order/submit", json=_ticket())
    oid = sub.json()["order_id"]

    st = oc_client.get(f"/api/order/{oid}/status")
    assert st.status_code == 200, st.text
    assert st.json()["status"] == "pending_confirmation"
    assert st.json()["order_id"] == oid


def test_status_unknown_order_404(oc_client):
    st = oc_client.get("/api/order/does-not-exist/status")
    assert st.status_code == 404


# ── Risk-check rejection still works (regression) ───────────────────────────


def test_failed_risk_check_rejected_before_telegram(oc_client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    import user_confirm
    import executor
    calls = {"send": 0, "exec": 0}
    monkeypatch.setattr(user_confirm, "send_confirmation",
                        lambda *a, **k: calls.__setitem__("send", calls["send"] + 1) or 1)
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult())

    ticket = _ticket(risk_checks=[
        {"key": "黑名單篩查", "sub": "x", "status": "fail", "detail": "blacklisted"},
    ])
    resp = oc_client.post("/api/order/submit", json=ticket)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert calls["send"] == 0
    assert calls["exec"] == 0
