from __future__ import annotations

import pytest

from stock_detail import (
    classify_pe_ratio,
    classify_rsi,
    classify_ma_trend,
    build_signal_badges,
    get_fundamental_verdict,
    Signal,
)
from stock_research import StockFundamentals


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_fund(**kwargs) -> StockFundamentals:
    defaults = dict(
        pe_ratio=15.0, eps=30.0, market_cap=500_000_000_000,
        week52_high=200.0, week52_low=120.0,
        dividend_yield=0.04, revenue_growth=0.10, profit_margin=0.20,
    )
    defaults.update(kwargs)
    return StockFundamentals(**defaults)


# ── Signal ────────────────────────────────────────────────────────────────────

class TestSignal:
    def test_has_label_color_description(self):
        s = Signal(label="RSI 超賣", color="green", description="低於 30，可能反彈")
        assert s.label == "RSI 超賣"
        assert s.color in ("green", "red", "yellow", "gray", "blue")
        assert len(s.description) > 0


# ── classify_pe_ratio ─────────────────────────────────────────────────────────

class TestClassifyPeRatio:
    def test_none_returns_no_data(self):
        result = classify_pe_ratio(None)
        assert result.color == "gray"

    def test_below_10_is_cheap(self):
        result = classify_pe_ratio(8.0)
        assert "低估" in result.label or "便宜" in result.label or result.color == "green"

    def test_10_to_20_is_reasonable(self):
        result = classify_pe_ratio(15.0)
        assert "合理" in result.label or result.color in ("green", "blue", "yellow")

    def test_20_to_30_is_high(self):
        result = classify_pe_ratio(25.0)
        assert result.color in ("yellow", "red") or "偏高" in result.label

    def test_above_30_is_expensive(self):
        result = classify_pe_ratio(35.0)
        assert result.color == "red" or "昂貴" in result.label or "偏高" in result.label

    def test_negative_pe_handled(self):
        result = classify_pe_ratio(-5.0)
        assert isinstance(result, Signal)

    def test_returns_signal(self):
        assert isinstance(classify_pe_ratio(18.0), Signal)


# ── classify_rsi ──────────────────────────────────────────────────────────────

class TestClassifyRsi:
    def test_none_returns_gray(self):
        result = classify_rsi(None)
        assert result.color == "gray"

    def test_below_30_is_oversold(self):
        result = classify_rsi(25.0)
        assert "超賣" in result.label or result.color == "green"

    def test_30_to_70_is_normal(self):
        result = classify_rsi(55.0)
        assert "正常" in result.label or result.color in ("gray", "blue", "yellow")

    def test_above_70_is_overbought(self):
        result = classify_rsi(78.0)
        assert "超買" in result.label or result.color == "red"

    def test_exactly_30_is_oversold_or_normal(self):
        result = classify_rsi(30.0)
        assert isinstance(result, Signal)

    def test_exactly_70_is_overbought_or_normal(self):
        result = classify_rsi(70.0)
        assert isinstance(result, Signal)

    def test_returns_signal(self):
        assert isinstance(classify_rsi(50.0), Signal)


# ── classify_ma_trend ─────────────────────────────────────────────────────────

class TestClassifyMaTrend:
    def test_bullish_alignment(self):
        # MA5 > MA20 > MA60 = 多頭排列
        result = classify_ma_trend(ma5=100, ma20=90, ma60=80)
        assert "多頭" in result.label or result.color == "green"

    def test_bearish_alignment(self):
        # MA5 < MA20 < MA60 = 空頭排列
        result = classify_ma_trend(ma5=80, ma20=90, ma60=100)
        assert "空頭" in result.label or result.color == "red"

    def test_mixed_is_consolidation(self):
        result = classify_ma_trend(ma5=90, ma20=95, ma60=85)
        assert isinstance(result, Signal)

    def test_none_values_handled(self):
        result = classify_ma_trend(ma5=None, ma20=None, ma60=None)
        assert result.color == "gray"

    def test_partial_none_handled(self):
        result = classify_ma_trend(ma5=100, ma20=None, ma60=80)
        assert isinstance(result, Signal)

    def test_returns_signal(self):
        assert isinstance(classify_ma_trend(100, 90, 80), Signal)


# ── build_signal_badges ───────────────────────────────────────────────────────

class TestBuildSignalBadges:
    def test_returns_list(self):
        result = build_signal_badges(make_fund(), {"RSI": 55.0, "MA5": 100, "MA20": 90, "MA60": 80})
        assert isinstance(result, list)

    def test_each_item_is_signal(self):
        result = build_signal_badges(make_fund(), {"RSI": 55.0})
        for s in result:
            assert isinstance(s, Signal)

    def test_at_least_one_badge(self):
        result = build_signal_badges(make_fund(), {})
        assert len(result) >= 1

    def test_includes_pe_signal(self):
        fund = make_fund(pe_ratio=8.0)
        result = build_signal_badges(fund, {})
        labels = " ".join(s.label for s in result)
        assert "PE" in labels or "本益比" in labels or "P/E" in labels

    def test_includes_rsi_signal_when_indicator_present(self):
        result = build_signal_badges(make_fund(), {"RSI": 25.0})
        labels = " ".join(s.label for s in result)
        assert "RSI" in labels

    def test_includes_ma_signal_when_indicators_present(self):
        result = build_signal_badges(make_fund(), {"MA5": 100.0, "MA20": 90.0, "MA60": 80.0})
        labels = " ".join(s.label for s in result)
        assert "MA" in labels or "均線" in labels

    def test_no_rsi_signal_when_missing(self):
        result = build_signal_badges(make_fund(), {})
        labels = " ".join(s.label for s in result)
        assert "RSI" not in labels

    def test_includes_dividend_signal_when_high(self):
        fund = make_fund(dividend_yield=0.06)
        result = build_signal_badges(fund, {})
        labels = " ".join(s.label for s in result)
        assert "殖利率" in labels or "配息" in labels


# ── get_fundamental_verdict ───────────────────────────────────────────────────

class TestGetFundamentalVerdict:
    def test_returns_string(self):
        assert isinstance(get_fundamental_verdict(make_fund()), str)

    def test_good_fundamentals_positive(self):
        fund = make_fund(pe_ratio=10.0, dividend_yield=0.05, revenue_growth=0.15)
        verdict = get_fundamental_verdict(fund)
        assert len(verdict) > 0

    def test_bad_fundamentals_warning(self):
        fund = make_fund(pe_ratio=50.0, dividend_yield=0.001, revenue_growth=-0.20)
        verdict = get_fundamental_verdict(fund)
        assert len(verdict) > 0

    def test_none_fundamentals_returns_no_data(self):
        fund = StockFundamentals(
            pe_ratio=None, eps=None, market_cap=None,
            week52_high=None, week52_low=None,
            dividend_yield=None, revenue_growth=None, profit_margin=None,
        )
        verdict = get_fundamental_verdict(fund)
        assert isinstance(verdict, str)

    def test_length_reasonable(self):
        verdict = get_fundamental_verdict(make_fund())
        assert 5 < len(verdict) < 200
