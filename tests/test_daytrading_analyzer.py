"""tests/test_daytrading_analyzer.py — TDD tests for daytrading_analyzer.py (PR-3)"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _indicators(price=100.0, rsi=55.0, vr=1.8, vwap=98.0):
    return {
        "current_price": price,
        "RSI": rsi,
        "volume_ratio": vr,
        "VWAP": vwap,
        "ATR": 3.0,
        "KD_K": 60.0,
        "KD_D": 55.0,
        "BB_position": 0.6,
        "bullish_alignment": True,
        "bearish_alignment": False,
    }


def _chip():
    return {
        "foreign_net": 800,
        "investment_trust_net": 200,
        "foreign_continuous_buy": 3,
    }


def _market(pct=0.5):
    return {"index_change_pct": pct}


def _valid_json(action="long", confidence=7,
                entry_low=99.0, entry_high=101.0,
                target_price=105.0, stop_loss=97.0):
    return json.dumps({
        "action": action,
        "confidence": confidence,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "target_price": target_price,
        "stop_loss": stop_loss,
        "timing": "拉回",
        "summary": "量比充足，站上 VWAP，適合逢拉回買進。",
    })


# ── DayTradingAnalysis dataclass ──────────────────────────────────────────────

class TestDayTradingAnalysisDataclass:
    def test_fields_accessible(self):
        from daytrading_analyzer import DayTradingAnalysis
        a = DayTradingAnalysis(
            code="2330", name="台積電",
            action="long", confidence=8,
            entry_low=99.0, entry_high=101.0,
            target_price=105.0, stop_loss=97.0,
            timing="拉回", summary="測試",
        )
        assert a.code == "2330"
        assert a.confidence == 8
        assert a.entry_low == 99.0

    def test_optional_fields_can_be_none(self):
        from daytrading_analyzer import DayTradingAnalysis
        a = DayTradingAnalysis(
            code="2330", name="台積電",
            action="skip", confidence=2,
            entry_low=None, entry_high=None,
            target_price=None, stop_loss=None,
            timing="觀望", summary="分數偏低",
        )
        assert a.target_price is None
        assert a.stop_loss is None


# ── build_daytrading_prompt ───────────────────────────────────────────────────

class TestBuildDaytradingPrompt:
    def _prompt(self, **kw):
        from daytrading_analyzer import build_daytrading_prompt
        defaults = dict(
            code="2330", name="台積電",
            indicators=_indicators(),
            chip=_chip(),
            market=_market(),
            dt_score=7,
        )
        defaults.update(kw)
        return build_daytrading_prompt(**defaults)

    def test_contains_code_and_name(self):
        p = self._prompt()
        assert "2330" in p and "台積電" in p

    def test_contains_rsi(self):
        p = self._prompt()
        assert "RSI" in p

    def test_contains_vwap(self):
        p = self._prompt()
        assert "VWAP" in p

    def test_contains_chip_info(self):
        p = self._prompt()
        assert "外資" in p

    def test_contains_market_info(self):
        p = self._prompt()
        assert "大盤" in p

    def test_requests_json_output(self):
        p = self._prompt()
        assert "JSON" in p or "json" in p

    def test_no_chip_still_builds(self):
        p = self._prompt(chip=None)
        assert "2330" in p

    def test_no_indicators_still_builds(self):
        p = self._prompt(indicators=None)
        assert "2330" in p


# ── parse_daytrading_response ─────────────────────────────────────────────────

class TestParseDaytradingResponse:
    def _parse(self, raw, code="2330", name="台積電"):
        from daytrading_analyzer import parse_daytrading_response
        return parse_daytrading_response(code, name, raw)

    def test_valid_json_returns_correct_fields(self):
        result = self._parse(_valid_json())
        assert result.action == "long"
        assert result.confidence == 7
        assert result.entry_low == 99.0
        assert result.target_price == 105.0
        assert result.timing == "拉回"

    def test_json_in_markdown_block_extracted(self):
        raw = "```json\n" + _valid_json() + "\n```"
        result = self._parse(raw)
        assert result.action == "long"

    def test_invalid_action_defaults_to_skip(self):
        raw = _valid_json(action="moon")
        result = self._parse(raw)
        assert result.action == "skip"

    def test_confidence_clamped_0_to_10(self):
        raw = _valid_json(confidence=99)
        result = self._parse(raw)
        assert result.confidence <= 10

        raw = _valid_json(confidence=-5)
        result = self._parse(raw)
        assert result.confidence >= 0

    def test_invalid_json_returns_skip(self):
        result = self._parse("not json at all")
        assert result.action == "skip"
        assert result.confidence == 0

    def test_empty_string_returns_skip(self):
        result = self._parse("")
        assert result.action == "skip"

    def test_returns_dataclass_always(self):
        from daytrading_analyzer import DayTradingAnalysis
        result = self._parse("garbage")
        assert isinstance(result, DayTradingAnalysis)

    def test_code_and_name_preserved(self):
        result = self._parse(_valid_json(), code="2454", name="聯發科")
        assert result.code == "2454"
        assert result.name == "聯發科"


# ── Price interval validation (P1) ───────────────────────────────────────────

class TestLongPriceValidation:
    """action="long" 時必須通過金融邏輯驗證，否則降級為 skip。"""

    def _parse(self, raw):
        from daytrading_analyzer import parse_daytrading_response
        return parse_daytrading_response("2330", "台積電", raw)

    def _degraded(self, result):
        """Assert the result is a properly degraded skip."""
        assert result.action == "skip"
        assert result.confidence == 0
        assert result.timing == "觀望"
        assert "無效" in result.summary or "跳過" in result.summary
        assert result.entry_low is None
        assert result.entry_high is None
        assert result.target_price is None
        assert result.stop_loss is None

    # 1. 合法區間正常通過
    def test_valid_long_passes(self):
        result = self._parse(_valid_json())
        assert result.action == "long"
        assert result.entry_low == 99.0
        assert result.entry_high == 101.0
        assert result.target_price == 105.0
        assert result.stop_loss == 97.0

    # 2. entry_low > entry_high → skip
    def test_entry_low_gt_entry_high_degrades(self):
        raw = _valid_json(entry_low=105.0, entry_high=99.0)
        self._degraded(self._parse(raw))

    # 3. stop_loss == entry_low → skip
    def test_stop_loss_equal_entry_low_degrades(self):
        raw = _valid_json(stop_loss=99.0, entry_low=99.0)
        self._degraded(self._parse(raw))

    # 4. stop_loss > entry_low → skip
    def test_stop_loss_gt_entry_low_degrades(self):
        raw = _valid_json(stop_loss=103.0, entry_low=99.0)
        self._degraded(self._parse(raw))

    # 5. target_price == entry_high → skip
    def test_target_price_equal_entry_high_degrades(self):
        raw = _valid_json(target_price=101.0, entry_high=101.0)
        self._degraded(self._parse(raw))

    # 6. target_price < entry_high → skip
    def test_target_price_lt_entry_high_degrades(self):
        raw = _valid_json(target_price=98.0, entry_high=101.0)
        self._degraded(self._parse(raw))

    # 7. entry_low = 0 → skip
    def test_zero_entry_low_degrades(self):
        raw = _valid_json(entry_low=0.0)
        self._degraded(self._parse(raw))

    # 8. stop_loss 為負數 → skip
    def test_negative_stop_loss_degrades(self):
        raw = _valid_json(stop_loss=-5.0)
        self._degraded(self._parse(raw))

    # 9. entry_low 為 null → skip
    def test_missing_entry_low_degrades(self):
        raw = json.dumps({
            "action": "long", "confidence": 7,
            "entry_low": None, "entry_high": 101.0,
            "target_price": 105.0, "stop_loss": 97.0,
            "timing": "拉回", "summary": "test",
        })
        self._degraded(self._parse(raw))

    # 10. action="skip" 即使價格全 null 也正常保留 skip
    def test_skip_with_null_prices_preserved(self):
        raw = json.dumps({
            "action": "skip", "confidence": 0,
            "entry_low": None, "entry_high": None,
            "target_price": None, "stop_loss": None,
            "timing": "觀望", "summary": "不適合",
        })
        result = self._parse(raw)
        assert result.action == "skip"
        assert result.entry_low is None
        # 不可因為 null 價格而改變 skip 的 summary
        assert result.summary == "不適合"


# ── run_daytrading_analysis ───────────────────────────────────────────────────

class TestRunDaytradingAnalysis:
    def _run(self, dt_score=7, ai_raw=None, **kw):
        from daytrading_analyzer import run_daytrading_analysis
        if ai_raw is None:
            ai_raw = _valid_json()
        with patch("daytrading_analyzer.call_haiku", return_value=ai_raw) as mock_ai:
            result = run_daytrading_analysis(
                code="2330", name="台積電",
                indicators=_indicators(),
                chip=_chip(),
                market=_market(),
                dt_score=dt_score,
                **kw,
            )
        return result, mock_ai

    def test_returns_daytrading_analysis(self):
        from daytrading_analyzer import DayTradingAnalysis
        result, _ = self._run()
        assert isinstance(result, DayTradingAnalysis)

    def test_calls_haiku_for_high_score(self):
        _, mock_ai = self._run(dt_score=7)
        mock_ai.assert_called_once()

    def test_low_score_skips_ai_call(self):
        """dt_score < 4 直接回傳 skip，不呼叫 AI"""
        result, mock_ai = self._run(dt_score=3)
        mock_ai.assert_not_called()
        assert result.action == "skip"

    def test_api_failure_returns_skip(self):
        result, _ = self._run(ai_raw="")
        assert result.action == "skip"

    def test_haiku_exception_returns_skip(self):
        from daytrading_analyzer import run_daytrading_analysis
        with patch("daytrading_analyzer.call_haiku", side_effect=Exception("timeout")):
            result = run_daytrading_analysis(
                code="2330", name="台積電",
                indicators=_indicators(), chip=_chip(),
                market=_market(), dt_score=7,
            )
        assert result.action == "skip"

    def test_result_code_and_name_match(self):
        result, _ = self._run()
        assert result.code == "2330"
        assert result.name == "台積電"


# ── Integration: daytrading_report with AI analysis ──────────────────────────

class TestDaytradingReportWithAI:
    def _make_pick(self, code="2330", name="台積電"):
        return {"code": code, "name": name}

    def _call(self, picks, ai_result=None):
        from daytrading_report import build_daytrading_report
        from daytrading_analyzer import DayTradingAnalysis

        if ai_result is None:
            ai_result = DayTradingAnalysis(
                code="2330", name="台積電",
                action="long", confidence=8,
                entry_low=99.0, entry_high=101.0,
                target_price=105.0, stop_loss=97.0,
                timing="拉回", summary="量比充足，站上 VWAP，適合拉回買進。",
            )

        with patch("daytrading_report._get_stock_universe", return_value=picks), \
             patch("daytrading_report._fetch_historical_win_rate", return_value=None), \
             patch("daytrading_report._fetch_market",
                   return_value={"index_change_pct": 0.5, "futures_premium_pct": 0.0}), \
             patch("daytrading_report._fetch_chip_data", return_value={}), \
             patch("daytrading_report._get_indicators", return_value=None), \
             patch("stock_query._fetch_annual_trend",
                   return_value={"error": "skip", "monthly_closes": []}), \
             patch("stock_query._assess_day_trading",
                   return_value={"score": 7, "verdict": "✅ 適合當沖",
                                 "data_ok": True,
                                 "reasons_good": ["量比充足"], "reasons_bad": []}), \
             patch("daytrading_report.run_daytrading_analysis",
                   return_value=ai_result) as mock_ai:
            report = build_daytrading_report(api=None)
        return report, mock_ai

    def test_ai_analysis_called_for_top_stocks(self):
        _, mock_ai = self._call([self._make_pick()])
        mock_ai.assert_called()

    def test_report_contains_ai_summary(self):
        report, _ = self._call([self._make_pick()])
        assert "拉回" in report or "VWAP" in report or "進場" in report

    def test_report_contains_entry_zone(self):
        report, _ = self._call([self._make_pick()])
        assert "99" in report or "101" in report or "進場" in report

    def test_report_contains_target_and_stop(self):
        report, _ = self._call([self._make_pick()])
        assert "105" in report or "97" in report or "目標" in report

    def test_invalid_long_prices_not_shown_in_report(self):
        """降級為 skip 的 long（無效價格區間）不得在報表顯示進場建議。"""
        from daytrading_analyzer import DayTradingAnalysis
        # 模擬 parse_daytrading_response 已降級為 skip 的結果
        degraded = DayTradingAnalysis(
            code="2330", name="台積電",
            action="skip", confidence=0,
            entry_low=None, entry_high=None,
            target_price=None, stop_loss=None,
            timing="觀望",
            summary="AI 回傳價格區間無效，已保守跳過",
        )
        report, _ = self._call([self._make_pick()], ai_result=degraded)
        assert "進場區間" not in report
        # degraded summary 不應出現（report 只在 action=="long" 時顯示 summary）
        assert "AI 回傳價格區間無效" not in report
