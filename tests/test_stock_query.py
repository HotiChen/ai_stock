"""
tests/test_stock_query.py — TDD tests for stock_query.py and telegram_bot routing.

Phase 1: Tests written FIRST (all should fail until implementation exists).
"""
from __future__ import annotations

import re
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers: build a minimal indicators dict
# ---------------------------------------------------------------------------

def _make_indicators(
    current_price=100.0,
    MA5=102.0, MA10=101.0, MA20=100.0, MA60=98.0,
    RSI=55.0,
    KD_K=60.0, KD_D=55.0,
    BB_upper=110.0, BB_lower=90.0, BB_position=0.5,
    volume_ratio=1.5,
    ATR=2.0,
    bullish_alignment=True,
    bearish_alignment=False,
) -> dict:
    return dict(
        current_price=current_price,
        MA5=MA5, MA10=MA10, MA20=MA20, MA60=MA60,
        RSI=RSI,
        KD_K=KD_K, KD_D=KD_D,
        BB_upper=BB_upper, BB_lower=BB_lower, BB_position=BB_position,
        volume_ratio=volume_ratio,
        ATR=ATR,
        bullish_alignment=bullish_alignment,
        bearish_alignment=bearish_alignment,
    )


def _make_annual(
    start_price=100.0,
    end_price=115.0,
    high_52w=120.0,
    low_52w=85.0,
    change_pct=15.0,
    monthly_closes=None,
    error=None,
) -> dict:
    return dict(
        start_price=start_price,
        end_price=end_price,
        high_52w=high_52w,
        low_52w=low_52w,
        change_pct=change_pct,
        monthly_closes=monthly_closes or [95.0, 100.0, 110.0, 115.0],
        error=error,
    )


# ===========================================================================
# Tests for _assess_day_trading()
# ===========================================================================

class TestAssessDayTrading:
    """Tests 1-4: _assess_day_trading(indicators, annual) -> dict"""

    def _import(self):
        from stock_query import _assess_day_trading
        return _assess_day_trading

    def test_high_volume_healthy_rsi_is_suitable(self):
        """Test 1: volume_ratio>=2.0 + healthy RSI → 適合當沖, score>=6"""
        _assess_day_trading = self._import()
        ind = _make_indicators(volume_ratio=2.5, RSI=55.0)
        annual = _make_annual()
        result = _assess_day_trading(ind, annual)
        assert "適合" in result["verdict"]
        assert result["score"] >= 6

    def test_low_volume_is_not_recommended(self):
        """Test 2: volume_ratio<0.8 → 不建議當沖, reasons_bad has volume message"""
        _assess_day_trading = self._import()
        ind = _make_indicators(volume_ratio=0.5)
        annual = _make_annual()
        result = _assess_day_trading(ind, annual)
        assert "不建議" in result["verdict"]
        reasons_bad_text = " ".join(result["reasons_bad"])
        # Should mention volume/量
        assert any("量" in r or "volume" in r.lower() for r in result["reasons_bad"])

    def test_overbought_rsi_adds_bad_reason(self):
        """Test 3: RSI>75 → reasons_bad contains overbought warning"""
        _assess_day_trading = self._import()
        ind = _make_indicators(RSI=80.0, volume_ratio=2.0)
        annual = _make_annual()
        result = _assess_day_trading(ind, annual)
        reasons_bad_text = " ".join(result["reasons_bad"])
        assert any("超買" in r or "RSI" in r for r in result["reasons_bad"])

    def test_none_indicators_does_not_raise(self):
        """Test 4: indicators=None → returns dict, verdict not empty, no exception"""
        _assess_day_trading = self._import()
        annual = _make_annual()
        result = _assess_day_trading(None, annual)
        assert isinstance(result, dict)
        assert "verdict" in result
        assert result["verdict"]  # not empty


# ===========================================================================
# Tests for _fetch_annual_trend()
# ===========================================================================

