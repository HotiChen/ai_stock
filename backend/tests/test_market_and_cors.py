"""Issue 4 (market snapshot auth) and Issue 5 (CORS hardening)."""
from __future__ import annotations

from .conftest import TEST_SECRET


def test_market_snapshot_requires_auth(client):
    resp = client.get("/api/market/snapshot")
    assert resp.status_code == 401


def test_market_snapshot_with_token(client, token):
    resp = client.get("/api/market/snapshot", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "session" in resp.json()


def test_cors_origins_from_env(client):
    """CORS_ORIGINS env (set in conftest) should allow the configured extra origin."""
    resp = client.get(
        "/api/health",
        headers={"Origin": "https://app.example.com"},
    )
    assert resp.headers.get("access-control-allow-origin") == "https://app.example.com"


def test_cors_disallows_unknown_origin(client):
    resp = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
    # Unknown origin must not be echoed back as allowed.
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example.com"


def test_cors_preflight_methods_and_headers(client):
    resp = client.options(
        "/api/market/snapshot",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    # Wildcard methods must no longer be used.
    assert "*" not in allow_methods
    assert "GET" in allow_methods
    assert "POST" in allow_methods
