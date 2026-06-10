"""JWT auth dependency — stdlib hmac/hashlib (HS256) to avoid cryptography C-ext issues.

Security hardening (see security review):
  * SECRET_KEY has NO insecure hardcoded default. If the env var is unset we
    generate a strong random *ephemeral* secret at startup and log a prominent
    warning. Tokens minted by a previous process / the old default secret will
    therefore fail to validate.
  * Passwords are never compared in plaintext. We support either a bcrypt
    ``USER_PASSWORD_HASH`` env var, or a plain ``USER_PASSWORD`` which is hashed
    once at startup. If neither is set, login always fails.
  * Tokens carry a ``type`` claim ("access" | "refresh") and a unique ``jti``.
    The normal auth dependency accepts only "access" tokens; "refresh" tokens
    are accepted only by /api/auth/refresh.
  * Logout adds the current token's ``jti`` to an in-memory revocation set,
    which the auth dependency checks on every request.

WebSocket auth: WS endpoints cannot send an Authorization header from browsers,
so they authenticate via a ``?token=<access_token>`` query parameter. Use
``authenticate_ws_token()`` to validate it; on failure the caller closes the
socket with code 4401 (application-level "Unauthorized").
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .schemas.auth import User
from .security import hash_password, verify_password

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

logger = logging.getLogger("quant.auth")

ALGORITHM = "HS256"
USER_EMAIL = os.getenv("USER_EMAIL", "admin@quant.ai")

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"
ACCESS_TOKEN_EXPIRES = timedelta(hours=24)
REFRESH_TOKEN_EXPIRES = timedelta(days=7)

# ── Secret key (no insecure default) ──────────────────────────────────────────
_INSECURE_DEFAULT = "dev-secret-change-in-production"


def _load_secret_key() -> str:
    key = os.getenv("SECRET_KEY")
    if key:
        if key == _INSECURE_DEFAULT:
            logger.warning(
                "SECRET_KEY is set to the old insecure default value; "
                "generating an ephemeral secret instead."
            )
        else:
            return key
    # Unset (or insecure default): generate a strong ephemeral secret.
    ephemeral = secrets.token_urlsafe(64)
    logger.warning(
        "================================================================\n"
        "SECRET_KEY env var is not set. Generated a RANDOM EPHEMERAL secret "
        "for this process only. All JWTs will be invalidated on restart. "
        "Set SECRET_KEY in the environment for production.\n"
        "================================================================"
    )
    return ephemeral


SECRET_KEY = _load_secret_key()

# ── Password configuration ────────────────────────────────────────────────────


def _load_password_hash() -> str | None:
    """Return the bcrypt hash to authenticate against, or None if login is disabled."""
    pw_hash = os.getenv("USER_PASSWORD_HASH")
    if pw_hash:
        return pw_hash
    plain = os.getenv("USER_PASSWORD")
    if plain:
        # Hash the plaintext once at startup so we never compare in plaintext.
        return hash_password(plain)
    logger.warning(
        "No USER_PASSWORD_HASH or USER_PASSWORD configured — login is disabled "
        "(all attempts will be rejected)."
    )
    return None


USER_PASSWORD_HASH = _load_password_hash()


def authenticate_user(email: str, password: str) -> bool:
    """Constant-time-ish credential check. Always False if no password configured."""
    if USER_PASSWORD_HASH is None:
        # Still run a dummy verify to avoid trivial timing oracles on email.
        verify_password(password, "$2b$12$" + "x" * 53)
        return False
    if email != USER_EMAIL:
        verify_password(password, USER_PASSWORD_HASH)
        return False
    return verify_password(password, USER_PASSWORD_HASH)


# ── In-memory token revocation (jti) set ──────────────────────────────────────
_REVOKED_JTIS: set[str] = set()


def revoke_jti(jti: str) -> None:
    if jti:
        _REVOKED_JTIS.add(jti)


def is_revoked(jti: str | None) -> bool:
    return bool(jti) and jti in _REVOKED_JTIS


# ── JWT (HS256 via stdlib hmac) ───────────────────────────────────────────────

security = HTTPBearer(auto_error=False)

_HEADER_B64 = base64.urlsafe_b64encode(
    json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()
).rstrip(b"=").decode()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign(msg: str) -> str:
    sig = hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
    return _b64url_encode(sig)


def _create_token(email: str, token_type: str, expires_delta: timedelta) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": email,
        "exp": int(expire.timestamp()),
        "type": token_type,
        "jti": uuid.uuid4().hex,
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    msg = f"{_HEADER_B64}.{payload_b64}"
    sig = _sign(msg)
    return f"{msg}.{sig}"


def create_access_token(email: str, expires_delta: timedelta = ACCESS_TOKEN_EXPIRES) -> str:
    return _create_token(email, TOKEN_TYPE_ACCESS, expires_delta)


def create_refresh_token(email: str, expires_delta: timedelta = REFRESH_TOKEN_EXPIRES) -> str:
    return _create_token(email, TOKEN_TYPE_REFRESH, expires_delta)


def _decode_token(token: str) -> dict:
    """Decode and verify a JWT token. Raises ValueError on failure."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token format")
    header_b64, payload_b64, sig_b64 = parts
    expected_sig = _sign(f"{header_b64}.{payload_b64}")
    if not hmac.compare_digest(expected_sig, sig_b64):
        raise ValueError("Invalid token signature")
    payload = json.loads(_b64url_decode(payload_b64))
    exp = payload.get("exp")
    if exp is not None and datetime.now(timezone.utc).timestamp() > exp:
        raise ValueError("Token expired")
    return payload


def _user_from_payload(payload: dict) -> User:
    email: str | None = payload.get("sub")
    if email is None:
        raise ValueError("Missing sub claim")
    return User(
        id="single-user",
        email=email,
        display_name=email.split("@")[0],
        shioaji_bound=False,
        telegram_bound=False,
        two_factor_enabled=False,
    )


def decode_token_of_type(token: str, expected_type: str) -> dict:
    """Decode, verify signature/exp, enforce token type and revocation.

    Raises ValueError on any failure.
    """
    payload = _decode_token(token)
    if payload.get("type") != expected_type:
        raise ValueError(f"Expected {expected_type} token")
    if is_revoked(payload.get("jti")):
        raise ValueError("Token revoked")
    return payload


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token_of_type(credentials.credentials, TOKEN_TYPE_ACCESS)
        return _user_from_payload(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )


def authenticate_ws_token(token: str | None) -> User | None:
    """Validate a WebSocket ``?token=`` query param. Returns the User or None.

    Callers should close the socket with code 4401 when this returns None.
    """
    if not token:
        return None
    try:
        payload = decode_token_of_type(token, TOKEN_TYPE_ACCESS)
        return _user_from_payload(payload)
    except ValueError:
        return None
