from __future__ import annotations

"""
dt_risk.py — 當日虧損熔斷（circuit breaker）

背景
----
當沖是高頻交易，單日連續停損可能在短時間內累積相當虧損。本模組提供：

  * get_today_dt_realized_pnl(db_path) — 加總「今日」當沖出場已實現損益
  * check_circuit_breaker(config, db_path) — 已實現虧損是否達熔斷門檻
  * 熔斷旗標持久化（JSON，跨 process 共享）：
      - is_circuit_breaker_active() — main.py 排程迴圈 / telegram_bot 都讀這個
      - set_circuit_breaker_flag()  — 觸發時寫入（含日期，跨日自動失效）
      - get_circuit_breaker_flag()  — 讀取旗標內容（給 Telegram 回覆原因用）

觸發後的實際動作（全平倉 / 停止進場 / Telegram 告警）由 main.py 接線
（_maybe_trigger_circuit_breaker），本模組只負責判斷與旗標持久化。

已實現損益的過濾規則
--------------------
daily_trades 中，DT 出場記錄目前有兩個實際寫入點：
  1. monitor_agent.py AlertWorker（tick 監控，auto_execute=True，只有 DT 的
     tick agent 會啟用）：action="sell", note="auto_exit", pnl=(exit-entry)*qty。
  2. main.py ForceCloseJob（13:25 強平，僅 SIMULATION 模式立即寫入確認 sell；
     真實模式寫 force_close_requested，pnl=None，成交未確認故不計入）：
     action="sell", note="force_close_simulation"，sector 沿用進場時寫入的
     sector（DT 買入固定寫 "當沖"，波段買入寫實際產業別），因此用
     sector=="當沖" 排除波段部位的強平損益。

_run_dt_sell_alerts（5 分鐘輪詢出場路徑）目前並未寫入 daily_trades pnl 記錄
（已知缺口，未在本任務範圍內修補，另見任務回報）。
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from atomic_json import atomic_read_json, atomic_write_json

log = logging.getLogger(__name__)

_FLAG_PATH = "data/dt_circuit_breaker.json"

# DT 出場記錄的 note 值（monitor_agent.AlertWorker 寫入）
_DT_AUTO_EXIT_NOTE = "auto_exit"
# 強制平倉模擬記錄的 note 值（main.py ForceCloseJob，僅 SIMULATION 模式）
_FORCE_CLOSE_SIM_NOTE = "force_close_simulation"
# DT 買入固定寫入的 sector 值（main.py _save_dt_buy_trade / telegram_bot._handle_dt_buy）
_DT_SECTOR = "當沖"


def _today() -> str:
    return date.today().isoformat()


# ── 已實現損益 ─────────────────────────────────────────────────────────────────

def get_today_dt_realized_pnl(db_path: str) -> float:
    """加總「今日」當沖出場已實現損益。

    只計入：
      - action == "sell"
      - pnl 非 None（未確認成交的賣單不計）
      - note == "auto_exit"（DT tick 監控自動出場）
        或 note == "force_close_simulation" 且 sector == "當沖"（DT 強平模擬）
    """
    from research_db import load_daily_trades

    trades = load_daily_trades(date.today(), db_path)
    total = 0.0
    for t in trades:
        if t.get("action") != "sell":
            continue
        pnl = t.get("pnl")
        if pnl is None:
            continue
        note = t.get("note")
        if note == _DT_AUTO_EXIT_NOTE:
            total += pnl
        elif note == _FORCE_CLOSE_SIM_NOTE and t.get("sector") == _DT_SECTOR:
            total += pnl
    return total


# ── 熔斷判斷 ───────────────────────────────────────────────────────────────────

@dataclass
class CircuitBreakerResult:
    triggered:    bool
    realized_pnl: float
    message:      str


def check_circuit_breaker(config, db_path: str) -> CircuitBreakerResult:
    """已實現虧損 <= -daily_max_loss 即觸發。"""
    realized_pnl = get_today_dt_realized_pnl(db_path)
    if realized_pnl <= -config.daily_max_loss:
        message = (
            f"當日已實現虧損 {realized_pnl:,.0f} 元，"
            f"達熔斷門檻 -{config.daily_max_loss:,.0f} 元"
        )
        return CircuitBreakerResult(triggered=True, realized_pnl=realized_pnl, message=message)
    return CircuitBreakerResult(triggered=False, realized_pnl=realized_pnl, message="")


# ── 旗標持久化（跨 process：main.py 排程迴圈寫入，telegram_bot 讀取）───────────

def is_circuit_breaker_active(path: Optional[str] = None) -> bool:
    """旗標是否生效：檔案存在、可解析、且日期為今日、且 triggered=True。

    跨日自動失效 — 昨日（或更早）寫入的旗標視為未觸發。

    ``path`` 預設為 None，實際使用時才解析模組層級的 ``_FLAG_PATH``（而非
    在函式定義時綁定預設值），讓測試可用 ``monkeypatch.setattr(dt_risk,
    "_FLAG_PATH", ...)`` 覆蓋，main.py / telegram_bot.py 也能共用同一個
    跨 process 的預設路徑。
    """
    data = atomic_read_json(path or _FLAG_PATH)
    if not isinstance(data, dict):
        return False
    return data.get("date") == _today() and bool(data.get("triggered"))


def get_circuit_breaker_flag(path: Optional[str] = None) -> dict:
    """讀取今日旗標內容；不存在或已跨日失效回傳空 dict。"""
    data = atomic_read_json(path or _FLAG_PATH)
    if not isinstance(data, dict) or data.get("date") != _today():
        return {}
    return data


def set_circuit_breaker_flag(realized_pnl: float, message: str, path: Optional[str] = None) -> None:
    """寫入今日熔斷旗標（atomic write，跨 process 安全）。"""
    path = path or _FLAG_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {
        "date":         _today(),
        "triggered":    True,
        "realized_pnl": realized_pnl,
        "message":      message,
        "triggered_at": datetime.now().isoformat(timespec="seconds"),
    })
