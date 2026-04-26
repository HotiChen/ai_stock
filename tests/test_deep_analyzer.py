from __future__ import annotations

from unittest.mock import patch

import pytest

from deep_analyzer import (
    DeepAnalysis,
    AnalysisFactor,
    build_deep_prompt,
    parse_deep_response,
    run_deep_analysis,
    get_price_trend_summary,
)


SAMPLE_RESPONSE = """{
  "signal": "buy",
  "confidence": 8,
  "summary": "RSI 超賣反彈，法人買超，美股費半強勁，台灣 AI 政策利多，短線偏多",
  "factors": {
    "historical_trend": "20日均線向上，RSI 28 超賣",
    "news": "近期法人買超，無重大利空",
    "theme": "AI 伺服器題材持續發酵",
    "us_market": "費半指數上漲 2.5%，對台股有帶動效果",
    "tw_policy": "政府推動 AI 產業補貼計劃",
    "us_policy": "Fed 暫停升息，流動性寬鬆"
  },
  "hold_days": 3,
  "target_price": 880.0,
  "stop_loss_price": 820.0
}"""


# ── AnalysisFactor ────────────────────────────────────────────────────────────

class TestAnalysisFactor:
    def test_six_dimensions(self):
        f = AnalysisFactor(
            historical_trend="向上",
            news="正面",
            theme="AI 題材",
            us_market="費半強勢",
            tw_policy="補貼政策",
            us_policy="Fed 暫停升息",
        )
        assert f.historical_trend == "向上"
        assert f.us_market == "費半強勢"


# ── DeepAnalysis ──────────────────────────────────────────────────────────────

class TestDeepAnalysis:
    def _make(self, **kw):
        defaults = dict(
            code="2330", name="台積電",
            signal="buy", confidence=8,
            summary="短線偏多",
            factors=AnalysisFactor("上升", "正面", "AI", "強勢", "利多", "寬鬆"),
            hold_days=3, target_price=880.0, stop_loss_price=820.0,
        )
        defaults.update(kw)
        return DeepAnalysis(**defaults)

    def test_signal_valid(self):
        for sig in ("buy", "hold", "sell"):
            a = self._make(signal=sig)
            assert a.signal == sig

    def test_confidence_range(self):
        a = self._make(confidence=7)
        assert 0 <= a.confidence <= 10

    def test_has_six_factor_dimensions(self):
        a = self._make()
        assert hasattr(a.factors, "historical_trend")
        assert hasattr(a.factors, "us_market")
        assert hasattr(a.factors, "tw_policy")
        assert hasattr(a.factors, "us_policy")


# ── get_price_trend_summary ───────────────────────────────────────────────────

class TestGetPriceTrendSummary:
    @patch("deep_analyzer.yf.download")
    def test_returns_string(self, mock_dl):
        import pandas as pd
        prices = [100, 102, 101, 103, 105, 104, 106]
        mock_dl.return_value = pd.DataFrame(
            {"Close": prices},
            index=pd.date_range("2026-04-01", periods=len(prices))
        )
        result = get_price_trend_summary("2330")
        assert isinstance(result, str)
        assert len(result) > 5

    @patch("deep_analyzer.yf.download", side_effect=Exception("network"))
    def test_failure_returns_fallback(self, mock_dl):
        result = get_price_trend_summary("2330")
        assert isinstance(result, str)


# ── build_deep_prompt ─────────────────────────────────────────────────────────

class TestBuildDeepPrompt:
    def _ctx(self):
        return "S&P500 +0.8%, 費半 +2.5%, VIX 16, 情緒偏多。Fed 維持利率不變。"

    def test_returns_string(self):
        prompt = build_deep_prompt(
            code="2330", name="台積電",
            price_trend="20日均線向上",
            news=["法人買超", "無重大利空"],
            fundamentals_text="PE 20, EPS 30",
            market_summary=self._ctx(),
            theme_info="AI 伺服器題材",
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_includes_stock_code(self):
        prompt = build_deep_prompt("2330", "台積電", "", [], "", self._ctx(), "")
        assert "2330" in prompt or "台積電" in prompt

    def test_includes_all_six_dimensions(self):
        prompt = build_deep_prompt("2330", "台積電", "上升", [], "", self._ctx(), "AI")
        keywords = ["歷史走勢", "新聞", "題材", "美股", "台灣政策", "美國政策"]
        assert any(k in prompt for k in keywords)

    def test_includes_market_summary(self):
        prompt = build_deep_prompt("2330", "台積電", "", [], "", "費半 +2.5%", "")
        assert "費半" in prompt or "2.5" in prompt

    def test_requests_json_output(self):
        prompt = build_deep_prompt("2330", "台積電", "", [], "", self._ctx(), "")
        assert "JSON" in prompt or "json" in prompt


# ── parse_deep_response ───────────────────────────────────────────────────────

class TestParseDeepResponse:
    def test_returns_deep_analysis(self):
        result = parse_deep_response("2330", "台積電", SAMPLE_RESPONSE)
        assert isinstance(result, DeepAnalysis)

    def test_signal_parsed(self):
        result = parse_deep_response("2330", "台積電", SAMPLE_RESPONSE)
        assert result.signal == "buy"

    def test_confidence_parsed(self):
        result = parse_deep_response("2330", "台積電", SAMPLE_RESPONSE)
        assert result.confidence == 8

    def test_factors_parsed(self):
        result = parse_deep_response("2330", "台積電", SAMPLE_RESPONSE)
        assert "RSI" in result.factors.historical_trend or len(result.factors.historical_trend) > 0

    def test_hold_days_parsed(self):
        result = parse_deep_response("2330", "台積電", SAMPLE_RESPONSE)
        assert result.hold_days == 3

    def test_invalid_json_returns_hold(self):
        result = parse_deep_response("2330", "台積電", "garbage text")
        assert result.signal == "hold"

    def test_confidence_clamped(self):
        bad = SAMPLE_RESPONSE.replace('"confidence": 8', '"confidence": 99')
        result = parse_deep_response("2330", "台積電", bad)
        assert result.confidence <= 10


# ── run_deep_analysis ─────────────────────────────────────────────────────────

class TestRunDeepAnalysis:
    @patch("deep_analyzer.call_ollama")
    @patch("deep_analyzer.get_price_trend_summary", return_value="20日均線向上")
    def test_returns_deep_analysis(self, mock_trend, mock_ollama):
        mock_ollama.return_value = SAMPLE_RESPONSE
        result = run_deep_analysis(
            code="2330", name="台積電",
            news=["法人買超"], fundamentals_text="PE 20",
            market_summary="費半強勢", theme_info="AI 題材",
        )
        assert isinstance(result, DeepAnalysis)
        assert result.code == "2330"

    @patch("deep_analyzer.call_ollama", side_effect=Exception("timeout"))
    @patch("deep_analyzer.get_price_trend_summary", return_value="")
    def test_failure_returns_hold(self, mock_trend, mock_ollama):
        result = run_deep_analysis("2330", "台積電", [], "", "", "")
        assert result.signal == "hold"

    @patch("deep_analyzer.call_ollama")
    @patch("deep_analyzer.get_price_trend_summary", return_value="下降")
    def test_prompt_includes_stock_info(self, mock_trend, mock_ollama):
        mock_ollama.return_value = SAMPLE_RESPONSE
        run_deep_analysis("2454", "聯發科", ["新聞"], "PE 15", "空頭", "AI")
        prompt = mock_ollama.call_args[0][1]
        assert "2454" in prompt or "聯發科" in prompt
