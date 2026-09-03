"""
tests/test_halt_visibility.py — HALT 不得再靜默，且強平時間只能有一個

背景：2026-08-21 08:25 有人按了「🚨 緊急暫停」，接下來 12 天系統每天靜靜
跳過所有工作——不寫 log、不告警、不過期、狀態查詢也看不到。process 活著、
log 乾淨、Telegram 無異常，完全沒有任何跡象。

另外系統有**兩個**強平時間設定：
    config.FORCE_CLOSE_TIME     13:15   ← ForceCloseJob / main 主迴圈
    config.DT_FORCE_CLOSE_TIME  13:00   ← DaytradingConfig / 模擬與規則
兩者可以各自被 .env 設成不同的值（正式環境確實如此），於是「什麼時候平倉」
這個問題在系統裡有兩個答案。
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def halt_file(tmp_path, monkeypatch):
    import halt as halt_mod
    monkeypatch.setattr(halt_mod, "_HALT_FILE", tmp_path / "HALT")
    return halt_mod


class TestStartupAnnouncement:
    def test_warns_and_notifies_when_halted(self, halt_file):
        """★ 啟動時處於暫停狀態必須大聲說出來——12 天靜默的直接教訓。"""
        import main
        halt_file.halt(reason="test")
        with patch.object(main, "TELEGRAM_CHAT_ID", "123"), \
             patch("telegram_bot.send_text") as send:
            main._announce_halt_state_on_startup()
        assert send.called
        body = send.call_args[0][1]
        assert "暫停" in body
        assert "賣出" in body or "平倉" in body, "要說明賣出與平倉不受影響"

    def test_silent_when_not_halted(self, halt_file):
        import main
        with patch.object(main, "TELEGRAM_CHAT_ID", "123"), \
             patch("telegram_bot.send_text") as send:
            main._announce_halt_state_on_startup()
        assert not send.called

    def test_never_raises_without_chat_id(self, halt_file):
        import main
        halt_file.halt(reason="test")
        with patch.object(main, "TELEGRAM_CHAT_ID", ""):
            main._announce_halt_state_on_startup()   # 不得拋出


class TestHourlyReminder:
    def test_first_call_reminds(self, halt_file):
        import main
        halt_file.halt(reason="test")
        state = {}
        with patch.object(main, "TELEGRAM_CHAT_ID", "123"), \
             patch("telegram_bot.send_text") as send:
            main._maybe_halt_reminder(datetime(2026, 9, 3, 10, 0), state)
        assert send.called

    def test_not_spammy_within_the_hour(self, halt_file):
        """★ 提醒要有，但不能洗版——每小時最多一次。"""
        import main
        halt_file.halt(reason="test")
        state = {}
        with patch.object(main, "TELEGRAM_CHAT_ID", "123"), \
             patch("telegram_bot.send_text") as send:
            main._maybe_halt_reminder(datetime(2026, 9, 3, 10, 0), state)
            main._maybe_halt_reminder(datetime(2026, 9, 3, 10, 30), state)
            main._maybe_halt_reminder(datetime(2026, 9, 3, 10, 59), state)
        assert send.call_count == 1

    def test_reminds_again_next_hour(self, halt_file):
        import main
        halt_file.halt(reason="test")
        state = {}
        with patch.object(main, "TELEGRAM_CHAT_ID", "123"), \
             patch("telegram_bot.send_text") as send:
            main._maybe_halt_reminder(datetime(2026, 9, 3, 10, 0), state)
            main._maybe_halt_reminder(datetime(2026, 9, 3, 11, 1), state)
        assert send.call_count == 2

    def test_silent_when_not_halted(self, halt_file):
        import main
        with patch.object(main, "TELEGRAM_CHAT_ID", "123"), \
             patch("telegram_bot.send_text") as send:
            main._maybe_halt_reminder(datetime(2026, 9, 3, 10, 0), {})
        assert not send.called


def _cb(chat_id: str, data: str) -> dict:
    """建構 Telegram callback_query，形狀與真實 webhook 一致。"""
    return {"id": "cb1", "data": data,
            "message": {"chat": {"id": int(chat_id)}}}


class TestEmergencyHaltConfirmation:
    def test_button_only_asks_does_not_write_flag(self, halt_file):
        """★ 按鈕不得直接生效。

        「🚨 緊急暫停」和「❓ 說明」在鍵盤上相鄰，而撤單、平倉都有二次確認，
        只有它沒有——2026-08-21 就是這樣被誤觸的。
        """
        import telegram_bot
        with patch.object(telegram_bot, "send_text") as send:
            telegram_bot.handle_emergency_halt("123")
        assert not halt_file.is_halted(), "只按按鈕不得寫入 HALT 旗標"
        markup = send.call_args.kwargs.get("reply_markup")
        assert markup, "必須附上 inline 確認鍵盤"

    def test_confirm_callback_writes_flag(self, halt_file):
        import telegram_bot
        with patch.object(telegram_bot, "send_text"), \
             patch("notifier._send"), patch.object(telegram_bot, "_post"):
            telegram_bot.handle_callback(_cb("123", "halt_confirm"))
        assert halt_file.is_halted()

    def test_abort_callback_leaves_no_trace(self, halt_file):
        import telegram_bot
        with patch.object(telegram_bot, "send_text"), \
             patch.object(telegram_bot, "_post"):
            telegram_bot.handle_callback(_cb("123", "halt_abort"))
        assert not halt_file.is_halted()

    def test_wording_matches_actual_behaviour(self, halt_file):
        """★ 文案原本寫「今日不再執行任何下單」，但旗標是永久的、而且它擋的
        不只下單。對使用者說出口的話就是規格。"""
        import telegram_bot
        with patch.object(telegram_bot, "send_text") as send, \
             patch("notifier._send"), patch.object(telegram_bot, "_post"):
            telegram_bot.handle_callback(_cb("123", "halt_confirm"))
        body = " ".join(str(c) for c in send.call_args_list)
        assert "今日" not in body, "旗標非當日失效，不可寫「今日」"
        assert "買進" in body
        assert "停損" in body or "平倉" in body


class TestSingleForceCloseTime:
    def test_dt_time_follows_main_setting_by_default(self, monkeypatch):
        """未特別設定 DT_FORCE_CLOSE_TIME 時，必須跟隨 FORCE_CLOSE_TIME。"""
        monkeypatch.delenv("DT_FORCE_CLOSE_TIME", raising=False)
        monkeypatch.setenv("FORCE_CLOSE_TIME", "13:10")
        import importlib

        import config
        importlib.reload(config)
        assert config.DT_FORCE_CLOSE_TIME == "13:10"
        importlib.reload(config)

    def test_explicit_dt_time_wins_for_both(self, monkeypatch):
        """★ 兩個都設且不同時，系統只能有一個答案——以 DT 的為準並告警，
        絕不能一邊 13:00、一邊 13:15。"""
        monkeypatch.setenv("FORCE_CLOSE_TIME", "13:15")
        monkeypatch.setenv("DT_FORCE_CLOSE_TIME", "13:00")
        import importlib

        import config
        importlib.reload(config)
        assert config.FORCE_CLOSE_TIME == config.DT_FORCE_CLOSE_TIME == "13:00"
        importlib.reload(config)
