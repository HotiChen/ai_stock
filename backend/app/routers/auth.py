import os

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import USER_EMAIL, USER_PASSWORD, create_access_token, get_current_user
from ..schemas.auth import LoginReq, LoginResponse, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginReq) -> LoginResponse:
    if req.email != USER_EMAIL or req.password != USER_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    token = create_access_token(req.email)
    user = User(
        id="single-user",
        email=req.email,
        display_name=req.email.split("@")[0],
        shioaji_bound=False,
        telegram_bound=False,
        two_factor_enabled=False,
    )
    return LoginResponse(
        user=user,
        access_token=token,
        refresh_token=token,
        expires_in=86400,
        two_factor_required=False,
    )


@router.post("/refresh")
async def refresh(current_user: User = Depends(get_current_user)) -> dict:
    token = create_access_token(current_user.email)
    return {"access_token": token}


@router.post("/logout")
async def logout() -> dict:
    return {"ok": True}


@router.get("/me", response_model=User)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