class TestFetchAnnualTrend:
    """Tests 5-6: _fetch_annual_trend(code, api=None) -> dict"""

    def _import(self):
        from stock_query import _fetch_annual_trend
        return _fetch_annual_trend

    def test_yfinance_success_calculates_correctly(self):
        """Test 5: yfinance returns data → change_pct correct, monthly_closes non-empty"""
        _fetch_annual_trend = self._import()

        import pandas as pd
        import numpy as np

        # Build a fake 252-row OHLCV DataFrame
        dates = pd.date_range("2025-01-01", periods=252, freq="B")
        closes = np.linspace(100.0, 120.0, 252)
        df = pd.DataFrame({
            "Close": closes,
            "High": closes + 2,
            "Low": closes - 2,
            "Volume": np.ones(252) * 1_000_000,
        }, index=dates)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = _fetch_annual_trend("2330")

        assert result.get("error") is None or result.get("error") == ""
        assert result["monthly_closes"] is not None and len(result["monthly_closes"]) > 0
        # change_pct should be positive (120 > 100)
        assert result["change_pct"] > 0

    def test_yfinance_failure_returns_error_field(self):
        """Test 6: yfinance raises → error field non-empty, no exception raised"""
        _fetch_annual_trend = self._import()

        with patch("yfinance.Ticker", side_effect=Exception("network error")):
            result = _fetch_annual_trend("9999")

        assert isinstance(result, dict)
        assert result.get("error")  # non-empty error


# ===========================================================================
# Tests for format_query_report()
# ===========================================================================

class TestFormatQueryReport:
    """Tests 7-10: format_query_report(...) -> str"""

    def _import(self):
        from stock_query import format_query_report
        return format_query_report

    def test_contains_code_and_name(self):
        """Test 7: report contains stock code and name"""
        format_query_report = self._import()
        ind = _make_indicators()
        annual = _make_annual()
        report = format_query_report(
            code="2330",
            name="台積電",
            indicators=ind,
            annual=annual,
            news=["新聞1", "新聞2"],
            analysis=None,
            dt_assessment={"verdict": "✅ 適合當沖", "score": 7,
                           "reasons_good": [], "reasons_bad": []},
        )
        assert "2330" in report
        assert "台積電" in report

    def test_none_indicators_does_not_raise(self):
        """Test 8: indicators=None → graceful degradation, no exception"""
        format_query_report = self._import()
        annual = _make_annual()
        report = format_query_report(
            code="2330",
            name="台積電",
            indicators=None,
            annual=annual,
            news=["新聞1"],
            analysis=None,
            dt_assessment={"verdict": "🟡 尚可", "score": 5,
                           "reasons_good": [], "reasons_bad": []},
        )
        assert isinstance(report, str)
        assert "2330" in report

    def test_none_analysis_does_not_raise(self):
        """Test 9: analysis=None → no exception"""
        format_query_report = self._import()
        ind = _make_indicators()
        annual = _make_annual()
        report = format_query_report(
            code="2454",
            name="聯發科",
            indicators=ind,
            annual=annual,
            news=["新聞1"],
            analysis=None,
            dt_assessment={"verdict": "✅ 適合當沖", "score": 8,
                           "reasons_good": [], "reasons_bad": []},
        )
        assert isinstance(report, str)

    def test_empty_news_shows_no_news_message(self):
        """Test 10: news=[] → report contains '無近期新聞' or similar"""
        format_query_report = self._import()
        ind = _make_indicators()
        annual = _make_annual()
        report = format_query_report(
            code="0050",
            name="元大台灣50",
            indicators=ind,
            annual=annual,
            news=[],
            analysis=None,
            dt_assessment={"verdict": "❌ 不建議當沖", "score": 2,
                           "reasons_good": [], "reasons_bad": []},
        )
        assert isinstance(report, str)
        assert "無" in report or "沒有" in report or "no news" in report.lower()


# ===========================================================================
# Tests for query_stock()
# ===========================================================================

