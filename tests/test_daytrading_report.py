"""tests/test_daytrading_report.py — TDD tests for daytrading_report.py"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pytest


def _make_pick(code="2330", name="台積電", confidence=7):
    return {"code": code, "name": name, "confidence": confidence}


def _make_assessment(score=7, verdict="✅ 適合當沖", good=None, bad=None):
    return {
        "score": score,
        "verdict": verdict,
        "reasons_good": good or ["量比充足"],
        "reasons_bad": bad or [],
    }


class TestConfidenceToWinPct:
    def test_confidence_0_gives_30_pct(self):
        from daytrading_report import _confidence_to_win_pct
        assert _confidence_to_win_pct(0) == 30.0

    def test_confidence_10_gives_80_pct(self):
        from daytrading_report import _confidence_to_win_pct
        assert _confidence_to_win_pct(10) == 80.0

    def test_confidence_5_gives_55_pct(self):
        from daytrading_report import _confidence_to_win_pct
        assert _confidence_to_win_pct(5) == 55.0

    def test_out_of_range_clamped(self):
        from daytrading_report import _confidence_to_win_pct
        assert _confidence_to_win_pct(-5) == 30.0
        assert _confidence_to_win_pct(99) == 80.0


class TestBuildDaytradingReport:
    def _call(self, picks, assessment_score=7, hist_win_rate=None):
        from daytrading_report import build_daytrading_report
        with patch("research_db.load_daily_plan", return_value=picks), \
             patch("daytrading_report._fetch_historical_win_rate", return_value=hist_win_rate), \
             patch("daytrading_report._get_indicators", return_value=None), \
             patch("stock_query._fetch_annual_trend", return_value={"error": "skip", "monthly_closes": []}), \
             patch("stock_query._assess_day_trading", return_value=_make_assessment(score=assessment_score)):
            return build_daytrading_report(api=None, db_path=":memory:")

    def test_no_picks_returns_waiting_message(self):
        report = self._call([])
        assert "尚無候選股" in report

    def test_report_contains_code_and_name(self):
        report = self._call([_make_pick("2330", "台積電", confidence=8)])
        assert "2330" in report
        assert "台積電" in report

    def test_report_contains_win_pct(self):
        report = self._call([_make_pick(confidence=8)])
        assert "預測勝率" in report
        assert "%" in report

    def test_low_score_stocks_excluded(self):
        """score < 4 → 不顯示"""
        report = self._call([_make_pick("9999", "低分股", confidence=3)], assessment_score=3)
        assert "建議觀望" in report or "9999" not in report

    def test_historical_win_rate_shown_when_available(self):
        report = self._call([_make_pick()], hist_win_rate=62.5)
        assert "62.5%" in report

    def test_historical_win_rate_hidden_when_none(self):
        report = self._call([_make_pick()], hist_win_rate=None)
        assert "近 30 日" not in report

    def test_returns_string_always(self):
        assert isinstance(self._call([]), str)
        assert isinstance(self._call([_make_pick()]), str)

    def test_disclaimer_present(self):
        report = self._call([_make_pick()])
        assert "非保證獲利" in report


class TestTelegramBotDaytradingRouting:
    def _make_update(self, text):
        return {"message": {"text": text, "chat": {"id": 123}, "from": {"id": 456}}}

    def _call(self, text):
        import telegram_bot
        with patch.object(telegram_bot, "CHAT_ID", "123"), \
             patch.object(telegram_bot, "USER_ID", ""), \
             patch("telegram_bot._is_authorized", return_value=True), \
             patch("telegram_bot.send_text") as mock_send, \
             patch("daytrading_report.build_daytrading_report", return_value="當沖報告") as mock_report:
            telegram_bot.process_update(self._make_update(text))
            return mock_send, mock_report

    def test_button_triggers_daytrading(self):
        _, mock_report = self._call("🎯 今日當沖預測")
        mock_report.assert_called_once()

    def test_text_command_triggers(self):
        _, mock_report = self._call("今日當沖")
        mock_report.assert_called_once()

    def test_slash_command_triggers(self):
        _, mock_report = self._call("/當沖")
        mock_report.assert_called_once()
