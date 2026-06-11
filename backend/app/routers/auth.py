from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..deps import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    decode_token_of_type,
    get_current_user,
    revoke_jti,
)
from ..schemas.auth import LoginReq, LoginResponse, RefreshReq, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


def _user_for(email: str) -> User:
    return User(
        id="single-user",
        email=email,
        display_name=email.split("@")[0],
        shioaji_bound=False,
        telegram_bound=False,
        two_factor_enabled=False,
    )


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginReq) -> LoginResponse:
    if not authenticate_user(req.email, req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    access = create_access_token(req.email)
    refresh = create_refresh_token(req.email)
    return LoginResponse(
        user=_user_for(req.email),
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=86400,
        two_factor_required=False,
    )


@router.post("/refresh")
async def refresh(req: RefreshReq) -> dict:
    """Exchange a *refresh*-type token for a fresh access token.

    Access-type tokens are rejected here.
    """
    try:
        payload = decode_token_of_type(req.refresh_token, "refresh")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access = create_access_token(payload["sub"])
    return {"access_token": access, "token_type": "bearer", "expires_in": 86400}


@router.post("/logout")
async def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Revoke the presented access token by adding its jti to the revocation set."""
    if credentials is not None:
        try:
            payload = decode_token_of_type(credentials.credentials, "access")
            revoke_jti(payload.get("jti"))
        except ValueError:
            # Already invalid/expired — nothing to revoke.
            pass
    return {"ok": True}


@router.get("/me", response_model=User)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
