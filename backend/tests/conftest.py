"""Pytest configuration for backend auth hardening tests.

Ensures imports resolve from the repo root (so ``backend.app...`` works) and
sets up a deterministic auth environment BEFORE the FastAPI app is imported,
because backend/app/deps.py reads its security config at import time.
"""
from __future__ import annotations

import os
import sys

# ── sys.path: make repo root importable so `backend.app...` resolves ──────────
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ── Deterministic auth env (must be set before importing the app) ─────────────
TEST_EMAIL = "trader@quant.ai"
TEST_PASSWORD = "S3cur3-P@ss"
TEST_SECRET = "test-fixed-secret-key-for-pytest-do-not-use-in-prod"

os.environ["SECRET_KEY"] = TEST_SECRET
os.environ["USER_EMAIL"] = TEST_EMAIL
os.environ["USER_PASSWORD"] = TEST_PASSWORD
os.environ.pop("USER_PASSWORD_HASH", None)
os.environ["CORS_ORIGINS"] = "http://localhost:5173,https://app.example.com"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def app():
    # Import lazily so the env above is already in place.
    from backend.app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def token(client) -> str:
    """A valid access token obtained via the real login flow."""
    resp = client.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture()
def refresh_token(client) -> str:
    resp = client.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["refresh_token"]
