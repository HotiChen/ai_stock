"""Tests for the Telegram→backend bridge endpoint (security-critical).

POST /api/order/internal/confirm-by-code is the channel that lets the
separately-running telegram_bot.py process push an approve/reject decision
into this process's in-memory order state machine.

Auth model (NOT JWT):
  - header X-Internal-Token must equal env BACKEND_INTERNAL_TOKEN
  - env unset/empty → endpoint always 403 (fail closed)
  - wrong / missing header → 401

These tests are TDD-style: they encode the required behaviour. They follow the
fixture / mocking style of test_order_confirm.py (dependency_overrides for JWT
on the *submit* helper path, env cleanup, store reset between tests).

Run with:
    cd /home/user/ai_stock && python -m pytest backend/tests/test_order_confirm_by_code.py -q
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
    """Import the FastAPI app + service + router fresh each test.

    The /submit endpoint is JWT-protected (used here only to register a pending
    order); we override get_current_user exactly like test_order_confirm.py.
    The internal confirm-by-code endpoint is NOT JWT-protected.
    """
    from backend.app.main import app
    from backend.app.schemas.auth import User
    from backend.app.services import order_confirm
    from backend.app.routers import order as order_router
    import backend.app.deps as deps_mod

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


_INTERNAL = "/api/order/internal/confirm-by-code"
_TOKEN = "s3cr3t-internal-token"


def _submit_pending(oc_client, monkeypatch, code: str = "2330") -> str:
    """Register a pending order via the real /submit path; return order_id."""
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    import user_confirm
    monkeypatch.setattr(user_confirm, "send_confirmation", lambda *a, **k: 999)
    sub = oc_client.post("/api/order/submit", json=_ticket(code=code))
    assert sub.status_code == 200, sub.text
    return sub.json()["order_id"]


# ── 1. Auth: env token unset → always 403 (fail closed) ─────────────────────


def test_env_token_unset_returns_403(oc_client, monkeypatch):
    monkeypatch.delenv("BACKEND_INTERNAL_TOKEN", raising=False)
    resp = oc_client.post(
        _INTERNAL,
        json={"code": "2330", "confirmed": True},
        headers={"X-Internal-Token": "anything"},
    )
    assert resp.status_code == 403, resp.text


def test_env_token_empty_returns_403(oc_client, monkeypatch):
    monkeypatch.setenv("BACKEND_INTERNAL_TOKEN", "")
    resp = oc_client.post(
        _INTERNAL,
        json={"code": "2330", "confirmed": True},
        headers={"X-Internal-Token": ""},
    )
    assert resp.status_code == 403, resp.text


# ── 2. Auth: wrong / missing header → 401 ───────────────────────────────────


def test_wrong_header_returns_401(oc_client, monkeypatch):
    monkeypatch.setenv("BACKEND_INTERNAL_TOKEN", _TOKEN)
    resp = oc_client.post(
        _INTERNAL,
        json={"code": "2330", "confirmed": True},
        headers={"X-Internal-Token": "wrong"},
    )
    assert resp.status_code == 401, resp.text


def test_missing_header_returns_401(oc_client, monkeypatch):
    monkeypatch.setenv("BACKEND_INTERNAL_TOKEN", _TOKEN)
    resp = oc_client.post(_INTERNAL, json={"code": "2330", "confirmed": True})
    assert resp.status_code == 401, resp.text


# ── 3. approve executes exactly once ────────────────────────────────────────


def test_approve_executes_exactly_once(oc_client, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")  # force real executor path
    monkeypatch.setenv("BACKEND_INTERNAL_TOKEN", _TOKEN)
    import executor
    calls = {"exec": 0}
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult(True))

    _submit_pending(oc_client, monkeypatch)

    resp = oc_client.post(
        _INTERNAL,
        json={"code": "2330", "confirmed": True},
        headers={"X-Internal-Token": _TOKEN},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] in ("filled", "confirmed")
    assert calls["exec"] == 1


# ── 4. reject never executes ────────────────────────────────────────────────


def test_reject_never_executes(oc_client, monkeypatch):
    monkeypatch.setenv("BACKEND_INTERNAL_TOKEN", _TOKEN)
    import executor
    calls = {"exec": 0}
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult())

    _submit_pending(oc_client, monkeypatch)

    resp = oc_client.post(
        _INTERNAL,
        json={"code": "2330", "confirmed": False},
        headers={"X-Internal-Token": _TOKEN},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"
    assert calls["exec"] == 0


# ── 5. no pending order for code → 404 ──────────────────────────────────────


def test_no_pending_for_code_returns_404(oc_client, monkeypatch):
    monkeypatch.setenv("BACKEND_INTERNAL_TOKEN", _TOKEN)
    resp = oc_client.post(
        _INTERNAL,
        json={"code": "9999", "confirmed": True},
        headers={"X-Internal-Token": _TOKEN},
    )
    assert resp.status_code == 404, resp.text


def test_pending_for_other_code_returns_404(oc_client, monkeypatch):
    monkeypatch.setenv("BACKEND_INTERNAL_TOKEN", _TOKEN)
    _submit_pending(oc_client, monkeypatch, code="2330")
    resp = oc_client.post(
        _INTERNAL,
        json={"code": "2454", "confirmed": True},
        headers={"X-Internal-Token": _TOKEN},
    )
    assert resp.status_code == 404, resp.text


# ── 6. expired → 410 ────────────────────────────────────────────────────────


def test_expired_returns_410(oc_client, monkeypatch, order_env):
    monkeypatch.setenv("BACKEND_INTERNAL_TOKEN", _TOKEN)
    monkeypatch.setenv("ORDER_CONFIRM_TIMEOUT_SECONDS", "60")
    import executor
    calls = {"exec": 0}
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult())

    oid = _submit_pending(oc_client, monkeypatch)
    store = order_env["order_confirm"]
    store.get_order(oid).created_at -= 999

    resp = oc_client.post(
        _INTERNAL,
        json={"code": "2330", "confirmed": True},
        headers={"X-Internal-Token": _TOKEN},
    )
    assert resp.status_code == 410, resp.text
    assert calls["exec"] == 0
    assert store.get_order(oid).state == "expired"


# ── 7. idempotent double-call → executor once ───────────────────────────────


def test_double_approve_executes_at_most_once(oc_client, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("BACKEND_INTERNAL_TOKEN", _TOKEN)
    import executor
    calls = {"exec": 0}
    monkeypatch.setattr(executor, "place_stock_order",
                        lambda *a, **k: calls.__setitem__("exec", calls["exec"] + 1) or _FakeExecResult(True))

    _submit_pending(oc_client, monkeypatch)

    first = oc_client.post(
        _INTERNAL,
        json={"code": "2330", "confirmed": True},
        headers={"X-Internal-Token": _TOKEN},
    )
    assert first.status_code == 200, first.text
    # Second call: the order is no longer pending → find_pending_by_code finds
    # nothing → 404 (the bot treats this as "ignore"). Executor must not re-run.
    second = oc_client.post(
        _INTERNAL,
        json={"code": "2330", "confirmed": True},
        headers={"X-Internal-Token": _TOKEN},
    )
    assert second.status_code in (404, 409, 200), second.text
    assert calls["exec"] == 1
