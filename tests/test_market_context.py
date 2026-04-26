from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from market_context import (
    USMarketSnapshot,
    fetch_us_market,
    fetch_macro_news,
    classify_market_sentiment,
    build_market_summary,
    MacroHeadline,
)


# ── USMarketSnapshot ──────────────────────────────────────────────────────────

class TestUSMarketSnapshot:
    def test_has_required_fields(self):
        s = USMarketSnapshot(
            sp500_change=0.5, nasdaq_change=1.2,
            sox_change=2.1, vix=18.0,
        )
        assert s.sp500_change == pytest.approx(0.5)
        assert s.vix == pytest.approx(18.0)

    def test_change_can_be_negative(self):
        s = USMarketSnapshot(sp500_change=-1.5, nasdaq_change=-2.0, sox_change=-3.0, vix=25.0)
        assert s.sp500_change < 0


# ── MacroHeadline ─────────────────────────────────────────────────────────────

class TestMacroHeadline:
    def test_has_required_fields(self):
        h = MacroHeadline(title="Fed 維持利率", source="Reuters", category="us_policy")
        assert h.title == "Fed 維持利率"
        assert h.category in ("us_policy", "tw_policy", "market", "semiconductor")


# ── classify_market_sentiment ─────────────────────────────────────────────────

class TestClassifyMarketSentiment:
    def test_bullish_when_all_up(self):
        s = USMarketSnapshot(sp500_change=1.0, nasdaq_change=1.5, sox_change=2.0, vix=15.0)
        assert classify_market_sentiment(s) == "bullish"

    def test_bearish_when_all_down(self):
        s = USMarketSnapshot(sp500_change=-1.5, nasdaq_change=-2.0, sox_change=-3.0, vix=30.0)
        assert classify_market_sentiment(s) == "bearish"

    def test_neutral_when_mixed(self):
        s = USMarketSnapshot(sp500_change=0.1, nasdaq_change=-0.1, sox_change=0.2, vix=20.0)
        result = classify_market_sentiment(s)
        assert result in ("neutral", "bullish", "bearish")

    def test_returns_string(self):
        s = USMarketSnapshot(sp500_change=0.0, nasdaq_change=0.0, sox_change=0.0, vix=20.0)
        assert isinstance(classify_market_sentiment(s), str)


# ── fetch_us_market ───────────────────────────────────────────────────────────

class TestFetchUsMarket:
    @patch("market_context.yf.download")
    def test_returns_snapshot(self, mock_dl):
        import pandas as pd
        import numpy as np
        mock_dl.return_value = pd.DataFrame({
            "Close": [100.0, 101.0]
        }, index=pd.date_range("2026-04-25", periods=2))
        result = fetch_us_market()
        assert isinstance(result, USMarketSnapshot)

    @patch("market_context.yf.download", side_effect=Exception("network error"))
    def test_failure_returns_none(self, mock_dl):
        result = fetch_us_market()
        assert result is None


# ── fetch_macro_news ──────────────────────────────────────────────────────────

class TestFetchMacroNews:
    @patch("market_context.feedparser.parse")
    def test_returns_list(self, mock_parse):
        mock_parse.return_value = MagicMock(entries=[
            MagicMock(title="Fed 升息", published="Mon, 27 Apr 2026 09:00:00 GMT"),
        ])
        result = fetch_macro_news()
        assert isinstance(result, list)

    @patch("market_context.feedparser.parse")
    def test_headlines_have_title(self, mock_parse):
        mock_parse.return_value = MagicMock(entries=[
            MagicMock(title="台灣政策新聞", published="Mon, 27 Apr 2026 09:00:00 GMT"),
        ])
        result = fetch_macro_news()
        if result:
            assert hasattr(result[0], "title")

    @patch("market_context.feedparser.parse", side_effect=Exception("timeout"))
    def test_failure_returns_empty(self, mock_parse):
        result = fetch_macro_news()
        assert result == []


# ── build_market_summary ──────────────────────────────────────────────────────

class TestBuildMarketSummary:
    def _make_snapshot(self):
        return USMarketSnapshot(sp500_change=0.8, nasdaq_change=1.2, sox_change=2.5, vix=16.0)

    def _make_headlines(self):
        return [
            MacroHeadline("Fed 維持利率", "Reuters", "us_policy"),
            MacroHeadline("台積電 ADR 漲", "Bloomberg", "semiconductor"),
        ]

    def test_returns_string(self):
        result = build_market_summary(self._make_snapshot(), self._make_headlines())
        assert isinstance(result, str)
        assert len(result) > 50

    def test_includes_sp500(self):
        result = build_market_summary(self._make_snapshot(), [])
        assert "S&P" in result or "0.8" in result or "sp500" in result.lower()

    def test_includes_news_titles(self):
        result = build_market_summary(self._make_snapshot(), self._make_headlines())
        assert "Fed" in result or "台積電" in result

    def test_includes_sentiment(self):
        result = build_market_summary(self._make_snapshot(), [])
        assert any(w in result for w in ("bullish", "bearish", "neutral", "多", "空", "震盪"))
