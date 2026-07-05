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

    analysis_count: int = 8
    """8:30 對技術評分前幾名做 AI 深度分析、並存成 watching 持倉的檔數。
    這個數字 = 9:05 開盤再確認的上限，也 = 紙上 / 真實進場的候選上限。"""

    display_count: int = 20
    """8:30 當沖預測訊息顯示、以及存進複盤 DB 的檔數（不觸發 AI 分析）。
    應 >= analysis_count；排名超過 analysis_count 的只顯示不追蹤。"""

    daily_max_loss: float = 3000.0
    """當日已實現虧損上限（元，正數）。當日當沖已實現損益 <= -daily_max_loss 時
    觸發熔斷（circuit breaker）：全平倉 active 持倉、當日停止新進場、Telegram 告警。"""

    risk_per_trade_pct: float = 1.0
    """單筆風險佔總資金百分比（%）。用於風險額倉位法：
    風險額 = 總資金 × risk_per_trade_pct / 100，股數 = 風險額 ÷ 每股風險
    （進場價 - 停損價）。"""

    llm_mode: str = "decider"
    """LLM 在當沖決策中的角色：
      "decider" （預設）：LLM 直接決定 action/entry（8:30）與 proceed（9:05），現狀行為。
      "advisor"          ：改由 dt_rules.py 的確定性規則決定，LLM 僅提供評論摘要，
                           不影響最終決策（可省略 9:05 LLM 呼叫節省成本）。
    非法值（不在上述兩者中）於載入時 fallback 為 "decider" 並記 log.warning。"""


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
        "analysis_count":       ("DT_ANALYSIS_COUNT",      int),
        "display_count":        ("DT_DISPLAY_COUNT",       int),
        "daily_max_loss":       ("DT_DAILY_MAX_LOSS",      float),
        "risk_per_trade_pct":   ("DT_RISK_PER_TRADE_PCT",  float),
        "llm_mode":             ("DT_LLM_MODE",            str),
    }
    for field, (env_key, cast) in env_map.items():
        val = os.getenv(env_key)
        if val is not None:
            defaults[field] = cast(val)

    # 2. JSON 覆蓋（UI 儲存的設定，優先於環境變數）
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # 只接受 dataclass 已知欄位；未知/舊欄位忽略並記錄 warning，避免 crash。
        unknown = [k for k in data if k not in defaults]
        if unknown:
            import logging
            logging.getLogger(__name__).warning(
                "load_daytrading_config 忽略未知欄位: %s", ", ".join(sorted(unknown))
            )
        defaults.update({k: v for k, v in data.items() if k in defaults})
    except FileNotFoundError:
        pass
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("load_daytrading_config JSON error: %s", e)

    # 3. llm_mode 合法性檢查（env 與 JSON 都可能帶入非法值）
    if defaults.get("llm_mode") not in ("decider", "advisor"):
        import logging
        logging.getLogger(__name__).warning(
            "load_daytrading_config llm_mode 不合法: %r，fallback 為 'decider'",
            defaults.get("llm_mode"),
        )
        defaults["llm_mode"] = "decider"

    return DaytradingConfig(**defaults)
