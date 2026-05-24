from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.base import AlertLevel, AppMode
from ..schemas.settings import (
    AppSettings,
    DesktopChannel,
    EmailChannel,
    IosPushChannel,
    NotifySettings,
    SettingsPatch,
    SlackChannel,
    TelegramChannel,
    ApiKeys,
    ToggleModeReq,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_mock_settings = AppSettings(
    budget=1_000_000,
    order_hard_limit=200_000,
    daily_max_loss_pct=0.03,
    max_position_ratio=0.2,
    max_sector_ratio=0.4,
    entry_confidence_threshold=0.65,
    default_stop_loss_pct=0.03,
    model_premarket="claude-haiku-4-5-20251001",
    model_dashboard="claude-haiku-4-5-20251001",
    model_chat="claude-sonnet-4-6",
    mode=AppMode.SIMULATION,
    notify=NotifySettings(
        telegram=TelegramChannel(enabled=False, chat_id="", levels=[AlertLevel.HIGH]),
        email=EmailChannel(enabled=False, address="", levels=[AlertLevel.HIGH]),
        desktop=DesktopChannel(enabled=True, levels=[AlertLevel.HIGH, AlertLevel.MED]),
        ios_push=IosPushChannel(enabled=False, device_token="", levels=[AlertLevel.HIGH]),
        slack=SlackChannel(enabled=False, webhook="", levels=[AlertLevel.HIGH]),
    ),
    blacklist=[],
    theme="dark",
    language="zh-TW",
    api_keys=ApiKeys(anthropic="sk-***", shioaji="***", telegram="***"),
    monitor_poll_seconds=30,
    db_path="ai_stock/data/research.db",
    version="0.1.0",
)


@router.get("/", response_model=AppSettings)
async def get_settings(current_user: User = Depends(get_current_user)) -> AppSettings:
    return _mock_settings


@router.patch("/", response_model=AppSettings)
async def patch_settings(
    patch: SettingsPatch,
    current_user: User = Depends(get_current_user),
) -> AppSettings:
    global _mock_settings
    updated = _mock_settings.model_copy(
        update={k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None}
    )
    _mock_settings = updated
    return _mock_settings


@router.post("/toggle-mode")
async def toggle_mode(
    req: ToggleModeReq,
    current_user: User = Depends(get_current_user),
) -> dict:
    global _mock_settings
    if req.confirm_email != current_user.email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="confirm_email does not match your account email",
        )
    _mock_settings = _mock_settings.model_copy(update={"mode": req.target})
    return {"ok": True, "mode": req.target}
