"""tests/test_daytrading_report.py — TDD tests for daytrading_report.py"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pytest


def _make_pick(code="2330", name="台積電", confidence=7):
    return {"code": code, "name": name, "confidence": confidence}


def _make_assessment(score=7, verdict="✅ 適合當沖", good=None, bad=None, data_ok=True):
    return {
        "score": score,
        "verdict": verdict,
        "data_ok": data_ok,
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
    def _call(self, picks, assessment_score=7, assessment_data_ok=True,
              hist_win_rate=None, market=None, chip_today=None):
        """
        Helper：patch 所有外部依賴，讓測試只驗證 build_daytrading_report 邏輯。

        picks              : 傳給 _get_stock_universe 的候選股列表
        assessment_score   : _assess_day_trading 回傳的評分（所有股票相同）
        assessment_data_ok : _assess_day_trading 回傳的 data_ok 旗標
        """
        from daytrading_report import build_daytrading_report
        if market is None:
            market = {"index_change_pct": 0.0, "futures_premium_pct": 0.0}
        if chip_today is None:
            chip_today = {}

        assessment = _make_assessment(score=assessment_score, data_ok=assessment_data_ok)

        def _mock_cont(code, end_date, data_fetcher=None, days=5):
            entry = chip_today.get(code, {})
            return {
                "foreign_continuous_buy": entry.get("foreign_continuous_buy", 0),
                "investment_trust_continuous_buy": entry.get("investment_trust_continuous_buy", 0),
            }

        with patch("daytrading_report._get_stock_universe", return_value=picks), \
             patch("daytrading_report._fetch_historical_win_rate", return_value=hist_win_rate), \
             patch("daytrading_report._fetch_market", return_value=market), \
             patch("daytrading_report._fetch_chip_data", return_value=chip_today), \
             patch("daytrading_report._get_indicators", return_value=None), \
             patch("chip_data.get_continuous_buy_days", side_effect=_mock_cont), \
             patch("stock_query._fetch_annual_trend", return_value={"error": "skip", "monthly_closes": []}), \
             patch("stock_query._assess_day_trading", return_value=assessment):
            return build_daytrading_report(api=None, db_path=":memory:")

    def test_no_picks_returns_waiting_message(self):
        report = self._call([])
        assert "無法取得" in report

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


# ===========================================================================
# P0 fix: 資料不足 / 門檻 / AI 呼叫防護
# ===========================================================================

class TestDataSufficiencyGating:
    """驗證「資料不足」三路分流與 AI 呼叫防護。"""

    def _call_with_assessment(self, picks, assessment, market=None):
        from daytrading_report import build_daytrading_report
        if market is None:
            market = {"index_change_pct": 0.0, "futures_premium_pct": 0.0}
        with patch("daytrading_report._get_stock_universe", return_value=picks), \
             patch("daytrading_report._fetch_historical_win_rate", return_value=None), \
             patch("daytrading_report._fetch_market", return_value=market), \
             patch("daytrading_report._fetch_chip_data", return_value={}), \
             patch("daytrading_report._get_indicators", return_value=None), \
             patch("stock_query._fetch_annual_trend", return_value={"error": "skip", "monthly_closes": []}), \
             patch("stock_query._assess_day_trading", return_value=assessment):
            return build_daytrading_report(api=None, db_path=":memory:")

    def test_no_data_stock_not_in_qualified(self):
        """indicators=None → data_ok=False → 不進 qualified → 不顯示在報表中。"""
        picks = [_make_pick("2330", "台積電")]
        assessment = _make_assessment(score=8, data_ok=False,
                                      verdict="⚠️ 資料不足")
        report = self._call_with_assessment(picks, assessment)
        assert "2330" not in report or "資料不足" in report or "建議觀望" in report

    def test_all_no_data_returns_insufficient_message(self):
        """所有股票 data_ok=False → 報表顯示資料不足，而非可交易清單。"""
        picks = [_make_pick("2330"), _make_pick("2317", "鴻海")]
        assessment = _make_assessment(score=0, data_ok=False,
                                      verdict="⚠️ 資料不足")
        report = self._call_with_assessment(picks, assessment)
        assert "資料不足" in report
        # 不應出現進場區間或預測勝率
        assert "預測勝率" not in report

    def test_has_data_but_all_below_threshold_returns_observe(self):
        """所有股票 data_ok=True 但 score < 4 → 顯示「建議觀望」而非可交易清單。"""
        picks = [_make_pick("2330"), _make_pick("2317", "鴻海")]
        assessment = _make_assessment(score=3, data_ok=True,
                                      verdict="❌ 不建議當沖")
        report = self._call_with_assessment(picks, assessment)
        assert "建議觀望" in report
        assert "預測勝率" not in report

    def test_ai_not_called_for_no_data_stocks(self):
        """data_ok=False 的股票不應觸發 run_daytrading_analysis。"""
        picks = [_make_pick("2330"), _make_pick("2317", "鴻海")]
        assessment = _make_assessment(score=0, data_ok=False)
        with patch("daytrading_report._get_stock_universe", return_value=picks), \
             patch("daytrading_report._fetch_historical_win_rate", return_value=None), \
             patch("daytrading_report._fetch_market",
                   return_value={"index_change_pct": 0.0, "futures_premium_pct": 0.0}), \
             patch("daytrading_report._fetch_chip_data", return_value={}), \
             patch("daytrading_report._get_indicators", return_value=None), \
             patch("stock_query._fetch_annual_trend",
                   return_value={"error": "skip", "monthly_closes": []}), \
             patch("stock_query._assess_day_trading", return_value=assessment), \
             patch("daytrading_report.run_daytrading_analysis") as mock_ai:
            from daytrading_report import build_daytrading_report
            build_daytrading_report(api=None, db_path=":memory:")
        mock_ai.assert_not_called()

    def test_ai_not_called_for_below_threshold_stocks(self):
        """score < _MIN_DT_SCORE 且 data_ok=True → run_daytrading_analysis 不應被呼叫。"""
        picks = [_make_pick("2330")]
        assessment = _make_assessment(score=3, data_ok=True,
                                      verdict="❌ 不建議當沖")
        with patch("daytrading_report._get_stock_universe", return_value=picks), \
             patch("daytrading_report._fetch_historical_win_rate", return_value=None), \
             patch("daytrading_report._fetch_market",
                   return_value={"index_change_pct": 0.0, "futures_premium_pct": 0.0}), \
             patch("daytrading_report._fetch_chip_data", return_value={}), \
             patch("daytrading_report._get_indicators", return_value=None), \
             patch("stock_query._fetch_annual_trend",
                   return_value={"error": "skip", "monthly_closes": []}), \
             patch("stock_query._assess_day_trading", return_value=assessment), \
             patch("daytrading_report.run_daytrading_analysis") as mock_ai:
            from daytrading_report import build_daytrading_report
            build_daytrading_report(api=None, db_path=":memory:")
        mock_ai.assert_not_called()

    def test_high_score_with_data_produces_normal_report(self):
        """data_ok=True + score >= 4 → 正常報表，含預測勝率與股票代號。"""
        picks = [_make_pick("2330", "台積電")]
        assessment = _make_assessment(score=8, data_ok=True,
                                      verdict="✅ 適合當沖")
        report = self._call_with_assessment(picks, assessment)
        assert "2330" in report
        assert "台積電" in report
        assert "預測勝率" in report


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
