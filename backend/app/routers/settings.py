"""
settings.py — Settings Router (Wave 2-C)

GET /api/settings  → 真實讀取 ai_stock/config.py 值，包成 AppSettings。
PATCH /api/settings → 保留 mock（Wave 4 實作寫回 .env）。
"""

from __future__ import annotations

import logging
import os
import sys

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

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# ── 路徑注入 ────────────────────────────────────────────────────────────────

_AI_STOCK_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../")
)
if _AI_STOCK_ROOT not in sys.path:
    sys.path.insert(0, _AI_STOCK_ROOT)


# ── 讀取真實 config ──────────────────────────────────────────────────────────

def _build_real_settings() -> AppSettings | None:
    """嘗試從 ai_stock/config.py 讀取真實設定值。"""
    try:
        import config as cfg

        # 判斷模式
        sim_env = os.environ.get("SHIOAJI_SIMULATION", "true")
        mode = AppMode.SIMULATION if sim_env.lower() in ("true", "1", "yes") else AppMode.LIVE

        # Telegram 設定（從 env 讀取）
        tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        tg_enabled = bool(tg_chat_id and tg_token)

        # DB 路徑
        db_path = os.environ.get("RESEARCH_DB_PATH", "ai_stock/data/research.db")

        # Version（嘗試從 pyproject.toml 或 env 讀取）
        version = os.environ.get("APP_VERSION", "0.1.0")
        try:
            import importlib.metadata
            version = importlib.metadata.version("ai-stock")
        except Exception:
            pass

        # ORDER_HARD_LIMIT（config.py 沒有直接定義，從 env 或 BUDGET 推算）
        order_hard_limit = int(
            os.environ.get("ORDER_HARD_LIMIT", str(int(cfg.BUDGET * 0.2)))
        )

        # 信心門檻（config.py 沒有，從 env 或預設）
        entry_confidence_threshold = float(
            os.environ.get("ENTRY_CONFIDENCE_THRESHOLD", "0.65")
        )

        # max_position_ratio / max_sector_ratio（risk_guard.py 有，從 env 或預設）
        max_position_ratio = float(
            os.environ.get("MAX_POSITION_RATIO", str(cfg.__dict__.get("MAX_POSITION_RATIO", 0.2)))
        )
        max_sector_ratio = float(
            os.environ.get("MAX_SECTOR_RATIO", "0.4")
        )

        # 黑名單（從 env 讀取，逗號分隔）
        blacklist_raw = os.environ.get("BLACKLIST", "")
        blacklist = [c.strip() for c in blacklist_raw.split(",") if c.strip()]

        # API keys（遮罩）
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        shioaji_id = os.environ.get("SHIOAJI_API_KEY", "") or os.environ.get("SHIOAJI_ID", "")
        masked_anthropic = ("sk-" + "*" * 8 + anthropic_key[-4:]) if len(anthropic_key) > 8 else "sk-***"
        masked_shioaji = ("***" + shioaji_id[-4:]) if len(shioaji_id) > 4 else "***"
        masked_tg = ("***" + tg_token[-4:]) if len(tg_token) > 4 else "***"

        return AppSettings(
            budget=int(cfg.BUDGET),
            order_hard_limit=order_hard_limit,
            daily_max_loss_pct=float(os.environ.get("DAILY_MAX_LOSS_PCT", "0.03")),
            max_position_ratio=max_position_ratio,
            max_sector_ratio=max_sector_ratio,
            entry_confidence_threshold=entry_confidence_threshold,
            default_stop_loss_pct=cfg.STOP_LOSS_PCT / 100.0 if cfg.STOP_LOSS_PCT > 1.0 else cfg.STOP_LOSS_PCT,
            model_premarket=os.environ.get("MODEL_PREMARKET", "claude-haiku-4-5-20251001"),
            model_dashboard=os.environ.get("MODEL_DASHBOARD", "claude-haiku-4-5-20251001"),
            model_chat=os.environ.get("MODEL_CHAT", "claude-sonnet-4-6"),
            mode=mode,
            notify=NotifySettings(
                telegram=TelegramChannel(
                    enabled=tg_enabled,
                    chat_id=tg_chat_id,
                    levels=[AlertLevel.HIGH],
                ),
                email=EmailChannel(
                    enabled=False,
                    address=os.environ.get("NOTIFY_EMAIL", ""),
                    levels=[AlertLevel.HIGH],
                ),
                desktop=DesktopChannel(
                    enabled=True,
                    levels=[AlertLevel.HIGH, AlertLevel.MED],
                ),
                ios_push=IosPushChannel(
                    enabled=False,
                    device_token="",
                    levels=[AlertLevel.HIGH],
                ),
                slack=SlackChannel(
                    enabled=False,
                    webhook=os.environ.get("SLACK_WEBHOOK", ""),
                    levels=[AlertLevel.HIGH],
                ),
            ),
            blacklist=blacklist,
            theme=os.environ.get("THEME", "dark"),  # type: ignore[arg-type]
            language=os.environ.get("LANGUAGE", "zh-TW"),  # type: ignore[arg-type]
            api_keys=ApiKeys(
                anthropic=masked_anthropic,
                shioaji=masked_shioaji,
                telegram=masked_tg,
            ),
            monitor_poll_seconds=30,
            db_path=db_path,
            version=version,
        )
    except Exception as e:
        log.warning("_build_real_settings() failed: %s", e)
        return None


# ── 預設 mock（當 config 讀取失敗時使用）───────────────────────────────────

#: 使用者在 UI 改過的設定（只存在記憶體，重啟後回到 config.py 的值）。
#: 這不是「已保存」——寫回 .env 尚未實作。
_settings_override: AppSettings | None = None
# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/", response_model=AppSettings)
async def get_settings(
    current_user: User = Depends(get_current_user),
) -> AppSettings:
    """讀取真實 config.py 值。

    讀不到就回 503——原本降級回一組預設值，畫面顯示的設定與系統實際
    在用的不一致，而且看不出來。
    """
    real = _build_real_settings()
    if real is None:
        raise HTTPException(
            status_code=503,
            detail="讀不到系統設定（config.py 是否可 import？）",
        )
    return _settings_override or real


@router.patch("/", response_model=AppSettings)
async def patch_settings(
    patch: SettingsPatch,
    current_user: User = Depends(get_current_user),
) -> AppSettings:
    """更新設定。

    ⚠ 目前只改記憶體，不寫回 .env——重啟後會回到 config.py 的值。
    這是已知限制，不是「設定已保存」。
    """
    global _settings_override
    base = _build_real_settings()
    if base is None:
        raise HTTPException(
            status_code=503,
            detail="讀不到系統設定，無法更新",
        )
    updated = base.model_copy(
        update={k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None}
    )
    _settings_override = updated
    return _settings_override


@router.post("/toggle-mode")
async def toggle_mode(
    req: ToggleModeReq,
    current_user: User = Depends(get_current_user),
) -> dict:
    """切換模擬/真實模式（需 email 全文確認）。"""
    global _settings_override
    if req.confirm_email != current_user.email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="confirm_email does not match your account email",
        )
    _settings_override = (_settings_override or _build_real_settings()).model_copy(
        update={"mode": req.target})
    # 嘗試寫入 env（best-effort，Wave 4 完整實作）
    try:
        sim_val = "true" if req.target == AppMode.SIMULATION else "false"
        os.environ["SHIOAJI_SIMULATION"] = sim_val
        log.info("SHIOAJI_SIMULATION set to %s in process env", sim_val)
    except Exception as e:
        log.warning("toggle_mode env write failed: %s", e)
    return {"ok": True, "mode": req.target}
