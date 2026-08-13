"""大盤／期貨資料缺漏時，當沖評分不可拋例外。

2026-08-13 實際故障
-------------------
    08:32:33 DayTrading report push failed:
             '>=' not supported between instances of 'NoneType' and 'float'
    09:05:15 DT 9:05 確認：無 watching 持倉
    09:10:18 DT 9:10 進場：無 watching 持倉（9:05 全數過濾）

當沖報告在評分階段就整個炸掉，當天沒有任何推播、沒有候選、沒有進場。

肇因是前一晚（cf208c2）把 ``futures_premium_pct`` 從「取不到就 0.0」改成
「取不到就 None」，但 ``_assess_day_trading`` 裡仍寫著：

    futures_pct = market.get("futures_premium_pct", 0.0)
    if futures_pct >= 0.3:

``dict.get(key, default)`` 的 default **只在 key 不存在時生效**；key 存在而值是
None 時回傳的就是 None。於是 ``None >= 0.3`` 直接 TypeError。

這個測試把「market 各種缺漏組合都不可炸」釘死，避免同類改動再次全鏈路癱瘓。
"""

from __future__ import annotations

import pytest


def _indicators() -> dict:
    return {
        "rsi": 55.0,
        "volume_ratio": 2.0,
        "atr_pct": 5.0,
        "close": 100.0,
        "ma5": 99.0,
        "ma20": 98.0,
        "vwap": 99.5,
    }


def _assess(market):
    from stock_query import _assess_day_trading
    return _assess_day_trading(_indicators(), chip=None, market=market)


# ── 這是 8/13 當天實際傳進去的 market ─────────────────────────────────────────

def test_both_index_and_futures_unavailable():
    """重現故障：兩者皆 None（Shioaji 與 yfinance 都取不到）。"""
    result = _assess({
        "index_change_pct": None,
        "futures_premium_pct": None,
        "index_available": False,
        "futures_available": False,
    })
    assert isinstance(result.get("score"), (int, float))


def test_futures_unavailable_but_index_present():
    """只有期貨缺——8/13 修好指數之後最可能的組合。"""
    result = _assess({
        "index_change_pct": 0.88,
        "futures_premium_pct": None,
        "index_available": True,
        "futures_available": False,
    })
    assert isinstance(result.get("score"), (int, float))


def test_index_unavailable_but_futures_present():
    result = _assess({
        "index_change_pct": None,
        "futures_premium_pct": 1.31,
        "index_available": False,
        "futures_available": True,
    })
    assert isinstance(result.get("score"), (int, float))


# ── 邊界 ─────────────────────────────────────────────────────────────────────

def test_market_is_none_entirely():
    assert isinstance(_assess(None).get("score"), (int, float))


def test_market_is_empty_dict():
    assert isinstance(_assess({}).get("score"), (int, float))


# ── 缺漏不可影響評分 ──────────────────────────────────────────────────────────

def test_missing_data_does_not_change_score_versus_no_market():
    """資料缺漏時應完全不做大盤/期貨加權，分數要與『沒有 market』一致。

    若缺漏被當成 0%，會走到「平盤」那條規則而改變分數——那正是要避免的。
    """
    baseline = _assess(None)["score"]
    missing = _assess({
        "index_change_pct": None,
        "futures_premium_pct": None,
    })["score"]
    assert missing == baseline


def test_real_values_still_score_normally():
    """反向確認：有真實資料時加權照常運作，守衛沒把好路徑一起擋掉。"""
    strong = _assess({"index_change_pct": 1.5, "futures_premium_pct": 0.5})
    weak = _assess({"index_change_pct": -1.5, "futures_premium_pct": 0.0})
    assert strong["score"] > weak["score"]
