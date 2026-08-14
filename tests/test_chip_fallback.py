"""盤前抓不到當日籌碼時，要退回最近一個有資料的交易日。

2026-08-14 實測
---------------
    fetch_institutional_investors("20260814")  →      0 檔   （今天）
    fetch_institutional_investors("20260813")  → 15,923 檔
    fetch_institutional_investors("20260812")  → 15,452 檔

證交所的三大法人買賣超是**收盤後**才發布，所以 08:30 盤前抓當日必然是空的。
這是資料的本質，不是故障。

但 `_fetch_chip_data` 只抓當日、抓不到就回空 dict，於是每一檔的 chip 都是
None，AI 讀到「籌碼資料無法取得」就觸發它的鐵律而保守跳過：

    籌碼資料無法取得 + 大盤方向未知，違反鐵律第4條
    盤前已明確指出籌碼盲區無法確認主力意圖
    盤前明確指出缺少法人籌碼資料不敢進場

盤前唯一可得、也確實有參考價值的，就是**前一交易日**的法人動向——外資連買
幾日這種訊號本來就是看歷史。退回昨日資料遠勝於告訴 AI「什麼都沒有」。

回溯上限刻意設得短：超過幾天的籌碼對當沖沒有參考價值，硬湊反而比誠實說
「沒有」更糟。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _chip(n: int) -> dict:
    """模擬 n 檔的籌碼資料。"""
    return {f"{2000+i}": {"foreign_net": 100} for i in range(n)}


def test_falls_back_to_previous_trading_day():
    """當日空 → 取前一日。"""
    from daytrading_report import _fetch_chip_data

    calls = []

    def _fetch(d):
        calls.append(d)
        return _chip(15923) if d == "20260813" else {}

    with patch("chip_data.fetch_institutional_investors", side_effect=_fetch):
        result = _fetch_chip_data("20260814")

    assert len(result) == 15923
    assert calls[0] == "20260814", "仍要先試當日"
    assert "20260813" in calls, "當日沒有就要往前找"


def test_uses_today_when_available():
    """收盤後當日資料已發布，就用當日的，不可還去抓昨天。"""
    from daytrading_report import _fetch_chip_data

    calls = []

    def _fetch(d):
        calls.append(d)
        return _chip(15000)

    with patch("chip_data.fetch_institutional_investors", side_effect=_fetch):
        result = _fetch_chip_data("20260814")

    assert len(result) == 15000
    assert calls == ["20260814"], "當日有資料就不該再往前找"


def test_skips_weekend_and_keeps_looking():
    """週一盤前：週日、週六都沒有，要一路回到週五。"""
    from daytrading_report import _fetch_chip_data

    def _fetch(d):
        return _chip(15000) if d == "20260814" else {}   # 週五

    with patch("chip_data.fetch_institutional_investors", side_effect=_fetch):
        result = _fetch_chip_data("20260817")            # 週一

    assert len(result) == 15000


def test_gives_up_after_the_lookback_limit():
    """連續多日都沒有就誠實回空，不無限往前翻。

    太舊的籌碼對當沖沒有參考價值，硬湊比說「沒有」更糟。
    """
    from daytrading_report import _fetch_chip_data

    with patch("chip_data.fetch_institutional_investors", return_value={}):
        assert _fetch_chip_data("20260814") == {}


def test_fetch_error_does_not_propagate():
    """抓取拋例外時回空 dict，不可讓整份報告掛掉。"""
    from daytrading_report import _fetch_chip_data

    with patch("chip_data.fetch_institutional_investors",
               side_effect=RuntimeError("TWSE down")):
        assert _fetch_chip_data("20260814") == {}


def test_reports_which_day_the_data_came_from(caplog):
    """用的是哪一天的籌碼要留下紀錄——否則事後無從判斷 AI 當時看到什麼。"""
    import logging
    from daytrading_report import _fetch_chip_data

    def _fetch(d):
        return _chip(100) if d == "20260813" else {}

    with patch("chip_data.fetch_institutional_investors", side_effect=_fetch):
        with caplog.at_level(logging.INFO):
            _fetch_chip_data("20260814")

    assert "20260813" in caplog.text