class TestQueryStock:
    """Tests 11-13: query_stock(code, api=None) -> str"""

    def _import(self):
        from stock_query import query_stock
        return query_stock

    def test_api_none_returns_str_with_code(self):
        """Test 11: api=None → returns str containing code, no exception"""
        query_stock = self._import()
        with patch("stock_query._fetch_stock_name", return_value="台積電"), \
             patch("stock_query._fetch_annual_trend", return_value=_make_annual()), \
             patch("stock_query._assess_day_trading", return_value={
                 "verdict": "✅ 適合當沖", "score": 7, "reasons_good": [], "reasons_bad": []}), \
             patch("stock_query.format_query_report", return_value="report 2330"):
            result = query_stock("2330", api=None)
        assert isinstance(result, str)
        assert "2330" in result

    def test_invalid_code_returns_error_string(self):
        """Test 12: invalid code (non-digit / too short) → error string, no exception"""
        query_stock = self._import()
        result = query_stock("AB")  # non-numeric / too short
        assert isinstance(result, str)
        assert result  # non-empty

    def test_all_dependencies_fail_still_returns_str(self):
        """Test 13: all external deps fail → still returns str, no exception"""
        query_stock = self._import()
        with patch("stock_query._fetch_stock_name", side_effect=Exception("err")), \
             patch("stock_query._fetch_annual_trend", side_effect=Exception("err")), \
             patch("technical_indicators.fetch_indicators", side_effect=Exception("err")):
            result = query_stock("2330", api=None)
        assert isinstance(result, str)
        assert result


# ===========================================================================
# Tests for telegram_bot routing (Phase 3)
# ===========================================================================

class TestTelegramBotRouting:
    """Tests 14-18: process_update() routing for stock query patterns"""

    def _make_update(self, text: str, chat_id: str = "123", user_id: str = "456") -> dict:
        return {
            "message": {
                "text": text,
                "chat": {"id": int(chat_id)},
                "from": {"id": int(user_id)},
            }
        }

    def _call_process_update(self, text: str):
        """Patch env vars, _is_authorized, and call process_update."""
        import telegram_bot
        update = self._make_update(text)
        with patch.object(telegram_bot, "CHAT_ID", "123"), \
             patch.object(telegram_bot, "USER_ID", ""), \
             patch("telegram_bot._is_authorized", return_value=True), \
             patch("telegram_bot.send_text") as mock_send, \
             patch("stock_query.query_stock", return_value="查股結果") as mock_query:
            telegram_bot.process_update(update)
            return mock_send, mock_query

    def test_pure_digits_triggers_query_stock(self):
        """Test 14: '2330' (4-6 digits) → query_stock called with code='2330'"""
        mock_send, mock_query = self._call_process_update("2330")
        mock_query.assert_called_once()
        assert mock_query.call_args[0][0] == "2330" or mock_query.call_args[1].get("code") == "2330"

    def test_slash_query_command_triggers(self):
        """Test 15: '/查股 2454' → query_stock called with code='2454'"""
        mock_send, mock_query = self._call_process_update("/查股 2454")
        mock_query.assert_called_once()
        args = mock_query.call_args
        assert args[0][0] == "2454" or args[1].get("code") == "2454"

    def test_cha_command_triggers(self):
        """Test 16: '查 0050' → query_stock called with code='0050'"""
        mock_send, mock_query = self._call_process_update("查 0050")
        mock_query.assert_called_once()
        args = mock_query.call_args
        assert args[0][0] == "0050" or args[1].get("code") == "0050"

    def test_hello_does_not_trigger_query(self):
        """Test 17: 'hello' → query_stock NOT called"""
        mock_send, mock_query = self._call_process_update("hello")
        mock_query.assert_not_called()

    def test_eight_digit_code_does_not_trigger(self):
        """Test 18: '12345678' (8 digits, too long) → query_stock NOT called"""
        mock_send, mock_query = self._call_process_update("12345678")
        mock_query.assert_not_called()
