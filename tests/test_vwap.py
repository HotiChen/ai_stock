"""tests/test_vwap.py — TDD tests for VWAP feature (PR-2)"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


# ── helpers ───────────────────────────────────────────────────────────────────

def _arrays(n=5, price=100.0, volume=1000.0):
    closes  = np.full(n, price)
    highs   = closes + 2.0
    lows    = closes - 2.0
    volumes = np.full(n, volume)
    return highs, lows, closes, volumes


def _make_df(n: int = 100) -> pd.DataFrame:
    closes  = np.linspace(80, 120, n)
    highs   = closes + 2
    lows    = closes - 2
    volumes = np.full(n, 1_000_000.0)
    dates   = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Close": closes, "High": highs, "Low": lows, "Volume": volumes},
        index=dates,
    )


# ── calc_vwap ─────────────────────────────────────────────────────────────────

class TestCalcVwap:
    def test_equal_volumes_returns_avg_typical_price(self):
        from technical_indicators import calc_vwap
        highs   = np.array([102.0, 104.0])
        lows    = np.array([98.0,  96.0])
        closes  = np.array([100.0, 100.0])
        volumes = np.array([1000.0, 1000.0])
        # typical = (102+98+100)/3=100, (104+96+100)/3=100 → VWAP = 100
        assert calc_vwap(highs, lows, closes, volumes) == pytest.approx(100.0)

    def test_high_volume_bar_dominates(self):
        from technical_indicators import calc_vwap
        highs   = np.array([102.0, 112.0])
        lows    = np.array([98.0,  108.0])
        closes  = np.array([100.0, 110.0])  # typical: 100, 110
        volumes = np.array([1.0, 1000.0])   # second bar dominates
        result = calc_vwap(highs, lows, closes, volumes)
        assert result > 109.0  # should be very close to 110

    def test_zero_volume_returns_last_close(self):
        from technical_indicators import calc_vwap
        highs, lows, closes, _ = _arrays(3, price=50.0)
        volumes = np.zeros(3)
        result = calc_vwap(highs, lows, closes, volumes)
        assert result == pytest.approx(50.0)

    def test_empty_arrays_returns_zero(self):
        from technical_indicators import calc_vwap
        result = calc_vwap(np.array([]), np.array([]), np.array([]), np.array([]))
        assert result == 0.0

    def test_single_bar(self):
        from technical_indicators import calc_vwap
        result = calc_vwap(
            np.array([105.0]), np.array([95.0]),
            np.array([100.0]), np.array([500.0]),
        )
        # typical = (105+95+100)/3 = 100
        assert result == pytest.approx(100.0)

    def test_returns_float(self):
        from technical_indicators import calc_vwap
        h, l, c, v = _arrays()
        assert isinstance(calc_vwap(h, l, c, v), float)

    def test_vwap_between_low_and_high(self):
        from technical_indicators import calc_vwap
        h, l, c, v = _arrays(20, price=100.0)
        result = calc_vwap(h, l, c, v)
        assert l[0] <= result <= h[0]


# ── VWAP in calculate_indicators ─────────────────────────────────────────────

class TestVwapInCalculateIndicators:
    def test_vwap_key_present(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        assert "VWAP" in result

    def test_vwap_is_float(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        assert isinstance(result["VWAP"], float)

    def test_vwap_between_period_low_and_high(self):
        from technical_indicators import calculate_indicators
        df = _make_df()
        result = calculate_indicators(df)
        # VWAP must be within the range of data
        assert result["VWAP"] > 0
        assert result["VWAP"] <= df["High"].max() + 1
        assert result["VWAP"] >= df["Low"].min() - 1

    def test_vwap_positive(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        assert result["VWAP"] > 0


# ── fetch_intraday_vwap ───────────────────────────────────────────────────────

class TestFetchIntradayVwap:
    def _make_intraday_df(self, n=60):
        closes  = np.linspace(100.0, 102.0, n)
        highs   = closes + 0.5
        lows    = closes - 0.5
        volumes = np.full(n, 500.0)
        times   = pd.date_range("2026-05-17 09:00", periods=n, freq="1min")
        return pd.DataFrame(
            {"Close": closes, "High": highs, "Low": lows, "Volume": volumes},
            index=times,
        )

    # yfinance 備援已移除（台股分鐘 K 在 yfinance 上延遲且常缺值，算出的
    # VWAP 與券商端對不起來）。以下改為驗證「只走 Shioaji，取不到就 None」。

    def test_api_none_uses_shared_session(self):
        """未傳 api 時要向 shioaji_session 取共用連線，不可自行 login。"""
        from technical_indicators import fetch_intraday_vwap
        with patch("shioaji_session.get_api", return_value=None) as g:
            assert fetch_intraday_vwap("2330", api=None) is None
        assert g.called

    def test_returns_none_when_no_connection(self):
        from technical_indicators import fetch_intraday_vwap
        with patch("shioaji_session.get_api", return_value=None):
            assert fetch_intraday_vwap("2330") is None

    def test_returns_none_when_kbars_empty(self):
        from technical_indicators import fetch_intraday_vwap
        api = MagicMock()
        api.Contracts.Stocks.get.return_value = MagicMock()
        api.kbars.return_value = {"ts": []}
        assert fetch_intraday_vwap("2330", api=api) is None

    def test_returns_none_when_kbars_raises(self):
        from technical_indicators import fetch_intraday_vwap
        api = MagicMock()
        api.Contracts.Stocks.get.return_value = MagicMock()
        api.kbars.side_effect = Exception("quote session not ready")
        assert fetch_intraday_vwap("2330", api=api) is None

    def test_shioaji_success_returns_float(self):
        from technical_indicators import fetch_intraday_vwap
        n = 60
        prices = np.linspace(100.0, 102.0, n).tolist()
        api = MagicMock()
        api.Contracts.Stocks.get.return_value = MagicMock()
        api.kbars.return_value = {
            "ts": list(range(n)),
            "Close": prices,
            "High": [p + 0.5 for p in prices],
            "Low":  [p - 0.5 for p in prices],
            "Volume": [500.0] * n,
        }
        result = fetch_intraday_vwap("2330", api=api)
        assert isinstance(result, float)
        assert result > 0

    def test_shioaji_failure_returns_none_no_fallback(self):
        """★ 移除 yfinance 備援後，Shioaji 失敗就是 None。

        這條刻意保留原本測試的情境（kbars 拋例外），只是斷言相反——
        它釘住「不會偷偷從別的來源生一個價格出來」。"""
        from technical_indicators import fetch_intraday_vwap
        api = MagicMock()
        api.kbars.side_effect = Exception("api error")
        assert fetch_intraday_vwap("2330", api=api) is None


# ── VWAP scoring in _assess_day_trading ───────────────────────────────────────

class TestAssessDayTradingVwap:
    def _base(self, price=100.0, vwap=100.0, volume_ratio=1.5, rsi=55.0):
        return {
            "current_price": price,
            "VWAP": vwap,
            "volume_ratio": volume_ratio,
            "RSI": rsi,
            "ATR": 3.0,
            "bullish_alignment": False,
            "bearish_alignment": False,
            "KD_K": 55.0,
            "KD_D": 50.0,
        }

    def _assess(self, indicators):
        from stock_query import _assess_day_trading
        return _assess_day_trading(indicators, {"error": None, "monthly_closes": []})

    def test_price_above_vwap_adds_score(self):
        base  = self._assess(self._base(price=100.0, vwap=100.0))["score"]
        above = self._assess(self._base(price=102.0, vwap=100.0))["score"]
        assert above >= base + 1

    def test_price_above_vwap_adds_reason(self):
        result = self._assess(self._base(price=102.0, vwap=100.0))
        assert any("VWAP" in r for r in result["reasons_good"])

    def test_price_below_vwap_reduces_score(self):
        base  = self._assess(self._base(price=100.0, vwap=100.0))["score"]
        below = self._assess(self._base(price=98.0,  vwap=100.0))["score"]
        assert below <= base - 1

    def test_price_below_vwap_adds_bad_reason(self):
        result = self._assess(self._base(price=98.0, vwap=100.0))
        assert any("VWAP" in r for r in result["reasons_bad"])

    def test_price_at_vwap_no_change(self):
        base  = self._assess(self._base(price=100.0, vwap=100.0))["score"]
        exact = self._assess(self._base(price=100.3, vwap=100.0))["score"]
        assert exact == base  # within 0.5% → no change

    def test_no_vwap_in_indicators_no_change(self):
        ind = self._base()
        del ind["VWAP"]
        base     = self._assess(self._base(price=100.0, vwap=100.0))["score"]
        no_vwap  = self._assess(ind)["score"]
        assert no_vwap == base
