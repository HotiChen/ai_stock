from pydantic import BaseModel


class LoginReq(BaseModel):
    email: str
    password: str


class Verify2FAReq(BaseModel):
    challenge_id: str
    code: str


class RefreshReq(BaseModel):
    refresh_token: str


class User(BaseModel):
    id: str
    email: str
    display_name: str
    shioaji_bound: bool
    telegram_bound: bool
    two_factor_enabled: bool


class LoginResponse(BaseModel):
    user: User | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_in: int | None = None
    two_factor_required: bool = False
    challenge_id: str | None = None
