from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from technical_indicators import (
    calc_rsi,
    calc_kd,
    calc_bollinger,
    calc_volume_ratio,
    TechnicalIndicators,
    format_indicators_text,
    fetch_indicators,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_ind(**kwargs) -> TechnicalIndicators:
    defaults = dict(
        code="2330", close=850.0,
        ma5=840.0, ma20=820.0, ma60=800.0,
        rsi=55.0, kd_k=60.0, kd_d=55.0,
        bb_upper=900.0, bb_mid=820.0, bb_lower=740.0,
        volume_ratio=1.2,
    )
    defaults.update(kwargs)
    return TechnicalIndicators(**defaults)


# ── calc_rsi ──────────────────────────────────────────────────────────────────

class TestCalcRsi:
    def test_all_gains_near_100(self):
        closes = np.array([100.0 + i for i in range(20)])
        assert calc_rsi(closes) > 90

    def test_all_losses_near_0(self):
        closes = np.array([120.0 - i for i in range(20)])
        assert calc_rsi(closes) < 10

    def test_alternating_returns_near_50(self):
        closes = np.array([100.0, 101.0, 100.0, 101.0, 100.0,
                           101.0, 100.0, 101.0, 100.0, 101.0,
                           100.0, 101.0, 100.0, 101.0, 100.0,
                           101.0, 100.0, 101.0, 100.0, 101.0])
        rsi = calc_rsi(closes)
        assert 40 <= rsi <= 60

    def test_returns_float_in_range(self):
        closes = np.linspace(100, 120, 30)
        rsi = calc_rsi(closes)
        assert isinstance(rsi, float)
        assert 0 <= rsi <= 100

    def test_insufficient_data_returns_50(self):
        closes = np.array([100.0, 101.0])
        assert calc_rsi(closes) == 50.0

    def test_custom_period(self):
        closes = np.linspace(100, 110, 30)
        rsi_14 = calc_rsi(closes, period=14)
        rsi_9  = calc_rsi(closes, period=9)
        assert 0 <= rsi_14 <= 100
        assert 0 <= rsi_9  <= 100


# ── calc_kd ───────────────────────────────────────────────────────────────────

class TestCalcKd:
    def test_close_at_high_k_approaches_100(self):
        # close == high every day → RSV = 100 always
        n = 30
        highs  = np.array([110.0] * n)
        lows   = np.array([90.0]  * n)
        closes = np.array([110.0] * n)  # close = high
        k, d = calc_kd(highs, lows, closes)
        assert k > 80
        assert d > 70

    def test_close_at_low_k_approaches_0(self):
        n = 30
        highs  = np.array([110.0] * n)
        lows   = np.array([90.0]  * n)
        closes = np.array([90.0]  * n)  # close = low
        k, d = calc_kd(highs, lows, closes)
        assert k < 20
        assert d < 30

    def test_returns_floats_in_range(self):
        n = 30
        highs  = np.linspace(105, 115, n)
        lows   = np.linspace(95,  105, n)
        closes = np.linspace(100, 110, n)
        k, d = calc_kd(highs, lows, closes)
        assert isinstance(k, float)
        assert isinstance(d, float)
        assert 0 <= k <= 100
        assert 0 <= d <= 100

    def test_insufficient_data_returns_50_50(self):
        highs  = np.array([110.0, 112.0])
        lows   = np.array([90.0,  91.0])
        closes = np.array([100.0, 101.0])
        k, d = calc_kd(highs, lows, closes)
        assert k == 50.0
        assert d == 50.0


# ── calc_bollinger ────────────────────────────────────────────────────────────

class TestCalcBollinger:
    def test_upper_mid_lower_order(self):
        closes = np.linspace(100, 120, 30)
        upper, mid, lower = calc_bollinger(closes)
        assert upper > mid > lower

    def test_mid_equals_mean(self):
        closes = np.linspace(100, 120, 20)
        upper, mid, lower = calc_bollinger(closes, period=20)
        assert abs(mid - float(closes.mean())) < 0.01

    def test_band_width_proportional_to_volatility(self):
        stable   = np.array([100.0] * 25)
        volatile = np.concatenate([np.linspace(80, 120, 13),
                                   np.linspace(120, 80, 12)])
        u1, m1, l1 = calc_bollinger(stable)
        u2, m2, l2 = calc_bollinger(volatile)
        assert (u2 - l2) > (u1 - l1)

    def test_insufficient_data_returns_price_for_all(self):
        closes = np.array([100.0, 101.0])
        upper, mid, lower = calc_bollinger(closes)
        assert upper == mid == lower


# ── calc_volume_ratio ─────────────────────────────────────────────────────────

class TestCalcVolumeRatio:
    def test_double_volume_returns_2(self):
        # 5-day avg = 1000, today = 2000
        volumes = np.array([1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 2000.0])
        assert calc_volume_ratio(volumes) == pytest.approx(2.0)

    def test_equal_volume_returns_1(self):
        volumes = np.array([1000.0] * 6)
        assert calc_volume_ratio(volumes) == pytest.approx(1.0)

    def test_insufficient_data_returns_1(self):
        volumes = np.array([1000.0])
        assert calc_volume_ratio(volumes) == 1.0

    def test_zero_avg_returns_1(self):
        volumes = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1000.0])
        assert calc_volume_ratio(volumes) == 1.0


