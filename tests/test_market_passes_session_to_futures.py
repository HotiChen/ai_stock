"""_fetch_market 必須把 Shioaji session 交給期貨溢貼水。

2026-08-12 起連續多日
---------------------
每天 log 都有這行：

    WARNING: 台指期溢貼水取不到——標示為『資料不可用』，不以 0%% 代替

先前查到 `api.Contracts.Futures.TXF` 是合約集合、要用 TXFR1 才拿得到報價，
於是修了 futures_premium._near_month_txf。但情況沒有改善。

原因是那條路在正式環境根本沒被走到：

    daytrading_report.py:145   fp = fetch_futures_premium()

沒有傳 api。fetch_futures_price 的第一段 `if api is not None` 直接跳過，
落到 TWSE MIS 退路——而 MIS 是股票報價服務，不供期貨：

    tse_TXFB5.tw   rtcode=0000  msgArray=1  z='-'
    tse_TXFH6.tw   rtcode=0000  msgArray=1  z='-'
    otc_TXFH6.tw   rtcode=0000  msgArray=1  z='-'

四種寫法都回 '-'。所以那條退路無論合約代碼對不對都不會有值。

同一支 _fetch_market 裡的大盤指數是自己建 session 的（market_index），
期貨只要拿同一個就好。實測 2026-08-19 11:45 傳進去之後：

    FuturesPremium(spot_index=44720.65, futures_price=44827.0,
                   premium=106.35, premium_pct=0.238, label='溢價 106 點（+0.24%）')
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── market_index 要提供可共用的 session ───────────────────────────────────────

def test_market_index_exposes_a_shared_session():
    import market_index

    assert hasattr(market_index, "get_session"), \
        "market_index 沒有公開的 session 取得方式，呼叫端只能去碰底線開頭的私有函式"


def test_shared_session_is_the_cached_one():
    """不可每次呼叫都重登——大盤與期貨要共用同一條連線。"""
    import market_index

    api = MagicMock()
    with patch.object(market_index, "_build_index_api", return_value=api) as build:
        market_index._index_api = None
        first = market_index.get_session()
        second = market_index.get_session()

    assert first is second is api
    assert build.call_count == 1, "同一天內重複建立連線"


# ── 這是 2026-08-12 起期貨一直取不到的原因 ────────────────────────────────────

def test_fetch_market_hands_the_session_to_futures():
    import daytrading_report

    api = MagicMock()
    with patch("market_index.get_session", return_value=api), \
         patch("market_index.fetch_market_index_pct", return_value=0.5), \
         patch("futures_premium.fetch_futures_premium") as fetch:
        fetch.return_value = None
        daytrading_report._fetch_market()

    assert fetch.called
    args, kwargs = fetch.call_args
    passed = list(args) + list(kwargs.values())
    assert api in passed, (
        f"fetch_futures_premium 沒有收到 Shioaji session，只會落到不供期貨的 "
        f"TWSE MIS 退路。實際參數：{passed}"
    )


def test_a_working_session_makes_futures_available():
    import daytrading_report
    from futures_premium import FuturesPremium

    fp = FuturesPremium(spot_index=44720.65, futures_price=44827.0,
                        premium=106.35, premium_pct=0.238, label="溢價 106 點（+0.24%）")
    with patch("market_index.get_session", return_value=MagicMock()), \
         patch("market_index.fetch_market_index_pct", return_value=0.5), \
         patch("futures_premium.fetch_futures_premium", return_value=fp):
        m = daytrading_report._fetch_market()

    assert m["futures_available"] is True
    assert m["futures_premium_pct"] == pytest.approx(0.238)


# ── 拿不到 session 時不可改成 0 ───────────────────────────────────────────────

def test_no_session_still_reports_unavailable_not_zero():
    """整條路都失敗時要維持 None + futures_available=False。"""
    import daytrading_report

    with patch("market_index.get_session", return_value=None), \
         patch("market_index.fetch_market_index_pct", return_value=None), \
         patch("futures_premium.fetch_futures_premium", return_value=None):
        m = daytrading_report._fetch_market()

    assert m["futures_premium_pct"] is None
    assert m["futures_available"] is False
