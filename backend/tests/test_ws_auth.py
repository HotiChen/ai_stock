"""Issue 3: WebSocket endpoints require a valid JWT via ?token= query param.

Heavy data builders are monkeypatched to lightweight stubs so the tests assert
the AUTH gate only, not the data pipeline.
"""
from __future__ import annotations

import pytest
from fastapi import WebSocketDisconnect


@pytest.fixture(autouse=True)
def _stub_ws_payloads(monkeypatch):
    """Replace heavy snapshot/chart builders with trivial stubs."""
    import backend.app.ws.daytrade as wsd
    import backend.app.ws.chart as wsc

    async def _fake_snapshot():
        return {"ok": True}

    monkeypatch.setattr(wsd, "get_live_snapshot", _fake_snapshot)
    monkeypatch.setattr(wsc, "_build_chart_view", lambda code, ticks: {"ok": True, "code": code})
    monkeypatch.setattr(wsc, "_fetch_real_ticks", lambda code: None)
    yield


# ── /ws/market ────────────────────────────────────────────────────────────────

def test_ws_market_without_token_rejected(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/market"):
            pass
    assert exc.value.code == 4401


def test_ws_market_with_token_accepted(client, token):
    with client.websocket_connect(f"/ws/market?token={token}") as ws:
        data = ws.receive_json()
        assert "session" in data


# ── /ws/daytrade ──────────────────────────────────────────────────────────────

def test_ws_daytrade_without_token_rejected(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/daytrade"):
            pass
    assert exc.value.code == 4401


def test_ws_daytrade_with_token_accepted(client, token):
    with client.websocket_connect(f"/ws/daytrade?token={token}") as ws:
        data = ws.receive_json()
        assert data == {"ok": True}


# ── /ws/daytrade/{code}/chart ─────────────────────────────────────────────────

def test_ws_chart_without_token_rejected(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/daytrade/2330/chart"):
            pass
    assert exc.value.code == 4401


def test_ws_chart_with_token_accepted(client, token):
    with client.websocket_connect(f"/ws/daytrade/2330/chart?token={token}") as ws:
        data = ws.receive_json()
        assert data["ok"] is True


# ── Refresh tokens must not authorize WS connections ──────────────────────────

def test_ws_rejects_refresh_token(client, refresh_token):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/market?token={refresh_token}"):
            pass
    assert exc.value.code == 4401