# ── TechnicalIndicators properties ───────────────────────────────────────────

class TestTechnicalIndicatorsProperties:
    def test_is_multi_head_true(self):
        ind = _make_ind(ma5=860.0, ma20=840.0, ma60=820.0)
        assert ind.is_multi_head is True

    def test_is_multi_head_false_when_ma5_below_ma20(self):
        ind = _make_ind(ma5=810.0, ma20=840.0, ma60=820.0)
        assert ind.is_multi_head is False

    def test_is_multi_head_false_when_ma20_below_ma60(self):
        ind = _make_ind(ma5=860.0, ma20=810.0, ma60=820.0)
        assert ind.is_multi_head is False

    def test_is_bb_overbought_true(self):
        ind = _make_ind(close=910.0, bb_upper=900.0)
        assert ind.is_bb_overbought is True

    def test_is_bb_overbought_false(self):
        ind = _make_ind(close=850.0, bb_upper=900.0)
        assert ind.is_bb_overbought is False

    def test_is_volume_breakout_true(self):
        ind = _make_ind(volume_ratio=1.5)
        assert ind.is_volume_breakout is True

    def test_is_volume_breakout_false(self):
        ind = _make_ind(volume_ratio=1.2)
        assert ind.is_volume_breakout is False

    def test_volume_breakout_exact_boundary(self):
        ind = _make_ind(volume_ratio=1.5)
        assert ind.is_volume_breakout is True


# ── format_indicators_text ────────────────────────────────────────────────────

