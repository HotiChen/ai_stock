"""
daytrading_config.py — 當沖下單參數設定

從 .env / 環境變數載入，預設值採保守設定：
  - 停損   ：-3%
  - 天花板  ：+9%（漲停前出場）
  - 追蹤停利：漲 2% 後啟動，從峰值回落 0.5% 觸發
  - 強制平倉：13:00（避免交割）
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class DaytradingConfig:
    """當沖交易執行參數。所有百分比均以「正數」表示幅度（如 3.0 = 3%）。"""

    budget_per_stock: float
    """DT_BUDGET：每支股票當沖預算（元），預設 30,000。"""

    stop_loss_pct: float
    """DT_STOP_LOSS_PCT：停損觸發幅度（%），預設 3.0。
    從買入價下跌 stop_loss_pct% 立即賣出。"""

    take_profit_pct: float
    """DT_TAKE_PROFIT_PCT：天花板停利幅度（%），預設 9.0。
    漲幅達 take_profit_pct% 立即賣出（漲停前出場）。"""

    trailing_start_pct: float
    """DT_TRAILING_START_PCT：追蹤停利啟動門檻（%），預設 2.0。
    當漲幅 >= trailing_start_pct 時，啟動追蹤停利機制。"""

    trailing_stop_pct: float
    """DT_TRAILING_STOP_PCT：從峰值回落多少 % 觸發停利（%），預設 0.5。
    例：漲到 +4%（峰值），回落 0.5% 跌至 +3.5% → 賣出。"""

    force_close_time: str
    """DT_FORCE_CLOSE_TIME：強制平倉時間 "HH:MM"，預設 "13:00"。
    到達此時間無條件賣出所有當沖倉位，避免產生交割義務。"""

    require_manual_confirm: bool
    """DT_MANUAL_CONFIRM：買入前是否需 Telegram 手動確認，預設 True。
    True = 發送確認鍵盤，等待按下才下買單。
    False = 自動直接下買單（需謹慎啟用）。"""


def load_daytrading_config() -> DaytradingConfig:
    """從環境變數建立 DaytradingConfig，缺少時使用安全預設值。"""
    return DaytradingConfig(
        budget_per_stock=float(os.getenv("DT_BUDGET", "30000")),
        stop_loss_pct=float(os.getenv("DT_STOP_LOSS_PCT", "3.0")),
        take_profit_pct=float(os.getenv("DT_TAKE_PROFIT_PCT", "9.0")),
        trailing_start_pct=float(os.getenv("DT_TRAILING_START_PCT", "2.0")),
        trailing_stop_pct=float(os.getenv("DT_TRAILING_STOP_PCT", "0.5")),
        force_close_time=os.getenv("DT_FORCE_CLOSE_TIME", "13:00"),
        require_manual_confirm=os.getenv("DT_MANUAL_CONFIRM", "true").lower() == "true",
    )
