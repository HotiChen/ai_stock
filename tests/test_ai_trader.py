from unittest.mock import patch, MagicMock

import pytest

from ai_trader import build_decision_prompt, build_enriched_decision_prompt, make_decision, make_enriched_decision, sanitize_decision
from stock_research import StockFundamentals


FAKE_INDICATORS = {"MA5": 600, "MA20": 580, "RSI": 45, "MACD": 1.2}
FAKE_NEWS = {"sentiment": "positive", "score": 7, "reason": "利多消息", "headlines": []}
FAKE_POSITION = {"stock_code": "2330", "quantity": 1000, "cost": 580.0}
FAKE_STOCK_NEWS = ["台積電 Q2 營收創高", "外資連買台積電 5 日"]
FAKE_FUNDAMENTALS = StockFundamentals(
    pe_ratio=18.5, eps=55.3, market_cap=2_000_000_000_000,
    week52_high=1000.0, week52_low=700.0,
    dividend_yield=0.025, revenue_growth=0.12, profit_margin=0.38,
)


class TestBuildDecisionPrompt:
    def test_returns_string(self):
        prompt = build_decision_prompt(
            indicators=FAKE_INDICATORS,
            news=FAKE_NEWS,
            position=None,
            cash=30000,
            already_bought_today=False,
            already_sold_today=False,
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 50

    def test_contains_cash_info(self):
        prompt = build_decision_prompt(FAKE_INDICATORS, FAKE_NEWS, None, 25000, False, False)
        assert "25000" in prompt

    def test_contains_already_bought_flag(self):
        prompt = build_decision_prompt(FAKE_INDICATORS, FAKE_NEWS, None, 30000, True, False)
        assert "是" in prompt

    def test_contains_position_info(self):
        prompt = build_decision_prompt(FAKE_INDICATORS, FAKE_NEWS, FAKE_POSITION, 30000, True, False)
        assert "2330" in prompt

    def test_no_position_shows_no_holding(self):
        prompt = build_decision_prompt(FAKE_INDICATORS, FAKE_NEWS, None, 30000, False, False)
        assert "無持倉" in prompt

    def test_news_sentiment_included(self):
        prompt = build_decision_prompt(FAKE_INDICATORS, FAKE_NEWS, None, 30000, False, False)
        assert "positive" in prompt or "利多" in prompt


class TestSanitizeDecision:
    def test_valid_buy_passes(self):
        decision = {"action": "buy", "stock_code": "2330", "quantity": 1, "reason": "ok", "confidence": 7}
        result = sanitize_decision(decision)
        assert result["action"] == "buy"

    def test_invalid_action_defaults_to_hold(self):
        decision = {"action": "explode", "stock_code": "2330", "quantity": 1, "reason": "", "confidence": 5}
        result = sanitize_decision(decision)
        assert result["action"] == "hold"

    def test_missing_keys_filled_with_defaults(self):
        result = sanitize_decision({"action": "hold"})
        assert "stock_code" in result
        assert "quantity" in result
        assert "reason" in result
        assert "confidence" in result

    def test_quantity_clipped_to_minimum_1(self):
        decision = {"action": "buy", "stock_code": "2330", "quantity": -5, "reason": "", "confidence": 5}
        result = sanitize_decision(decision)
        assert result["quantity"] >= 1

    def test_confidence_clipped_0_to_10(self):
        decision = {"action": "hold", "stock_code": "", "quantity": 0, "reason": "", "confidence": 999}
        result = sanitize_decision(decision)
        assert 0 <= result["confidence"] <= 10


class TestBuildEnrichedDecisionPrompt:
    def test_returns_string(self):
        prompt = build_enriched_decision_prompt(
            code="2330", name="台積電",
            indicators=FAKE_INDICATORS, market_news=FAKE_NEWS,
            stock_news=FAKE_STOCK_NEWS, fundamentals=FAKE_FUNDAMENTALS,
            position=None, cash=30000,
            already_bought_today=False, already_sold_today=False,
        )
        assert isinstance(prompt, str) and len(prompt) > 100

    def test_includes_stock_specific_news(self):
        prompt = build_enriched_decision_prompt(
            code="2330", name="台積電",
            indicators=FAKE_INDICATORS, market_news=FAKE_NEWS,
            stock_news=FAKE_STOCK_NEWS, fundamentals=FAKE_FUNDAMENTALS,
            position=None, cash=30000,
            already_bought_today=False, already_sold_today=False,
        )
        assert "台積電 Q2 營收創高" in prompt or "外資連買" in prompt

    def test_includes_pe_ratio(self):
        prompt = build_enriched_decision_prompt(
            code="2330", name="台積電",
            indicators=FAKE_INDICATORS, market_news=FAKE_NEWS,
            stock_news=[], fundamentals=FAKE_FUNDAMENTALS,
            position=None, cash=30000,
            already_bought_today=False, already_sold_today=False,
        )
        assert "18.5" in prompt or "本益比" in prompt or "PE" in prompt

    def test_includes_general_market_sentiment(self):
        prompt = build_enriched_decision_prompt(
            code="2330", name="台積電",
            indicators=FAKE_INDICATORS, market_news=FAKE_NEWS,
            stock_news=[], fundamentals=FAKE_FUNDAMENTALS,
            position=None, cash=30000,
            already_bought_today=False, already_sold_today=False,
        )
        assert "positive" in prompt or "利多" in prompt

    def test_works_with_none_fundamentals(self):
        prompt = build_enriched_decision_prompt(
            code="2330", name="台積電",
            indicators=FAKE_INDICATORS, market_news=FAKE_NEWS,
            stock_news=[], fundamentals=None,
            position=None, cash=30000,
            already_bought_today=False, already_sold_today=False,
        )
        assert isinstance(prompt, str)

    def test_works_with_empty_stock_news(self):
        prompt = build_enriched_decision_prompt(
            code="2330", name="台積電",
            indicators=FAKE_INDICATORS, market_news=FAKE_NEWS,
            stock_news=[], fundamentals=None,
            position=None, cash=30000,
            already_bought_today=False, already_sold_today=False,
        )
        assert isinstance(prompt, str)


class TestMakeEnrichedDecision:
    @patch("ai_trader.fetch_stock_news", return_value=FAKE_STOCK_NEWS)
    @patch("ai_trader.fetch_stock_fundamentals", return_value=FAKE_FUNDAMENTALS)
    @patch("ai_trader.call_ollama")
    def test_returns_valid_decision(self, mock_ollama, mock_fund, mock_news):
        mock_ollama.return_value = '{"action": "buy", "stock_code": "2330", "quantity": 1, "reason": "RSI低+基本面佳", "confidence": 8}'
        result = make_enriched_decision(
            code="2330", name="台積電",
            indicators=FAKE_INDICATORS, market_news=FAKE_NEWS,
            position=None, cash=30000,
        )
        assert result["action"] == "buy"
        assert result["confidence"] == 8

    @patch("ai_trader.fetch_stock_news", return_value=FAKE_STOCK_NEWS)
    @patch("ai_trader.fetch_stock_fundamentals", return_value=FAKE_FUNDAMENTALS)
    @patch("ai_trader.call_ollama")
    def test_calls_stock_news_fetch(self, mock_ollama, mock_fund, mock_news):
        mock_ollama.return_value = '{"action": "hold", "stock_code": "", "quantity": 1, "reason": "觀望", "confidence": 5}'
        make_enriched_decision("2330", "台積電", FAKE_INDICATORS, FAKE_NEWS, None, 30000)
        mock_news.assert_called_once_with("2330", "台積電")

    @patch("ai_trader.fetch_stock_news", return_value=FAKE_STOCK_NEWS)
    @patch("ai_trader.fetch_stock_fundamentals", return_value=FAKE_FUNDAMENTALS)
    @patch("ai_trader.call_ollama")
    def test_calls_fundamentals_fetch(self, mock_ollama, mock_fund, mock_news):
        mock_ollama.return_value = '{"action": "hold", "stock_code": "", "quantity": 1, "reason": "觀望", "confidence": 5}'
        make_enriched_decision("2330", "台積電", FAKE_INDICATORS, FAKE_NEWS, None, 30000)
        mock_fund.assert_called_once_with("2330")

    @patch("ai_trader.fetch_stock_news", side_effect=Exception("網路錯誤"))
    @patch("ai_trader.fetch_stock_fundamentals", return_value=FAKE_FUNDAMENTALS)
    @patch("ai_trader.call_ollama")
    def test_news_fetch_failure_still_decides(self, mock_ollama, mock_fund, mock_news):
        mock_ollama.return_value = '{"action": "hold", "stock_code": "", "quantity": 1, "reason": "無新聞", "confidence": 4}'
        result = make_enriched_decision("2330", "台積電", FAKE_INDICATORS, FAKE_NEWS, None, 30000)
        assert result["action"] in ("buy", "sell", "hold")

    @patch("ai_trader.fetch_stock_news", return_value=[])
    @patch("ai_trader.fetch_stock_fundamentals", side_effect=Exception("API 失敗"))
    @patch("ai_trader.call_ollama")
    def test_fundamentals_fetch_failure_still_decides(self, mock_ollama, mock_fund, mock_news):
        mock_ollama.return_value = '{"action": "hold", "stock_code": "", "quantity": 1, "reason": "無基本面", "confidence": 4}'
        result = make_enriched_decision("2330", "台積電", FAKE_INDICATORS, FAKE_NEWS, None, 30000)
        assert result["action"] in ("buy", "sell", "hold")

    def test_both_bought_and_sold_returns_hold(self):
        result = make_enriched_decision(
            "2330", "台積電", FAKE_INDICATORS, FAKE_NEWS, None, 30000,
            already_bought_today=True, already_sold_today=True,
        )
        assert result["action"] == "hold"


class TestMakeDecision:
    def test_both_bought_and_sold_returns_hold(self):
        result = make_decision(
            indicators=FAKE_INDICATORS,
            news=FAKE_NEWS,
            position=None,
            cash=30000,
            already_bought_today=True,
            already_sold_today=True,
        )
        assert result["action"] == "hold"

    @patch("ai_trader.call_ollama")
    def test_valid_buy_decision(self, mock_ollama):
        mock_ollama.return_value = '{"action": "buy", "stock_code": "2330", "quantity": 1, "reason": "RSI低", "confidence": 7}'
        result = make_decision(FAKE_INDICATORS, FAKE_NEWS, None, 30000)
        assert result["action"] == "buy"
        assert result["stock_code"] == "2330"

    @patch("ai_trader.call_ollama")
    def test_ollama_failure_returns_hold(self, mock_ollama):
        mock_ollama.side_effect = Exception("timeout")
        result = make_decision(FAKE_INDICATORS, FAKE_NEWS, None, 30000)
        assert result["action"] == "hold"

    @patch("ai_trader.call_ollama")
    def test_garbled_response_returns_hold(self, mock_ollama):
        mock_ollama.return_value = "我不知道要怎麼做"
        result = make_decision(FAKE_INDICATORS, FAKE_NEWS, None, 30000)
        assert result["action"] == "hold"

    @patch("ai_trader.call_ollama")
    def test_sell_only_when_has_position(self, mock_ollama):
        mock_ollama.return_value = '{"action": "sell", "stock_code": "2330", "quantity": 1, "reason": "停利", "confidence": 8}'
        result = make_decision(FAKE_INDICATORS, FAKE_NEWS, FAKE_POSITION, 0, already_bought_today=True)
        assert result["action"] == "sell"

    @patch("ai_trader.call_ollama")
    def test_result_always_has_required_keys(self, mock_ollama):
        mock_ollama.return_value = '{"action": "hold"}'
        result = make_decision(FAKE_INDICATORS, FAKE_NEWS, None, 30000)
        for key in ("action", "stock_code", "quantity", "reason", "confidence"):
            assert key in result
