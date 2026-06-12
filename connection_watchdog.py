from __future__ import annotations

"""
connection_watchdog.py — Shioaji 連線狀態機 + Telegram 推送

狀態機：UNKNOWN → CONNECTED → DISCONNECTED（→ CONNECTED 再連）

公開介面：
  report_success()               ← polling 成功時呼叫
  report_failure(reason: str)    ← polling 失敗時呼叫
  get_state() -> str             ← 回傳目前狀態（供測試/查詢用）

設計原則：
  - thread-safe（_lock 保護所有狀態）
  - report_*() 永遠不 raise（notify 失敗靜默吞掉）
  - 防抖：WATCHDOG_FAILURE_THRESHOLD（預設 2）次連續失敗才轉 DISCONNECTED
  - 速率限制：同一狀態每 10 分鐘最多推一次（_RATE_LIMIT_SEC = 600）
  - push via lazy import telegram_bot.send_text，TELEGRAM_CHAT_ID 從 env 讀取
"""

import os
import threading
import time

# ── 常數 ──────────────────────────────────────────────────────────────────────

_RATE_LIMIT_SEC: int = 600  # 10 分鐘

_STATE_UNKNOWN      = "UNKNOWN"
_STATE_CONNECTED    = "CONNECTED"
_STATE_DISCONNECTED = "DISCONNECTED"

# ── module-level 狀態（受 _lock 保護）────────────────────────────────────────

_lock               = threading.Lock()
_state: str         = _STATE_UNKNOWN
_failure_count: int = 0
# 上次各類型推送的 time.time()（用於速率限制）
# 使用三個獨立 key：初次連線 / 斷線 / 重新連線
_PUSH_CONNECTED    = "connected"
_PUSH_DISCONNECTED = "disconnected"
_PUSH_RECONNECTED  = "reconnected"
_last_push: dict[str, float] = {
    _PUSH_CONNECTED:    0.0,
    _PUSH_DISCONNECTED: 0.0,
    _PUSH_RECONNECTED:  0.0,
}


def _get_threshold() -> int:
    """從環境變數讀取防抖門檻（預設 2）。"""
    try:
        return max(1, int(os.environ.get("WATCHDOG_FAILURE_THRESHOLD", "2")))
    except ValueError:
        return 2


def _notify(message: str) -> None:
    """推送 Telegram 訊息（lazy import；失敗時靜默）。

    外部測試可 patch 此函式以攔截推送內容。
    """
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        return
    try:
        from telegram_bot import send_text  # lazy import
        send_text(chat_id, message)
    except Exception:
        pass  # 推送失敗一律靜默


def _try_push(push_key: str, message: str) -> None:
    """在速率限制允許時呼叫 _notify()，並更新最後推送時間。

    push_key 使用 _PUSH_* 常數，與狀態名稱解耦，
    使初次連線、斷線、重新連線各有獨立的速率視窗。
    """
    global _last_push
    now = time.time()
    if now - _last_push.get(push_key, 0.0) >= _RATE_LIMIT_SEC:
        _last_push[push_key] = now
        try:
            _notify(message)
        except Exception:
            pass  # _notify 本身已靜默，此處雙重保護


# ── 公開 API ──────────────────────────────────────────────────────────────────

def get_state() -> str:
    """回傳目前連線狀態字串（供測試 / 查詢）。"""
    with _lock:
        return _state


def report_success() -> None:
    """Shioaji polling 成功時呼叫。

    - UNKNOWN  → CONNECTED：推「已連線」
    - DISCONNECTED → CONNECTED：推「已重新連線」
    - CONNECTED → CONNECTED：重設失敗計數；速率限制下不重複推送
    """
    global _state, _failure_count
    try:
        with _lock:
            _failure_count = 0  # 成功 → 計數器歸零
            prev = _state

            if prev == _STATE_UNKNOWN:
                _state = _STATE_CONNECTED
                _try_push(_PUSH_CONNECTED, "🟢 Shioaji 已連線")

            elif prev == _STATE_DISCONNECTED:
                _state = _STATE_CONNECTED
                # 重新連線：使用獨立 key，與初次連線速率視窗分開
                _try_push(_PUSH_RECONNECTED, "🟢 Shioaji 已重新連線")

            # CONNECTED → CONNECTED：不推送（速率限制已在 _try_push 處理）
    except Exception:
        pass  # 狀態機內部錯誤靜默


def report_failure(reason: str = "") -> None:
    """Shioaji polling 失敗時呼叫。

    連續失敗達到 WATCHDOG_FAILURE_THRESHOLD 次才轉 DISCONNECTED 並推送斷線通知。
    成功後計數器歸零。
    """
    global _state, _failure_count
    try:
        with _lock:
            if _state == _STATE_DISCONNECTED:
                # 已是斷線狀態，不做任何轉移
                return

            _failure_count += 1
            threshold = _get_threshold()

            if _failure_count >= threshold:
                prev = _state
                _state = _STATE_DISCONNECTED
                msg = f"🔴 Shioaji 斷線：{reason}（系統降級運行中）"
                _try_push(_PUSH_DISCONNECTED, msg)
    except Exception:
        pass  # 狀態機內部錯誤靜默
