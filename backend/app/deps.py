"""JWT auth dependency — uses stdlib hmac/hashlib (HS256) to avoid cryptography C-ext issues."""

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .schemas.auth import User

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../ai_stock/.env"))

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = "HS256"
USER_EMAIL = os.getenv("USER_EMAIL", "admin@quant.ai")
USER_PASSWORD = os.getenv("USER_PASSWORD", "password")

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


def create_access_token(email: str, expires_delta: timedelta = timedelta(hours=24)) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {"sub": email, "exp": int(expire.timestamp())}
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    msg = f"{_HEADER_B64}.{payload_b64}"
    sig = _sign(msg)
    return f"{msg}.{sig}"


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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        payload = _decode_token(token)
        email: str | None = payload.get("sub")
        if email is None:
            raise ValueError("Missing sub claim")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return User(
        id="single-user",
        email=email,
        display_name=email.split("@")[0],
        shioaji_bound=False,
        telegram_bound=False,
        two_factor_enabled=False,
    )