class TestFormatIndicatorsText:
    def test_contains_rsi(self):
        ind = _make_ind(rsi=62.3)
        text = format_indicators_text(ind)
        assert "RSI" in text and "62" in text

    def test_contains_kd(self):
        ind = _make_ind(kd_k=70.0, kd_d=65.0)
        text = format_indicators_text(ind)
        assert "KD" in text or ("K=" in text and "D=" in text)

    def test_contains_ma_values(self):
        ind = _make_ind(ma5=840.0, ma20=820.0, ma60=800.0)
        text = format_indicators_text(ind)
        assert "MA5" in text and "MA20" in text and "MA60" in text

    def test_contains_bollinger(self):
        ind = _make_ind(bb_upper=900.0, bb_lower=740.0)
        text = format_indicators_text(ind)
        assert "布林" in text or "Bollinger" in text or "BB" in text

    def test_contains_volume_ratio(self):
        ind = _make_ind(volume_ratio=1.8)
        text = format_indicators_text(ind)
        assert "量比" in text or "成交量" in text or "1.8" in text

    def test_multi_head_signal_mentioned(self):
        ind = _make_ind(ma5=860.0, ma20=840.0, ma60=820.0)
        text = format_indicators_text(ind)
        assert "多頭" in text or "均線" in text

    def test_overbought_warning_mentioned(self):
        ind = _make_ind(close=910.0, bb_upper=900.0)
        text = format_indicators_text(ind)
        assert "過熱" in text or "上軌" in text or "BBU" in text

    def test_volume_breakout_mentioned(self):
        ind = _make_ind(volume_ratio=2.0)
        text = format_indicators_text(ind)
        assert "帶量" in text or "突破" in text or "量能" in text

    def test_returns_nonempty_string(self):
        ind = _make_ind()
        text = format_indicators_text(ind)
        assert isinstance(text, str) and len(text) > 50

    def test_rsi_oversold_mentioned(self):
        ind = _make_ind(rsi=25.0)
        text = format_indicators_text(ind)
        assert "超賣" in text or "低檔" in text or "RSI" in text


# ── fetch_indicators (integration, mocked) ───────────────────────────────────

class TestFetchIndicators:
    def _make_df(self, n=60):
        import pandas as pd
        dates  = pd.date_range("2026-01-01", periods=n, freq="B")
        closes = np.linspace(100, 120, n)
        highs  = closes + 2
        lows   = closes - 2
        vols   = np.array([1_000_000] * n, dtype=float)
        df = pd.DataFrame({
            "Close":  closes,
            "High":   highs,
            "Low":    lows,
            "Volume": vols,
        }, index=dates)
        # yfinance returns MultiIndex columns when auto_adjust=True
        df.columns = pd.MultiIndex.from_tuples(
            [(c, "2330.TW") for c in df.columns]
        )
        return df

    @patch("technical_indicators.yf.download")
    def test_returns_technical_indicators(self, mock_dl):
        mock_dl.return_value = self._make_df()
        result = fetch_indicators("2330")
        assert isinstance(result, TechnicalIndicators)
        assert result.code == "2330"

    @patch("technical_indicators.yf.download")
    def test_ma5_ma20_ma60_are_floats(self, mock_dl):
        mock_dl.return_value = self._make_df()
        result = fetch_indicators("2330")
        assert isinstance(result.ma5,  float)
        assert isinstance(result.ma20, float)
        assert isinstance(result.ma60, float)

    @patch("technical_indicators.yf.download")
    def test_rsi_in_range(self, mock_dl):
        mock_dl.return_value = self._make_df()
        result = fetch_indicators("2330")
        assert 0 <= result.rsi <= 100

    @patch("technical_indicators.yf.download")
    def test_kd_in_range(self, mock_dl):
        mock_dl.return_value = self._make_df()
        result = fetch_indicators("2330")
        assert 0 <= result.kd_k <= 100
        assert 0 <= result.kd_d <= 100

    @patch("technical_indicators.yf.download")
    def test_bb_order(self, mock_dl):
        mock_dl.return_value = self._make_df()
        result = fetch_indicators("2330")
        assert result.bb_upper > result.bb_mid > result.bb_lower

    @patch("technical_indicators.yf.download")
    def test_volume_ratio_positive(self, mock_dl):
        mock_dl.return_value = self._make_df()
        result = fetch_indicators("2330")
        assert result.volume_ratio > 0

    @patch("technical_indicators.yf.download")
    def test_download_failure_returns_none(self, mock_dl):
        mock_dl.side_effect = Exception("network error")
        result = fetch_indicators("2330")
        assert result is None

    @patch("technical_indicators.yf.download")
    def test_insufficient_data_returns_none(self, mock_dl):
        import pandas as pd
        df = pd.DataFrame({"Close": [100.0, 101.0]})
        mock_dl.return_value = df
        result = fetch_indicators("2330")
        assert result is None
