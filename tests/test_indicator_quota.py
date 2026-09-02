"""
tests/test_indicator_quota.py — 8:30 選股不得燒光 Shioaji 每日額度

2026-09-02 實況：一次回填用掉 506/500 MB，當天 8:30 選股再也拿不到任何指標。
即使沒有回填，8:30 本身就會超標——50 支候選 × 150 天分鐘 K ≈ 600 MB。

修法：指標改走「增量快取的日線」，昨天抓過的不再重抓。抓不到時退回快取的
舊資料，而不是整支候選作廢。
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

pd = pytest.importorskip("pandas")


def _daily(n=120):
    import numpy as np
    idx = pd.date_range(end=pd.Timestamp("2026-09-02"), periods=n, freq="D")
    closes = 100.0 + np.cumsum(np.random.randn(n) * 0.3)
    return pd.DataFrame({
        "Open": closes - 0.2, "High": closes + 1.0, "Low": closes - 1.0,
        "Close": closes, "Volume": [1_000_000.0] * n,
    }, index=idx)


class TestGetIndicatorsUsesCache:
    def test_cached_daily_path_tried_first(self):
        """★ 必須先走增量快取，不能每支都抓 150 天分鐘 K。"""
        from daytrading_report import _get_indicators
        with patch("shioaji_history.fetch_daily_cached",
                   return_value=_daily()) as cached, \
             patch("technical_indicators.fetch_indicators") as raw:
            ind = _get_indicators("2330", api=MagicMock())
        assert cached.called, "應先嘗試增量快取"
        assert not raw.called, "快取成功時不得再打昂貴的 150 天 kbars"
        assert ind is not None and "RSI" in ind

    def test_falls_back_to_raw_fetch_when_cache_path_fails(self):
        from daytrading_report import _get_indicators
        with patch("shioaji_history.fetch_daily_cached", return_value=None), \
             patch("technical_indicators.fetch_indicators",
                   return_value={"RSI": 55.0}) as raw:
            ind = _get_indicators("2330", api=MagicMock())
        assert raw.called
        assert ind == {"RSI": 55.0}

    def test_returns_none_when_both_paths_fail(self):
        from daytrading_report import _get_indicators
        with patch("shioaji_history.fetch_daily_cached", return_value=None), \
             patch("technical_indicators.fetch_indicators", return_value=None):
            assert _get_indicators("2330", api=MagicMock()) is None

    def test_no_connection_returns_none_without_fetching(self):
        from daytrading_report import _get_indicators
        with patch("shioaji_session.get_api", return_value=None), \
             patch("shioaji_history.fetch_daily_cached") as cached:
            assert _get_indicators("2330") is None
        assert not cached.called


class TestBatchUsesCache:
    def test_batch_uses_cached_daily(self):
        from technical_indicators import fetch_indicators_shioaji_batch
        with patch("shioaji_history.fetch_daily_cached",
                   return_value=_daily()) as cached:
            out = fetch_indicators_shioaji_batch(["2330", "2454"],
                                                 api=MagicMock())
        assert cached.call_count == 2
        assert set(out) == {"2330", "2454"}
