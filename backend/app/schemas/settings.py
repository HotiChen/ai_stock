from typing import Literal
from pydantic import BaseModel

from .base import AlertLevel, AppMode


class NotifyChannel(BaseModel):
    enabled: bool
    levels: list[AlertLevel]


class TelegramChannel(NotifyChannel):
    chat_id: str


class EmailChannel(NotifyChannel):
    address: str


class DesktopChannel(NotifyChannel):
    pass


class IosPushChannel(NotifyChannel):
    device_token: str


class SlackChannel(NotifyChannel):
    webhook: str


class NotifySettings(BaseModel):
    telegram: TelegramChannel
    email: EmailChannel
    desktop: DesktopChannel
    ios_push: IosPushChannel
    slack: SlackChannel


class ApiKeys(BaseModel):
    anthropic: str
    shioaji: str
    telegram: str


class AppSettings(BaseModel):
    budget: int
    order_hard_limit: int
    daily_max_loss_pct: float
    max_position_ratio: float
    max_sector_ratio: float
    entry_confidence_threshold: float
    default_stop_loss_pct: float
    model_premarket: str
    model_dashboard: str
    model_chat: str
    mode: AppMode
    notify: NotifySettings
    blacklist: list[str]
    theme: Literal["light", "dark"]
    language: Literal["zh-TW", "zh-CN", "en"]
    api_keys: ApiKeys
    monitor_poll_seconds: int
    db_path: str
    version: str


class SettingsPatch(BaseModel):
    budget: int | None = None
    order_hard_limit: int | None = None
    daily_max_loss_pct: float | None = None
    max_position_ratio: float | None = None
    max_sector_ratio: float | None = None
    entry_confidence_threshold: float | None = None
    default_stop_loss_pct: float | None = None
    model_premarket: str | None = None
    model_dashboard: str | None = None
    model_chat: str | None = None
    theme: Literal["light", "dark"] | None = None
    language: Literal["zh-TW", "zh-CN", "en"] | None = None
    monitor_poll_seconds: int | None = None
    blacklist: list[str] | None = None


class ToggleModeReq(BaseModel):
    target: AppMode
    confirm_email: str
