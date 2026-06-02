"""Shioaji 即時報價 service — 給 FastAPI backend 用的單例 + 價格快取。

與 main.py scheduler 共存：兩個 process 各自登入同帳號，Shioaji 允許
多 session，若登入失敗直接回傳空 dict（呼叫方降級回 DB 儲存的報價）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

_lock = threading.Lock()
_api = None
_last_try: float = 0.0
_RETRY_COOLDOWN = 60.0  # 登入失敗後 60 秒內不重試

# {code: (price, timestamp)}
_price_cache: dict[str, tuple[float, float]] = {}
_CACHE_TTL = 30.0  # 秒：30 秒內同 code 不重打 API


def _connect() -> Optional[object]:
    global _api, _last_try
    now = time.time()
    if now - _last_try < _RETRY_COOLDOWN:
        return None
    _last_try = now

    api_key = os.getenv("SHIOAJI_API_KEY", "")
    secret_key = os.getenv("SHIOAJI_SECRET_KEY", "")
    if not api_key or not secret_key:
        log.debug("ShioajiService: no API keys, skipping")
        return None

    simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() != "false"
    try:
        import shioaji as sj
        api = sj.Shioaji(simulation=simulation)
        api.login(api_key=api_key, secret_key=secret_key, fetch_contract=True)
        _api = api
        log.info("ShioajiService connected (simulation=%s)", simulation)
        return _api
    except Exception as e:
        log.warning("ShioajiService login failed: %s", e)
        return None


def _get_api() -> Optional[object]:
    global _api
    with _lock:
        if _api is not None:
            return _api
        return _connect()


def get_live_prices(codes: list[str]) -> dict[str, float]:
    """
    回傳 {code: close_price}。
    - 30 秒內同 code 用快取
    - Shioaji 不可用時回傳空 dict（呼叫方用 DB 報價）
    """
    result: dict[str, float] = {}
    now = time.time()
    uncached: list[str] = []

    for code in codes:
        cached = _price_cache.get(code)
        if cached and now - cached[1] < _CACHE_TTL:
            result[code] = cached[0]
        else:
            uncached.append(code)

    if not uncached:
        return result

    api = _get_api()
    if api is None:
        return result

    try:
        contracts = [api.Contracts.Stocks.get(c) for c in uncached]
        contracts = [c for c in contracts if c is not None]
        if not contracts:
            return result
        snaps = api.snapshots(contracts)
        for s in snaps:
            price = float(getattr(s, "close", 0) or 0)
            if price > 0:
                _price_cache[s.code] = (price, now)
                result[s.code] = price
    except Exception as e:
        log.warning("ShioajiService get_live_prices error: %s — reconnecting next call", e)
        global _api
        with _lock:
            _api = None  # 下次重新登入

    return result
