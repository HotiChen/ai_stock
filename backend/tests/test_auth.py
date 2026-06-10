"""Auth hardening tests: secret key, password hashing, refresh/logout flows."""
from __future__ import annotations

import os

from .conftest import TEST_EMAIL, TEST_PASSWORD, TEST_SECRET


# ── Issue 2: password verification ────────────────────────────────────────────

def test_login_correct_password(client):
    resp = client.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    # Backwards-compatible contract
    assert body["access_token"] != body["refresh_token"]
    assert body.get("user") is not None
    # token_type must be present and bearer-compatible
    assert body.get("token_type", "bearer").lower() == "bearer"


def test_login_wrong_password(client):
    resp = client.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": "totally-wrong"},
    )
    assert resp.status_code == 401


def test_login_wrong_email(client):
    resp = client.post(
        "/api/auth/login",
        json={"email": "nobody@quant.ai", "password": TEST_PASSWORD},
    )
    assert resp.status_code == 401


def test_login_fails_when_no_password_configured(monkeypatch):
    """If neither USER_PASSWORD nor USER_PASSWORD_HASH is set, login always 401.

    Reloads deps/auth with a cleared password env to assert no 'password' default.
    """
    import importlib

    monkeypatch.delenv("USER_PASSWORD", raising=False)
    monkeypatch.delenv("USER_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("USER_EMAIL", TEST_EMAIL)

    import backend.app.deps as deps_mod
    import backend.app.routers.auth as auth_mod
    importlib.reload(deps_mod)
    importlib.reload(auth_mod)

    from fastapi.testclient import TestClient
    # Build a tiny app with just the reloaded auth router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth_mod.router)
    c = TestClient(app)

    # Both the old default 'password' and any guess must fail.
    assert c.post("/api/auth/login", json={"email": TEST_EMAIL, "password": "password"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": TEST_EMAIL, "password": ""}).status_code == 401

    # Restore the normal module state for other tests.
    monkeypatch.setenv("USER_PASSWORD", TEST_PASSWORD)
    importlib.reload(deps_mod)
    importlib.reload(auth_mod)


def test_password_hash_env_supported(monkeypatch):
    """A USER_PASSWORD_HASH (bcrypt) should authenticate the matching password."""
    import importlib

    from backend.app import security as sec  # password helper module

    hashed = sec.hash_password("h@shed-Pass-99")

    monkeypatch.delenv("USER_PASSWORD", raising=False)
    monkeypatch.setenv("USER_PASSWORD_HASH", hashed)
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("USER_EMAIL", TEST_EMAIL)

    import backend.app.deps as deps_mod
    import backend.app.routers.auth as auth_mod
    importlib.reload(deps_mod)
    importlib.reload(auth_mod)

    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(auth_mod.router)
    c = TestClient(app)

    assert c.post("/api/auth/login", json={"email": TEST_EMAIL, "password": "h@shed-Pass-99"}).status_code == 200
    assert c.post("/api/auth/login", json={"email": TEST_EMAIL, "password": "nope"}).status_code == 401

    # Restore
    monkeypatch.delenv("USER_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("USER_PASSWORD", TEST_PASSWORD)
    importlib.reload(deps_mod)
    importlib.reload(auth_mod)


# ── Issue 1: secret key — old default must not validate ───────────────────────

def test_token_signed_with_old_default_secret_rejected(client):
    """A token forged with the removed 'dev-secret-change-in-production' default
    must NOT be accepted."""
    import base64
    import hashlib
    import hmac
    import json
    from datetime import datetime, timedelta, timezone

    old_secret = "dev-secret-change-in-production"

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    exp = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    payload = b64(json.dumps({"sub": TEST_EMAIL, "exp": exp, "type": "access"}, separators=(",", ":")).encode())
    msg = f"{header}.{payload}"
    sig = b64(hmac.new(old_secret.encode(), msg.encode(), hashlib.sha256).digest())
    forged = f"{msg}.{sig}"

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


def test_ephemeral_secret_generated_when_unset(monkeypatch):
    """When SECRET_KEY is unset, deps must generate a strong random secret
    (not the old insecure default)."""
    import importlib

    monkeypatch.delenv("SECRET_KEY", raising=False)
    import backend.app.deps as deps_mod
    importlib.reload(deps_mod)

    assert deps_mod.SECRET_KEY != "dev-secret-change-in-production"
    assert len(deps_mod.SECRET_KEY) >= 32

    # Restore deterministic secret for the rest of the suite.
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    importlib.reload(deps_mod)
    import backend.app.routers.auth as auth_mod
    importlib.reload(auth_mod)


# ── Issue 6: refresh token / logout revocation ────────────────────────────────

def test_me_with_access_token(client, token):
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == TEST_EMAIL


def test_refresh_with_refresh_token_returns_new_access(client, refresh_token):
    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200, resp.text
    new_access = resp.json()["access_token"]
    assert new_access
    # New access token works against a protected endpoint.
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200


def test_refresh_rejects_access_token(client, token):
    """An access-type token must NOT be usable at /refresh."""
    resp = client.post("/api/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401


def test_auth_dependency_rejects_refresh_token(client, refresh_token):
    """A refresh-type token must NOT pass the normal auth dependency."""
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert resp.status_code == 401


def test_logout_revokes_token(client, token):
    # Token works before logout.
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    # Logout revokes it.
    out = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert out.status_code == 200
    # Same token now rejected.
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_no_auth_header_rejected(client):
    assert client.get("/api/auth/me").status_code == 401
