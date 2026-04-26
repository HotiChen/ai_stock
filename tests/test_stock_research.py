from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from stock_research import (
    StockFundamentals,
    build_stock_research_prompt,
    format_fundamentals_text,
    parse_analysis_result,
    fetch_stock_news,
    fetch_stock_fundamentals,
    analyze_stock_ai,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_fundamentals(**kwargs) -> StockFundamentals:
    defaults = dict(
        pe_ratio=15.2,
        eps=55.3,
        market_cap=2_000_000_000_000,
        week52_high=1000.0,
        week52_low=700.0,
        dividend_yield=0.025,
        revenue_growth=0.12,
        profit_margin=0.38,
    )
    defaults.update(kwargs)
    return StockFundamentals(**defaults)


SAMPLE_NEWS = ["台積電 Q2 營收創新高", "外資連買台積電 5 日", "台積電擴大 CoWoS 產能"]
SAMPLE_INDICATORS = {"RSI": 55.2, "MACD": 12.5, "MA5": 850.0, "MA20": 830.0, "Close": 880.0}


# ── StockFundamentals ─────────────────────────────────────────────────────────

class TestStockFundamentals:
    def test_has_required_fields(self):
        f = make_fundamentals()
        assert hasattr(f, "pe_ratio")
        assert hasattr(f, "eps")
        assert hasattr(f, "market_cap")
        assert hasattr(f, "week52_high")
        assert hasattr(f, "week52_low")
        assert hasattr(f, "dividend_yield")
        assert hasattr(f, "revenue_growth")
        assert hasattr(f, "profit_margin")

    def test_accepts_none_values(self):
        f = StockFundamentals(
            pe_ratio=None, eps=None, market_cap=None,
            week52_high=None, week52_low=None,
            dividend_yield=None, revenue_growth=None, profit_margin=None,
        )
        assert f.pe_ratio is None


# ── format_fundamentals_text ──────────────────────────────────────────────────

class TestFormatFundamentalsText:
    def test_returns_string(self):
        result = format_fundamentals_text(make_fundamentals())
        assert isinstance(result, str)

    def test_includes_pe_ratio(self):
        f = make_fundamentals(pe_ratio=18.5)
        result = format_fundamentals_text(f)
        assert "18.5" in result or "本益比" in result

    def test_includes_eps(self):
        f = make_fundamentals(eps=42.0)
        result = format_fundamentals_text(f)
        assert "42" in result or "EPS" in result

    def test_handles_none_pe_gracefully(self):
        f = make_fundamentals(pe_ratio=None)
        result = format_fundamentals_text(f)
        assert isinstance(result, str)
        assert "N/A" in result or "無資料" in result or "-" in result

    def test_includes_dividend_yield(self):
        f = make_fundamentals(dividend_yield=0.04)
        result = format_fundamentals_text(f)
        assert "4" in result or "殖利率" in result

    def test_includes_52w_range(self):
        f = make_fundamentals(week52_high=1000.0, week52_low=700.0)
        result = format_fundamentals_text(f)
        assert "1000" in result or "700" in result

    def test_all_none_returns_non_empty(self):
        f = StockFundamentals(
            pe_ratio=None, eps=None, market_cap=None,
            week52_high=None, week52_low=None,
            dividend_yield=None, revenue_growth=None, profit_margin=None,
        )
        result = format_fundamentals_text(f)
        assert len(result) > 0


# ── build_stock_research_prompt ───────────────────────────────────────────────

class TestBuildStockResearchPrompt:
    def test_returns_non_empty_string(self):
        result = build_stock_research_prompt("2330", "台積電", SAMPLE_NEWS, make_fundamentals(), SAMPLE_INDICATORS)
        assert isinstance(result, str) and len(result) > 100

    def test_includes_stock_code(self):
        result = build_stock_research_prompt("2330", "台積電", SAMPLE_NEWS, make_fundamentals(), SAMPLE_INDICATORS)
        assert "2330" in result

    def test_includes_stock_name(self):
        result = build_stock_research_prompt("2330", "台積電", SAMPLE_NEWS, make_fundamentals(), SAMPLE_INDICATORS)
        assert "台積電" in result

    def test_includes_news_headlines(self):
        news = ["台積電大漲停", "外資買超"]
        result = build_stock_research_prompt("2330", "台積電", news, make_fundamentals(), SAMPLE_INDICATORS)
        assert "台積電大漲停" in result or "外資買超" in result

    def test_includes_rsi(self):
        result = build_stock_research_prompt("2330", "台積電", SAMPLE_NEWS, make_fundamentals(), {"RSI": 72.0})
        assert "RSI" in result or "72" in result

    def test_includes_pe_in_prompt(self):
        result = build_stock_research_prompt("2330", "台積電", SAMPLE_NEWS, make_fundamentals(pe_ratio=20.0), {})
        assert "20" in result or "本益比" in result or "PE" in result

    def test_empty_news_handled(self):
        result = build_stock_research_prompt("2330", "台積電", [], make_fundamentals(), {})
        assert isinstance(result, str)

    def test_asks_for_buy_sell_hold(self):
        result = build_stock_research_prompt("2330", "台積電", SAMPLE_NEWS, make_fundamentals(), SAMPLE_INDICATORS)
        assert any(w in result for w in ["buy", "sell", "hold", "買", "賣"])


# ── parse_analysis_result ─────────────────────────────────────────────────────

class TestParseAnalysisResult:
    def test_parses_valid_json(self):
        raw = '{"action": "buy", "reason": "RSI 低檔", "confidence": 7, "target_price": 900.0, "stop_loss": 820.0}'
        result = parse_analysis_result(raw)
        assert result["action"] == "buy"
        assert result["confidence"] == 7

    def test_defaults_to_hold_on_invalid(self):
        result = parse_analysis_result("這不是 JSON")
        assert result["action"] == "hold"

    def test_returns_hold_on_empty(self):
        result = parse_analysis_result("")
        assert result["action"] == "hold"

    def test_clamps_confidence_0_to_10(self):
        raw = '{"action": "buy", "confidence": 99}'
        result = parse_analysis_result(raw)
        assert 0 <= result["confidence"] <= 10

    def test_rejects_invalid_action(self):
        raw = '{"action": "dance", "confidence": 5}'
        result = parse_analysis_result(raw)
        assert result["action"] == "hold"

    def test_all_required_keys_present(self):
        raw = '{"action": "sell", "reason": "overbought", "confidence": 8, "target_price": 850.0, "stop_loss": 900.0}'
        result = parse_analysis_result(raw)
        for key in ("action", "reason", "confidence", "target_price", "stop_loss"):
            assert key in result

    def test_missing_optional_fields_have_defaults(self):
        raw = '{"action": "hold"}'
        result = parse_analysis_result(raw)
        assert result.get("reason") is not None
        assert result.get("confidence") is not None

    def test_hold_action_accepted(self):
        raw = '{"action": "hold", "confidence": 5, "reason": "觀望"}'
        result = parse_analysis_result(raw)
        assert result["action"] == "hold"


# ── fetch_stock_news ──────────────────────────────────────────────────────────

class TestFetchStockNews:
    def test_returns_list(self):
        mock_feed = MagicMock()
        mock_feed.entries = [
            MagicMock(title="台積電漲停"),
            MagicMock(title="台積電外資買超"),
        ]
        with patch("stock_research.feedparser.parse", return_value=mock_feed):
            result = fetch_stock_news("2330", "台積電", max_items=5)
        assert isinstance(result, list)

    def test_returns_headlines_as_strings(self):
        mock_feed = MagicMock()
        mock_feed.entries = [MagicMock(title="台積電新高")]
        with patch("stock_research.feedparser.parse", return_value=mock_feed):
            result = fetch_stock_news("2330", "台積電", max_items=5)
        assert all(isinstance(h, str) for h in result)

    def test_respects_max_items(self):
        mock_feed = MagicMock()
        mock_feed.entries = [MagicMock(title=f"新聞{i}") for i in range(20)]
        with patch("stock_research.feedparser.parse", return_value=mock_feed):
            result = fetch_stock_news("2330", "台積電", max_items=3)
        assert len(result) <= 3

    def test_handles_fetch_error_gracefully(self):
        with patch("stock_research.feedparser.parse", side_effect=Exception("網路錯誤")):
            result = fetch_stock_news("2330", "台積電")
        assert isinstance(result, list)

    def test_deduplicates_headlines(self):
        mock_feed = MagicMock()
        mock_feed.entries = [MagicMock(title="同一則新聞")] * 5
        with patch("stock_research.feedparser.parse", return_value=mock_feed):
            result = fetch_stock_news("2330", "台積電", max_items=10)
        assert len(result) == len(set(result))


# ── fetch_stock_fundamentals ──────────────────────────────────────────────────

class TestFetchStockFundamentals:
    def _make_yf_info(self, **kwargs):
        defaults = {
            "trailingPE": 20.5,
            "trailingEps": 42.0,
            "marketCap": 10_000_000_000,
            "fiftyTwoWeekHigh": 1000.0,
            "fiftyTwoWeekLow": 700.0,
            "dividendYield": 0.03,
            "revenueGrowth": 0.15,
            "profitMargins": 0.40,
        }
        defaults.update(kwargs)
        return defaults

    def test_returns_stock_fundamentals(self):
        mock_ticker = MagicMock()
        mock_ticker.info = self._make_yf_info()
        with patch("stock_research.yf.Ticker", return_value=mock_ticker):
            result = fetch_stock_fundamentals("2330")
        assert isinstance(result, StockFundamentals)

    def test_maps_pe_ratio(self):
        mock_ticker = MagicMock()
        mock_ticker.info = self._make_yf_info(trailingPE=18.5)
        with patch("stock_research.yf.Ticker", return_value=mock_ticker):
            result = fetch_stock_fundamentals("2330")
        assert result.pe_ratio == pytest.approx(18.5)

    def test_maps_eps(self):
        mock_ticker = MagicMock()
        mock_ticker.info = self._make_yf_info(trailingEps=55.0)
        with patch("stock_research.yf.Ticker", return_value=mock_ticker):
            result = fetch_stock_fundamentals("2330")
        assert result.eps == pytest.approx(55.0)

    def test_uses_tw_suffix(self):
        mock_ticker = MagicMock()
        mock_ticker.info = self._make_yf_info()
        with patch("stock_research.yf.Ticker", return_value=mock_ticker) as mock_yf:
            fetch_stock_fundamentals("2330")
        mock_yf.assert_called_once_with("2330.TW")

    def test_handles_api_error_gracefully(self):
        with patch("stock_research.yf.Ticker", side_effect=Exception("API 失敗")):
            result = fetch_stock_fundamentals("2330")
        assert isinstance(result, StockFundamentals)
        assert result.pe_ratio is None

    def test_handles_missing_fields(self):
        mock_ticker = MagicMock()
        mock_ticker.info = {}  # empty
        with patch("stock_research.yf.Ticker", return_value=mock_ticker):
            result = fetch_stock_fundamentals("2330")
        assert result.pe_ratio is None
        assert result.eps is None


# ── analyze_stock_ai ──────────────────────────────────────────────────────────

class TestAnalyzeStockAi:
    def test_returns_dict_with_action(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "response": '{"action": "buy", "reason": "RSI 低", "confidence": 7, "target_price": 900.0, "stop_loss": 820.0}'
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("stock_research.requests.post", return_value=mock_resp):
            result = analyze_stock_ai("2330", "台積電", SAMPLE_NEWS, make_fundamentals(), SAMPLE_INDICATORS)
        assert "action" in result
        assert result["action"] in ("buy", "sell", "hold")

    def test_defaults_to_hold_on_error(self):
        with patch("stock_research.requests.post", side_effect=Exception("timeout")):
            result = analyze_stock_ai("2330", "台積電", [], make_fundamentals(), {})
        assert result["action"] == "hold"
