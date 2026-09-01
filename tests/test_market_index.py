"""Tests for market_index.fetch_market_index_change()（Shioaji 版）。

原本走 yfinance ^TWII，改為 Shioaji Contracts.Indexs.TSE["001"] 的 snapshot
change_rate。失敗一律回 0.0，讓 dt_rules 走「大盤缺值、不擋僅註記」的既有路徑。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from market_index import fetch_market_index_change


class _Snap:
    def __init__(self, change_rate, close=21800.0):
        self.code, self.close, self.change_rate = "001", close, change_rate
        self.change_price = 0.0
        self.total_volume = 0
        self.open = self.high = self.low = close


def _api(change_rate=1.0, raises=False):
    class C:
        pass
    class Api:
        def snapshots(self, contracts):
            if raises:
                raise RuntimeError("quote session down")
            return [_Snap(change_rate)]
    api = Api()
    api.Contracts = C()
    api.Contracts.Indexs = C()
    api.Contracts.Indexs.TSE = {"001": object()}
    return api


def test_returns_float():
    assert isinstance(fetch_market_index_change(api=_api(1.0)), float)


def test_returns_change_rate_from_snapshot():
    assert fetch_market_index_change(api=_api(1.0)) == pytest.approx(1.0)


def test_negative_change_preserved():
    """跌的時候不能變成 0.0——大盤翻空是 dt_rules 擋 long 的關鍵條件。"""
    assert fetch_market_index_change(api=_api(-1.25)) == pytest.approx(-1.25)


def test_zero_when_quote_session_down():
    assert fetch_market_index_change(api=_api(raises=True)) == 0.0


def test_zero_when_no_connection():
    """無連線時回 0.0 且不得拋出。"""
    with patch("shioaji_session.get_api", return_value=None):
        assert fetch_market_index_change() == 0.0


def test_uses_shared_session_when_api_not_passed():
    """未傳 api 時要走共用連線，不可自行 login（會多開 session）。"""
    with patch("shioaji_session.get_api", return_value=_api(2.5)) as g:
        assert fetch_market_index_change() == pytest.approx(2.5)
        assert g.called
