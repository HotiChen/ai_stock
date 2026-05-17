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
    def _call(self, picks, assessment_score=7, hist_win_rate=None,
              market=None, chip_today=None):
        from daytrading_report import build_daytrading_report
        if market is None:
            market = {"index_change_pct": 0.0}
        if chip_today is None:
            chip_today = {}
        # get_continuous_buy_days 回傳 chip_today 裡已有的連續天數，避免打真實 API
        def _mock_cont(code, end_date, data_fetcher=None, days=5):
            entry = chip_today.get(code, {})
            return {
                "foreign_continuous_buy": entry.get("foreign_continuous_buy", 0),
                "investment_trust_continuous_buy": entry.get("investment_trust_continuous_buy", 0),
            }
        with patch("research_db.load_daily_plan", return_value=picks), \
             patch("daytrading_report._fetch_historical_win_rate", return_value=hist_win_rate), \
             patch("daytrading_report._fetch_market", return_value=market), \
             patch("daytrading_report._fetch_chip_data", return_value=chip_today), \
             patch("daytrading_report._get_indicators", return_value=None), \
             patch("chip_data.get_continuous_buy_days", side_effect=_mock_cont), \
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

    def test_market_label_shown_in_report(self):
        report = self._call([_make_pick()], market={"index_change_pct": 1.2})
        assert "大盤" in report
        assert "強勢" in report

    def test_market_crash_label(self):
        report = self._call([_make_pick()], market={"index_change_pct": -1.5})
        assert "大跌" in report

    def test_chip_foreign_net_shown(self):
        chip_today = {"2330": {"foreign_net": 1200, "investment_trust_net": 0,
                               "dealer_net": 0, "total_net": 1200}}
        report = self._call([_make_pick("2330")], chip_today=chip_today)
        assert "外資" in report

    def test_chip_continuous_buy_shown(self):
        chip_today = {"2330": {"foreign_net": 800, "investment_trust_net": 0,
                               "dealer_net": 0, "total_net": 800,
                               "foreign_continuous_buy": 4,
                               "investment_trust_continuous_buy": 0}}
        report = self._call([_make_pick("2330")], chip_today=chip_today)
        assert "連買 4 日" in report


class TestAssessDayTradingChipMarket:
    """Tests for chip and market scoring in _assess_day_trading()"""

    def _base_indicators(self, volume_ratio=1.5, rsi=55.0):
        return {
            "volume_ratio": volume_ratio, "RSI": rsi, "ATR": 5.0,
            "current_price": 100.0, "bullish_alignment": False,
            "bearish_alignment": False, "BB_position": 0.5,
            "KD_K": 55.0, "KD_D": 50.0,
        }

    def _annual(self):
        return {"error": None, "monthly_closes": [], "change_pct": 0}

    def _assess(self, chip=None, market=None, volume_ratio=1.5, rsi=55.0):
        from stock_query import _assess_day_trading
        return _assess_day_trading(
            self._base_indicators(volume_ratio, rsi),
            self._annual(),
            chip=chip,
            market=market,
        )

    def test_chip_none_does_not_change_score(self):
        assert self._assess()["score"] == self._assess(chip=None)["score"]

    def test_foreign_strong_buy_adds_2(self):
        base = self._assess()["score"]
        result = self._assess(chip={"foreign_net": 1500, "investment_trust_net": 0,
                                    "dealer_net": 0, "foreign_continuous_buy": 0})
        # score is clamped at 10, so check increment or cap
        assert result["score"] >= min(10, base + 2)
        assert any("外資" in r for r in result["reasons_good"])

    def test_foreign_strong_sell_reduces_2(self):
        base = self._assess()["score"]
        result = self._assess(chip={"foreign_net": -1500, "investment_trust_net": 0,
                                    "dealer_net": 0, "foreign_continuous_buy": 0})
        assert result["score"] <= base - 2
        assert any("外資" in r for r in result["reasons_bad"])

    def test_trust_buy_adds_1(self):
        base = self._assess()["score"]
        result = self._assess(chip={"foreign_net": 0, "investment_trust_net": 500,
                                    "dealer_net": 0, "foreign_continuous_buy": 0})
        assert result["score"] == base + 1

    def test_continuous_buy_3days_adds_1(self):
        base = self._assess()["score"]
        result = self._assess(chip={"foreign_net": 0, "investment_trust_net": 0,
                                    "dealer_net": 0, "foreign_continuous_buy": 3})
        assert result["score"] == base + 1

    def test_market_crash_reduces_2(self):
        base = self._assess()["score"]
        result = self._assess(market={"index_change_pct": -1.5})
        assert result["score"] == base - 2
        assert any("大盤" in r for r in result["reasons_bad"])

    def test_market_strong_adds_1(self):
        base = self._assess()["score"]
        result = self._assess(market={"index_change_pct": 1.2})
        assert result["score"] == base + 1

    def test_market_flat_no_change(self):
        base = self._assess()["score"]
        assert self._assess(market={"index_change_pct": 0.1})["score"] == base

    def test_market_none_no_change(self):
        base = self._assess()["score"]
        assert self._assess(market=None)["score"] == base

    def test_score_clamped_0_to_10(self):
        result = self._assess(
            chip={"foreign_net": 2000, "investment_trust_net": 1000,
                  "dealer_net": 500, "foreign_continuous_buy": 5},
            market={"index_change_pct": 2.0},
        )
        assert 0 <= result["score"] <= 10


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
