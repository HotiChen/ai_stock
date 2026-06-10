"""Password hashing helpers (bcrypt).

Prefers passlib's CryptContext (as mandated by the security review). Some
environments ship a passlib (1.7.x) / bcrypt (5.x) combination where passlib's
bcrypt backend fails to initialise; in that case we fall back to the `bcrypt`
library directly. Both paths produce/verify standard ``$2b$`` bcrypt hashes, so
the two are interchangeable.

bcrypt only considers the first 72 bytes of a password; we truncate explicitly
to avoid the ValueError raised by bcrypt 4+/5+ on longer inputs.
"""
from __future__ import annotations

_BCRYPT_MAX_BYTES = 72


def _truncate(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


# Try to build a working passlib context; if its bcrypt backend is broken in
# this environment, mark it unusable and fall back to the bcrypt lib directly.
_passlib_ctx = None
try:  # pragma: no cover - environment dependent
    from passlib.context import CryptContext

    _ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    # Probe the backend once; raises if bcrypt backend is unusable.
    _ctx.verify("probe", _ctx.hash("probe"))
    _passlib_ctx = _ctx
except Exception:  # pragma: no cover - environment dependent
    _passlib_ctx = None

import bcrypt as _bcrypt  # noqa: E402


def hash_password(password: str) -> str:
    """Return a bcrypt hash for ``password`` (constant-cost)."""
    if _passlib_ctx is not None:
        return _passlib_ctx.hash(password)
    return _bcrypt.hashpw(_truncate(password), _bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time verification of ``password`` against a bcrypt ``hashed``."""
    if not hashed:
        return False
    if _passlib_ctx is not None:
        try:
            return _passlib_ctx.verify(password, hashed)
        except Exception:
            # passlib raised on the truncation; retry via direct bcrypt below.
            pass
    try:
        return _bcrypt.checkpw(_truncate(password), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
