"""
shioaji_session.py — 全專案共用的 Shioaji 連線

為什麼需要
----------
monitor_agent.ensure_connected() 每次呼叫都建立新的 sj.Shioaji() 並 login()。
把 yfinance 全面換成 Shioaji 之後，十幾個模組都需要 api；若各自呼叫
ensure_connected 就會開十幾條連線——券商有連線數上限，且每次登入要數秒。
（正式環境 log 早就出現過兩行 "Shioaji connected"，就是這個問題的徵兆。）

用法
----
    import shioaji_session
    api = shioaji_session.get_api()          # 需要時才登入，之後共用
    api = shioaji_session.get_api(connect=False)   # 有就用，沒有不主動登入

main.py 啟動時已經連好線，應呼叫 ``set_api(api)`` 註冊進來，避免重複登入。

失敗冷卻
--------
連線失敗後 FAILURE_COOLDOWN_SEC 秒內不重試。沒有這層保護的話，每一次查價
都會觸發一次登入嘗試（且每次都要等 timeout），整個流程會被拖垮——這是把
「查價」從 yfinance 換成 Shioaji 之後才會出現的新風險。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

#: 連線失敗後的冷卻秒數，期間內不再嘗試登入
FAILURE_COOLDOWN_SEC = 300

_lock = threading.Lock()
_api = None
_last_failure_at: Optional[float] = None
#: 最後一次連線失敗的原因。ensure_connected 會吞掉例外並回傳 None，
#: 呼叫端只知道「失敗了」卻不知道為什麼——而原因往往決定該怎麼修
#: （SDK 版本過舊 vs 憑證錯誤，處理方式完全不同）。
_last_error: Optional[str] = None


def _now() -> float:
    """獨立成函式讓測試可控制時間（不需要真的 sleep 300 秒）。"""
    return time.time()


def last_error() -> Optional[str]:
    """最後一次連線失敗的原因（成功後清空）。"""
    return _last_error


def _connect():
    """實際登入。獨立成函式讓測試可替換，避免碰到真實券商。"""
    from monitor_agent import ensure_connected, last_login_error
    api = ensure_connected(
        os.getenv("SHIOAJI_API_KEY", ""),
        os.getenv("SHIOAJI_SECRET_KEY", ""),
        simulation=os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true",
    )
    if api is None:
        # ensure_connected 吞掉例外只回 None；把原因撈回來供 doctor 判讀。
        global _last_error
        _last_error = last_login_error()
    return api


def set_api(api) -> None:
    """註冊一個既有連線（main.py 啟動後呼叫），或傳 None 清除。"""
    global _api, _last_failure_at, _last_error
    with _lock:
        _api = api
        if api is not None:
            _last_failure_at = None
            _last_error = None


def get_api(connect: bool = True):
    """取得共用連線。

    connect=False：有快取就回傳，沒有則回 None 且**不會嘗試登入**。
    給「有就用、沒有就算了」的呼叫端使用（例如純顯示用途），避免一個
    無關緊要的查詢觸發數秒的登入。
    """
    global _api, _last_failure_at, _last_error
    with _lock:
        if _api is not None:
            return _api
        if not connect:
            return None
        if (_last_failure_at is not None
                and _now() - _last_failure_at < FAILURE_COOLDOWN_SEC):
            return None

        try:
            api = _connect()
        except Exception as e:
            _last_error = str(e)
            _last_failure_at = _now()
            log.warning("Shioaji 連線失敗（%s），%d 秒內不再重試",
                        e, FAILURE_COOLDOWN_SEC)
            return None
        if api is None:
            _last_failure_at = _now()
            log.warning("Shioaji 連線失敗，%d 秒內不再重試", FAILURE_COOLDOWN_SEC)
            return None
        _api = api
        _last_error = None
        return _api


def reconnect():
    """強制重新登入（清掉快取與冷卻）。

    冷卻期是為了防止「每次查價都重試登入」的風暴，但 token 過期後的主動
    重連是明確的補救動作，不該被冷卻擋住——所以這裡直接繞過。
    """
    global _api, _last_failure_at, _last_error
    with _lock:
        _api = None
        _last_failure_at = None
        _last_error = None
    log.warning("Shioaji 重新登入中（token 過期或連線異常）")
    return get_api()


def reset() -> None:
    """清空快取與冷卻狀態（測試用；正式流程不需要）。"""
    global _api, _last_failure_at, _last_error
    with _lock:
        _api = None
        _last_failure_at = None
        _last_error = None
