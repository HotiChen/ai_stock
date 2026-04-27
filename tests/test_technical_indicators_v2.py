from __future__ import annotations

"""
升級版 technical_indicators 測試：
- calculate_indicators(df) → dict（給 rules.py 用）
- 新增 MACD, ATR, MA10, support, resistance
- fetch_indicators 抓 100 日
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch


def _make_df(n: int = 100, trend: str = "up") -> pd.DataFrame:
    if trend == "up":
        closes = np.linspace(80, 120, n)
    elif trend == "down":
        closes = np.linspace(120, 80, n)
    else:
        closes = np.array([100.0] * n)

    highs   = closes + 2
    lows    = closes - 2
    volumes = np.array([1_000_000.0] * n)
    dates   = pd.date_range("2025-01-01", periods=n, freq="B")
    df = pd.DataFrame({
        "Close":  closes,
        "High":   highs,
        "Low":    lows,
        "Volume": volumes,
    }, index=dates)
    df.columns = pd.MultiIndex.from_tuples([(c, "2330.TW") for c in df.columns])
    return df


# ── calculate_indicators ──────────────────────────────────────────────────────

class TestCalculateIndicators:
    def _df(self, n=100):
        from technical_indicators import _df_to_arrays
        return _make_df(n)

    def test_returns_dict(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        required = [
            "current_price", "MA5", "MA10", "MA20", "MA60",
            "RSI", "KD_K", "KD_D", "KD_K_prev", "KD_D_prev",
            "MACD_hist", "MACD_hist_prev",
            "BB_upper", "BB_lower", "BB_position",
            "volume_ratio", "resistance", "support", "ATR",
            "bullish_alignment", "bearish_alignment",
            "trailing_stop", "stop_loss_ma20",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_uptrend_bullish_alignment(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df(100, "up"))
        assert result["bullish_alignment"] is True
        assert result["bearish_alignment"] is False

    def test_downtrend_bearish_alignment(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df(100, "down"))
        assert result["bearish_alignment"] is True
        assert result["bullish_alignment"] is False

    def test_resistance_equals_20day_high(self):
        from technical_indicators import calculate_indicators
        df = _make_df(100, "up")
        result = calculate_indicators(df)
        highs    = df[("High", "2330.TW")].values.astype(float)[-20:]
        expected = float(highs.max())
        assert abs(result["resistance"] - expected) < 0.01

    def test_support_equals_20day_low(self):
        from technical_indicators import calculate_indicators
        df = _make_df(100, "up")
        result = calculate_indicators(df)
        lows     = df[("Low", "2330.TW")].values.astype(float)[-20:]
        expected = float(lows.min())
        assert abs(result["support"] - expected) < 0.01

    def test_trailing_stop_is_7pct_below_price(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        expected = round(result["current_price"] * 0.93, 2)
        assert abs(result["trailing_stop"] - expected) < 0.01

    def test_stop_loss_ma20_is_99pct_of_ma20(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        expected = round(result["MA20"] * 0.99, 2)
        assert abs(result["stop_loss_ma20"] - expected) < 0.01

    def test_bb_position_between_0_and_1_for_normal(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df(100, "flat"))
        assert 0 <= result["BB_position"] <= 1

    def test_atr_positive(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        assert result["ATR"] > 0

    def test_volume_ratio_equals_1_for_constant_volume(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        # all volumes are equal → ratio should be 1.0
        assert abs(result["volume_ratio"] - 1.0) < 0.01

    def test_insufficient_data_raises(self):
        from technical_indicators import calculate_indicators
        with pytest.raises(ValueError, match="資料不足"):
            calculate_indicators(_make_df(n=50))

    def test_all_values_are_floats_or_bool(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        for key, val in result.items():
            assert isinstance(val, (float, int, bool, np.floating, np.integer)), \
                f"{key} has unexpected type {type(val)}"


# ── calc_macd ─────────────────────────────────────────────────────────────────

class TestCalcMacd:
    def test_returns_hist_and_prev(self):
        from technical_indicators import calc_macd
        closes = np.linspace(100, 130, 60)
        hist, hist_prev = calc_macd(closes)
        assert isinstance(hist, float)
        assert isinstance(hist_prev, float)

    def test_uptrend_hist_positive(self):
        from technical_indicators import calc_macd
        closes = np.linspace(80, 130, 60)
        hist, _ = calc_macd(closes)
        assert hist > 0

    def test_downtrend_hist_negative(self):
        from technical_indicators import calc_macd
        closes = np.linspace(130, 80, 60)
        hist, _ = calc_macd(closes)
        assert hist < 0

    def test_insufficient_data_returns_zeros(self):
        from technical_indicators import calc_macd
        closes = np.array([100.0] * 10)
        hist, hist_prev = calc_macd(closes)
        assert hist == 0.0
        assert hist_prev == 0.0


# ── calc_atr ──────────────────────────────────────────────────────────────────

class TestCalcAtr:
    def test_returns_positive_float(self):
        from technical_indicators import calc_atr
        n = 30
        highs  = np.linspace(105, 115, n)
        lows   = np.linspace(95,  105, n)
        closes = np.linspace(100, 110, n)
        atr = calc_atr(highs, lows, closes)
        assert isinstance(atr, float)
        assert atr > 0

    def test_higher_volatility_higher_atr(self):
        from technical_indicators import calc_atr
        n = 30
        closes = np.linspace(100, 110, n)
        narrow_atr = calc_atr(closes + 1, closes - 1, closes)
        wide_atr   = calc_atr(closes + 5, closes - 5, closes)
        assert wide_atr > narrow_atr

    def test_insufficient_data_returns_zero(self):
        from technical_indicators import calc_atr
        closes = np.array([100.0, 101.0])
        atr = calc_atr(closes + 1, closes - 1, closes)
        assert atr == 0.0


# ── fetch_indicators uses 100 days ────────────────────────────────────────────

class TestFetchIndicators100Days:
    @patch("technical_indicators.yf.download")
    def test_requests_100_day_period(self, mock_dl):
        mock_dl.return_value = _make_df(100)
        from technical_indicators import fetch_indicators
        fetch_indicators("2330")
        call_kwargs = mock_dl.call_args
        period_arg  = call_kwargs.kwargs.get("period") or call_kwargs.args[1] if call_kwargs.args else None
        period_str  = str(call_kwargs)
        assert "100d" in period_str or "100" in period_str

    @patch("technical_indicators.yf.download")
    def test_returns_calculate_indicators_dict_compatible(self, mock_dl):
        mock_dl.return_value = _make_df(100)
        from technical_indicators import fetch_indicators
        result = fetch_indicators("2330")
        assert result is not None
        # should contain all keys needed by rules.py
        assert "current_price" in result or hasattr(result, "close")
