"""
tests/test_market_data_ready.py — 8:30 選股前的 Shioaji 行情就緒閘門

問題：ensure_connected 的 login(fetch_contract=True) 回傳只代表「登入成功」，
底層 solace 報價 session 仍在非同步暖機。8:30 全市場掃描
（_get_stock_universe → batch_fetch_snapshots）在真實模式無 yfinance 備援，
報價未就緒就抓到空清單 → 零候選 → dt_prediction_log 空。

wait_for_market_data 以一檔高流動性股票反覆探測 get_snapshot，就緒才放行。
"""
from unittest.mock import MagicMock, patch

import monitor_agent


def _snap(close):
    return {"close": close, "volume": 1000, "change_price": 0.0}


class TestWaitForMarketData:
    def test_ready_first_try(self):
        sleep = MagicMock()
        with patch("monitor_agent.get_snapshot", return_value=_snap(1000.0)) as gs:
            ok = monitor_agent.wait_for_market_data(
                MagicMock(), probe_code="2330", max_attempts=20,
                interval=3.0, sleep_fn=sleep,
            )
        assert ok is True
        assert gs.call_count == 1          # 一次就成功
        sleep.assert_not_called()          # 成功不 sleep

    def test_ready_after_retries(self):
        sleep = MagicMock()
        seq = [None, None, _snap(842.0)]   # 前兩次未就緒，第三次成功
        with patch("monitor_agent.get_snapshot", side_effect=seq) as gs:
            ok = monitor_agent.wait_for_market_data(
                MagicMock(), max_attempts=20, interval=3.0, sleep_fn=sleep,
            )
        assert ok is True
        assert gs.call_count == 3
        assert sleep.call_count == 2       # 兩次失敗後各等一次

    def test_never_ready_returns_false(self):
        sleep = MagicMock()
        with patch("monitor_agent.get_snapshot", return_value=None) as gs:
            ok = monitor_agent.wait_for_market_data(
                MagicMock(), max_attempts=5, interval=3.0, sleep_fn=sleep,
            )
        assert ok is False
        assert gs.call_count == 5           # 用盡 max_attempts
        assert sleep.call_count == 4        # 最後一次不 sleep（no sleep after final）

    def test_zero_close_is_not_ready(self):
        sleep = MagicMock()
        with patch("monitor_agent.get_snapshot", return_value=_snap(0.0)) as gs:
            ok = monitor_agent.wait_for_market_data(
                MagicMock(), max_attempts=3, interval=1.0, sleep_fn=sleep,
            )
        assert ok is False                  # close=0（Not ready 的典型回值）不算就緒
        assert gs.call_count == 3

    def test_none_close_is_not_ready(self):
        sleep = MagicMock()
        with patch("monitor_agent.get_snapshot", return_value={"close": None}):
            ok = monitor_agent.wait_for_market_data(
                MagicMock(), max_attempts=2, interval=1.0, sleep_fn=sleep,
            )
        assert ok is False

    def test_api_none_returns_false_without_probing(self):
        sleep = MagicMock()
        with patch("monitor_agent.get_snapshot") as gs:
            ok = monitor_agent.wait_for_market_data(None, sleep_fn=sleep)
        assert ok is False
        gs.assert_not_called()              # api 不存在不探測
        sleep.assert_not_called()
