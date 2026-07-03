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
             patch("stock_query._assess_day_trading", return_value=assessment):
            return build_daytrading_report(api=None, db_path=":memory:")

    def test_no_picks_returns_waiting_message(self):
        report = self._call([])
        assert "無法取得" in report

    def test_report_contains_code_and_name(self):
        report = self._call([_make_pick("2330", "台積電", confidence=8)])
        assert "2330" in report
        assert "台積電" in report

    def test_report_shows_technical_score(self):
        """報表顯示技術評分 x/10，不顯示偽勝率百分比。"""
        report = self._call([_make_pick()])
        assert "技術評分" in report or "當沖" in report
        assert "預測勝率" not in report

    def test_report_does_not_map_score_to_win_pct(self):
        """dt_score 不應被映射成百分比後以勝率名義顯示。"""
        report = self._call([_make_pick()], assessment_score=7)
        assert "預測勝率" not in report
        assert "80%" not in report  # 7/10 → 65% 這類映射值不應出現

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
        assert "技術評分為啟發式指標" in report

    def test_disclaimer_does_not_call_score_a_win_rate(self):
        """disclaimer 不得把技術評分稱為勝率。"""
        report = self._call([_make_pick()])
        assert "勝率為統計估算" not in report

    def test_hist_win_rate_and_score_are_separate(self):
        """歷史勝率與技術評分是獨立欄位，不混稱。"""
        report = self._call([_make_pick()], hist_win_rate=62.5)
        # 歷史勝率有明確來源標籤
        assert "近 30 日實際勝率" in report
        assert "62.5%" in report
        # 技術評分仍以 /10 形式出現
        assert "/10" in report

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

    def test_annual_trend_not_fetched_during_dt_scoring(self):
        """build_daytrading_report 不應在當沖評分路徑呼叫 _fetch_annual_trend。"""
        from daytrading_report import build_daytrading_report
        assessment = _make_assessment(score=7, data_ok=True)
        market = {"index_change_pct": 0.0, "futures_premium_pct": 0.0}
        with patch("daytrading_report._get_stock_universe",
                   return_value=[_make_pick()]), \
             patch("daytrading_report._fetch_historical_win_rate", return_value=None), \
             patch("daytrading_report._fetch_market", return_value=market), \
             patch("daytrading_report._fetch_chip_data", return_value={}), \
             patch("daytrading_report._get_indicators", return_value=None), \
             patch("stock_query._assess_day_trading", return_value=assessment), \
             patch("daytrading_report.run_daytrading_analysis",
                   return_value=MagicMock(action="skip")), \
             patch("stock_query._fetch_annual_trend") as mock_annual:
            build_daytrading_report(api=None, db_path=":memory:")
        mock_annual.assert_not_called()


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
             patch("stock_query._assess_day_trading", return_value=assessment), \
             patch("daytrading_report.run_daytrading_analysis") as mock_ai:
            from daytrading_report import build_daytrading_report
            build_daytrading_report(api=None, db_path=":memory:")
        mock_ai.assert_not_called()

    def test_high_score_with_data_produces_normal_report(self):
        """data_ok=True + score >= 4 → 正常報表，含技術評分與股票代號。"""
        picks = [_make_pick("2330", "台積電")]
        assessment = _make_assessment(score=8, data_ok=True,
                                      verdict="✅ 適合當沖")
        report = self._call_with_assessment(picks, assessment)
        assert "2330" in report
        assert "台積電" in report
        assert "8/10" in report        # 技術評分以 x/10 呈現
        assert "預測勝率" not in report  # 不得偽裝成勝率


