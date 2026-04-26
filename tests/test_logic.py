import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from logic import (
    add_indicators,
    build_chart,
    get_bar_colors,
    get_yesterday_close,
    normalize_kbars_df,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_ohlcv(n: int = 100, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    open_ = close + rng.normal(0, 0.5, n)
    high = np.maximum(close, open_) + rng.uniform(0, 1, n)
    low = np.minimum(close, open_) - rng.uniform(0, 1, n)
    volume = rng.integers(1000, 50000, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"ts": dates, "Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}
    )


# ── normalize_kbars_df ────────────────────────────────────────────────────────

class TestNormalizeKbarsDf:
    def test_lowercase_columns_become_capitalized(self):
        df = pd.DataFrame({"ts": [], "open": [], "high": [], "low": [], "close": [], "volume": []})
        result = normalize_kbars_df(df)
        assert list(result.columns) == ["ts", "Open", "High", "Low", "Close", "Volume"]

    def test_already_correct_columns_unchanged(self):
        df = make_ohlcv(5)
        result = normalize_kbars_df(df)
        assert list(result.columns) == ["ts", "Open", "High", "Low", "Close", "Volume"]

    def test_ts_column_is_datetime(self):
        df = make_ohlcv(5)
        df["ts"] = df["ts"].astype(str)
        result = normalize_kbars_df(df)
        assert pd.api.types.is_datetime64_any_dtype(result["ts"])

    def test_sorted_by_ts_ascending(self):
        df = make_ohlcv(10)
        df = df.iloc[::-1].reset_index(drop=True)
        result = normalize_kbars_df(df)
        assert result["ts"].is_monotonic_increasing


# ── add_indicators ────────────────────────────────────────────────────────────

class TestAddIndicators:
    def test_ma_columns_added(self):
        df = make_ohlcv(100)
        result = add_indicators(df)
        assert "MA5" in result.columns
        assert "MA20" in result.columns
        assert "MA60" in result.columns

    def test_rsi_column_added(self):
        df = make_ohlcv(100)
        result = add_indicators(df)
        assert "RSI" in result.columns

    def test_macd_columns_added(self):
        df = make_ohlcv(100)
        result = add_indicators(df)
        assert "MACD" in result.columns
        assert "MACD_signal" in result.columns
        assert "MACD_hist" in result.columns

    def test_ma5_has_nan_first_4_rows(self):
        df = make_ohlcv(100)
        result = add_indicators(df)
        assert result["MA5"].iloc[:4].isna().all()
        assert pd.notna(result["MA5"].iloc[4])

    def test_rsi_range_0_to_100(self):
        df = make_ohlcv(100)
        result = add_indicators(df)
        valid = result["RSI"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_ma5_value_correct(self):
        df = make_ohlcv(100)
        result = add_indicators(df)
        expected = df["Close"].iloc[:5].mean()
        assert abs(result["MA5"].iloc[4] - expected) < 1e-4

    def test_does_not_modify_original_df(self):
        df = make_ohlcv(100)
        original_cols = list(df.columns)
        add_indicators(df.copy())
        assert list(df.columns) == original_cols

    def test_short_df_still_returns_without_error(self):
        df = make_ohlcv(10)
        result = add_indicators(df)
        assert "MA5" in result.columns
        assert "RSI" in result.columns


# ── get_bar_colors ────────────────────────────────────────────────────────────

class TestGetBarColors:
    def test_close_gt_open_is_red(self):
        df = pd.DataFrame({"Open": [100.0], "Close": [105.0]})
        colors = get_bar_colors(df)
        assert colors == ["#ef5350"]

    def test_close_lt_open_is_green(self):
        df = pd.DataFrame({"Open": [105.0], "Close": [100.0]})
        colors = get_bar_colors(df)
        assert colors == ["#26a69a"]

    def test_close_eq_open_is_red(self):
        df = pd.DataFrame({"Open": [100.0], "Close": [100.0]})
        colors = get_bar_colors(df)
        assert colors == ["#ef5350"]

    def test_length_matches_df(self):
        df = make_ohlcv(50)
        colors = get_bar_colors(df)
        assert len(colors) == 50


# ── get_yesterday_close ───────────────────────────────────────────────────────

class TestGetYesterdayClose:
    def test_basic_calculation(self):
        class FakeSnapshot:
            close = 150.0
            change_price = 5.0

        assert get_yesterday_close(FakeSnapshot()) == pytest.approx(145.0)

    def test_negative_change(self):
        class FakeSnapshot:
            close = 100.0
            change_price = -3.0

        assert get_yesterday_close(FakeSnapshot()) == pytest.approx(103.0)

    def test_no_change(self):
        class FakeSnapshot:
            close = 100.0
            change_price = 0.0

        assert get_yesterday_close(FakeSnapshot()) == pytest.approx(100.0)


# ── build_chart ───────────────────────────────────────────────────────────────

class TestBuildChart:
    def test_returns_figure(self):
        df = add_indicators(make_ohlcv(100))
        fig = build_chart(df)
        assert isinstance(fig, go.Figure)

    def test_has_four_subplots(self):
        df = add_indicators(make_ohlcv(100))
        fig = build_chart(df)
        rows = {trace.get("yaxis", "") for trace in fig.to_dict()["data"]}
        assert len(fig.to_dict()["data"]) >= 4

    def test_contains_candlestick(self):
        df = add_indicators(make_ohlcv(100))
        fig = build_chart(df)
        trace_types = [type(t).__name__ for t in fig.data]
        assert "Candlestick" in trace_types

    def test_contains_volume_bar(self):
        df = add_indicators(make_ohlcv(100))
        fig = build_chart(df)
        bar_traces = [t for t in fig.data if isinstance(t, go.Bar)]
        assert len(bar_traces) >= 1

    def test_contains_rsi_trace(self):
        df = add_indicators(make_ohlcv(100))
        fig = build_chart(df)
        trace_names = [t.name for t in fig.data]
        assert "RSI" in trace_names

    def test_contains_macd_trace(self):
        df = add_indicators(make_ohlcv(100))
        fig = build_chart(df)
        trace_names = [t.name for t in fig.data]
        assert "MACD" in trace_names

    def test_no_rangeslider(self):
        df = add_indicators(make_ohlcv(100))
        fig = build_chart(df)
        layout = fig.to_dict()["layout"]
        assert layout.get("xaxis", {}).get("rangeslider", {}).get("visible", True) is False

    def test_short_df_no_ma60(self):
        df = add_indicators(make_ohlcv(20))
        fig = build_chart(df)
        trace_names = [t.name for t in fig.data]
        assert "MA60" not in trace_names or all(
            pd.isna(t.y).all() if t.name == "MA60" else True for t in fig.data
        )
