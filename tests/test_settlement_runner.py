"""Tests for settlement_runner.py (H10)"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest

import settlement_runner


# ── _is_trading_day ───────────────────────────────────────────────────────────

class TestIsTradingDay:
    @pytest.mark.parametrize("weekday,expected", [
        (0, True),   # Monday
        (1, True),   # Tuesday
        (2, True),   # Wednesday
        (3, True),   # Thursday
        (4, True),   # Friday
        (5, False),  # Saturday
        (6, False),  # Sunday
    ])
    def test_weekday_returns_correct_value(self, weekday, expected):
        fake_dt = MagicMock()
        fake_dt.weekday.return_value = weekday
        with patch("settlement_runner.datetime") as mock_dt:
            mock_dt.now.return_value = fake_dt
            assert settlement_runner._is_trading_day() == expected


# ── run() ─────────────────────────────────────────────────────────────────────

class TestRun:
    def test_non_trading_day_returns_early(self):
        """週末時不呼叫 handle_settle_now。"""
        with patch("settlement_runner._is_trading_day", return_value=False), \
             patch("settlement_runner.log") as mock_log:
            settlement_runner.run()
            # 確認沒有嘗試 import telegram_bot
            mock_log.info.assert_any_call("今日非交易日，略過。")

    def test_empty_chat_id_logs_error_and_returns(self):
        """CHAT_ID 空時記錄錯誤並提早返回，不呼叫 handle_settle_now。"""
        with patch("settlement_runner._is_trading_day", return_value=True), \
             patch("settlement_runner.log") as mock_log:
            # Mock the import inside run()
            mock_bot = MagicMock()
            mock_bot.CHAT_ID = ""
            mock_bot.handle_settle_now = MagicMock()
            with patch.dict("sys.modules", {"telegram_bot": mock_bot}):
                settlement_runner.run()
            mock_bot.handle_settle_now.assert_not_called()

    def test_trading_day_calls_handle_settle_now(self):
        """交易日且 CHAT_ID 有值時應呼叫 handle_settle_now。"""
        with patch("settlement_runner._is_trading_day", return_value=True):
            mock_bot = MagicMock()
            mock_bot.CHAT_ID = "123456"
            mock_bot.handle_settle_now = MagicMock()
            with patch.dict("sys.modules", {"telegram_bot": mock_bot}):
                settlement_runner.run()
            mock_bot.handle_settle_now.assert_called_once_with("123456")

    def test_exception_sends_notification(self):
        """handle_settle_now 拋出例外時，嘗試發送錯誤通知。"""
        with patch("settlement_runner._is_trading_day", return_value=True):
            mock_bot = MagicMock()
            mock_bot.CHAT_ID = "123456"
            mock_bot.handle_settle_now.side_effect = RuntimeError("settle failed")

            mock_notifier = MagicMock()
            mock_notifier._send = MagicMock()

            with patch.dict("sys.modules", {
                "telegram_bot": mock_bot,
                "notifier": mock_notifier,
            }):
                settlement_runner.run()  # 不應 raise

            mock_notifier._send.assert_called_once()
            call_arg = mock_notifier._send.call_args[0][0]
            assert "失敗" in call_arg or "settle failed" in call_arg