class TestAssessDayTradingChipMarket:
    """Tests for chip and market scoring in _assess_day_trading()"""

    def _base_indicators(self, volume_ratio=1.5, rsi=55.0):
        return {
            "volume_ratio": volume_ratio, "RSI": rsi, "ATR": 5.0,
            "current_price": 100.0, "bullish_alignment": False,
            "bearish_alignment": False, "BB_position": 0.5,
            "KD_K": 55.0, "KD_D": 50.0,
        }

    def _assess(self, chip=None, market=None, volume_ratio=1.5, rsi=55.0):
        from stock_query import _assess_day_trading
        return _assess_day_trading(
            self._base_indicators(volume_ratio, rsi),
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


class TestSaveWatchingPositions:
    """8b. 儲存盤中監控倉位：所有 AI 分析過的 qualified 標的都要存成 watching，
    不再只存 AI 判斷 long 的，讓 9:05 開盤再確認能對每一支重新判斷。"""

    def _run(self, ai_results):
        """ai_results: dict[code] -> DayTradingAnalysis，模擬 run_daytrading_analysis 回傳。
        回傳 save_daytrading_positions 收到的 positions list（None = 未被呼叫）。"""
        from daytrading_report import build_daytrading_report
        from daytrading_monitor import DaytradingPosition

        picks = [_make_pick(c, n) for c, n in
                 [("2337", "旺宏"), ("2449", "京元電子"), ("2330", "台積電")]]
        assessment = _make_assessment(score=8, data_ok=True)
        captured = {}

        def _fake_save(positions, *a, **k):
            captured["positions"] = positions

        def _fake_ai(code, name, **kw):
            return ai_results[code]

        with patch("daytrading_report._get_stock_universe", return_value=picks), \
             patch("daytrading_report._fetch_historical_win_rate", return_value=None), \
             patch("daytrading_report._fetch_market",
                   return_value={"index_change_pct": 0.0, "futures_premium_pct": 0.0}), \
             patch("daytrading_report._fetch_chip_data", return_value={}), \
             patch("daytrading_report._get_indicators", return_value=None), \
             patch("stock_query._assess_day_trading", return_value=assessment), \
             patch("daytrading_report.run_daytrading_analysis", side_effect=_fake_ai), \
             patch("daytrading_monitor.replace_today", side_effect=_fake_save):
            build_daytrading_report(api=None, db_path=":memory:")
        return captured.get("positions")

    def _ai(self, code, name, action="long"):
        from daytrading_analyzer import DayTradingAnalysis
        if action == "long":
            return DayTradingAnalysis(
                code=code, name=name, action="long", confidence=8,
                entry_low=99.0, entry_high=101.0,
                target_price=105.0, stop_loss=97.0,
                timing="拉回", summary=f"{name} 量比充足",
            )
        return DayTradingAnalysis(
            code=code, name=name, action="skip", confidence=0,
            entry_low=None, entry_high=None,
            target_price=None, stop_loss=None,
            timing="觀望", summary=f"{name} 開盤氣氛不明，觀望",
        )

    def test_skip_picks_saved_as_watching(self):
        """AI 判斷 skip 的標的（如今天 2337/2449）仍要存成 watching。"""
        ai_results = {
            "2337": self._ai("2337", "旺宏", action="skip"),
            "2449": self._ai("2449", "京元電子", action="skip"),
            "2330": self._ai("2330", "台積電", action="skip"),
        }
        positions = self._run(ai_results)
        assert positions is not None, "全 skip 時仍應呼叫 save_daytrading_positions"
        codes = {p.code for p in positions}
        assert {"2337", "2449", "2330"}.issubset(codes)
        assert all(p.status == "watching" for p in positions)

    def test_mixed_long_and_skip_all_saved(self):
        """long 與 skip 混合時，兩者都要存。"""
        ai_results = {
            "2337": self._ai("2337", "旺宏", action="long"),
            "2449": self._ai("2449", "京元電子", action="skip"),
            "2330": self._ai("2330", "台積電", action="long"),
        }
        positions = self._run(ai_results)
        codes = {p.code for p in positions}
        assert codes == {"2337", "2449", "2330"}

    def test_skip_pick_keeps_none_prices(self):
        """skip 標的 entry/target/stop 維持 None（監控與 9:05 reconfirm 皆 None-safe）。"""
        ai_results = {
            "2337": self._ai("2337", "旺宏", action="skip"),
            "2449": self._ai("2449", "京元電子", action="long"),
            "2330": self._ai("2330", "台積電", action="long"),
        }
        positions = self._run(ai_results)
        skip_pos = next(p for p in positions if p.code == "2337")
        assert skip_pos.entry_low is None
        assert skip_pos.target_price is None
        assert skip_pos.stop_loss is None
        # ai_summary 仍保留 skip 理由，供 9:05 reconfirm 參考
        assert "觀望" in skip_pos.ai_summary


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
