"""
daytrading_config.py — 當沖下單參數設定

優先從 data/daytrading_config.json 載入（UI 儲存的設定）；
JSON 不存在時退而求其次讀 .env / 環境變數，再無則用安全預設值。

參數說明：
  stop_loss_pct         : 停損（跌幾 % 認賠）
  take_profit_pct       : 天花板停利（漲幾 % 立即出場）
  trailing_start_pct    : 移動停損啟動門檻（漲到幾 % 才開始跟蹤）
  trailing_gap_pct      : 移動停損距最高點的間距（最高點下方幾 %）
  force_close_time      : 強制平倉時間（HH:MM）
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_PATH = "data/daytrading_config.json"


@dataclass
class DaytradingConfig:
    """當沖交易執行參數。所有百分比均以「正數」表示幅度（如 3.0 = 3%）。"""

    budget_per_stock: float = 30_000.0
    """每支股票當沖預算（元）。"""

    stop_loss_pct: float = 3.0
    """停損觸發幅度（%）。從買入價下跌此 % 立即賣出。"""

    take_profit_pct: float = 9.0
    """天花板停利幅度（%）。漲幅達此 % 立即賣出（漲停前出場）。"""

    trailing_start_pct: float = 3.0
    """移動停損啟動門檻（%）。漲幅 >= 此值才開始用移動停損保護獲利。"""

    trailing_gap_pct: float = 2.0
    """移動停損間距（%）。停損點永遠在最高點下方此 %。
    例：最高點 +5%，停損跟在 +3%（= 5 - 2）。"""

    force_close_time: str = "13:00"
    """強制平倉時間 "HH:MM"。到達此時間無條件賣出，避免產生交割義務。"""

    require_manual_confirm: bool = True
    """買入前是否需 Telegram 手動確認。False = 自動直接下買單（謹慎啟用）。"""

    paper_trade_only: bool = False
    """純紙上追蹤模式。True = 9:10 不下任何委託，只以市價紙上進場並追蹤出場
    邏輯、收盤結算損益（與真實下單路徑完全隔離）。預設關閉。"""


# ── Persistence ───────────────────────────────────────────────────────────────

def save_daytrading_config(cfg: DaytradingConfig, path: str = _DEFAULT_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)


def load_daytrading_config(path: str = _DEFAULT_PATH) -> DaytradingConfig:
    """JSON → 環境變數 → 預設值，三層 fallback。"""
    defaults = asdict(DaytradingConfig())

    # 1. 環境變數覆蓋預設值
    env_map = {
        "budget_per_stock":     ("DT_BUDGET",             float),
        "stop_loss_pct":        ("DT_STOP_LOSS_PCT",       float),
        "take_profit_pct":      ("DT_TAKE_PROFIT_PCT",     float),
        "trailing_start_pct":   ("DT_TRAILING_START_PCT",  float),
        "trailing_gap_pct":     ("DT_TRAILING_GAP_PCT",    float),
        "force_close_time":     ("DT_FORCE_CLOSE_TIME",    str),
        "require_manual_confirm": ("DT_MANUAL_CONFIRM",    lambda v: v.lower() == "true"),
        "paper_trade_only":     ("DT_PAPER_ONLY",          lambda v: v.lower() == "true"),
    }
    for field, (env_key, cast) in env_map.items():
        val = os.getenv(env_key)
        if val is not None:
            defaults[field] = cast(val)

    # 2. JSON 覆蓋（UI 儲存的設定，優先於環境變數）
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        defaults.update(data)
    except FileNotFoundError:
        pass
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("load_daytrading_config JSON error: %s", e)

    return DaytradingConfig(**defaults)
