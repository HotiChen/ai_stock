from __future__ import annotations

from unittest.mock import MagicMock, patch, call
from pathlib import Path

import pytest

import halt


@pytest.fixture(autouse=True)
def tmp_halt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(halt, "_HALT_FILE", tmp_path / "HALT")


# ── halt / resume / is_halted ─────────────────────────────────────────────────

def test_initially_not_halted():
    assert halt.is_halted() is False


def test_halt_sets_flag():
    halt.halt()
    assert halt.is_halted() is True


def test_halt_writes_reason():
    halt.halt(reason="test_reason")
    content = halt._HALT_FILE.read_text()
    assert "test_reason" in content


def test_resume_clears_flag():
    halt.halt()
    halt.resume()
    assert halt.is_halted() is False


def test_resume_is_idempotent():
    halt.resume()  # no flag exists — should not raise
    assert halt.is_halted() is False


def test_halt_then_resume_then_halt_again():
    halt.halt()
    halt.resume()
    halt.halt()
    assert halt.is_halted() is True


# ── cancel_all_orders ─────────────────────────────────────────────────────────

def _make_trade(order_id: str, status: str):
    trade = MagicMock()
    trade.order.id = order_id
    trade.order.code = "2330"
    trade.status.status = status
    return trade


def test_cancel_all_cancels_submitted_orders():
    api = MagicMock()
    api.list_trades.return_value = [
        _make_trade("A001", "Submitted"),
        _make_trade("A002", "PendingSubmit"),
    ]
    result = halt.cancel_all_orders(api)
    assert set(result["cancelled"]) == {"A001", "A002"}
    assert result["failed"] == []
    assert api.cancel_order.call_count == 2


def test_cancel_all_skips_filled_orders():
    api = MagicMock()
    api.list_trades.return_value = [
        _make_trade("A001", "Filled"),
        _make_trade("A002", "Cancelled"),
    ]
    result = halt.cancel_all_orders(api)
    assert result["cancelled"] == []
    api.cancel_order.assert_not_called()


def test_cancel_all_handles_partial_failure():
    api = MagicMock()
    api.list_trades.return_value = [
        _make_trade("A001", "Submitted"),
        _make_trade("A002", "Submitted"),
    ]
    api.cancel_order.side_effect = [None, Exception("timeout")]
    result = halt.cancel_all_orders(api)
    assert "A001" in result["cancelled"]
    assert "A002" in result["failed"]


def test_cancel_all_returns_error_on_api_failure():
    api = MagicMock()
    api.update_status.side_effect = Exception("connection lost")
    result = halt.cancel_all_orders(api)
    assert result["cancelled"] == []
    assert "error" in result


def test_cancel_all_empty_trades():
    api = MagicMock()
    api.list_trades.return_value = []
    result = halt.cancel_all_orders(api)
    assert result["cancelled"] == []
    assert result["failed"] == []


# ── telegram_bot integration ──────────────────────────────────────────────────

def test_bot_halt_button_sets_flag(monkeypatch):
    import telegram_bot as bot
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_text"), \
         patch("notifier._send"):
        bot.handle_emergency_halt("123")
    assert halt.is_halted()


def test_bot_halt_button_already_halted(monkeypatch):
    import telegram_bot as bot
    halt.halt()
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_text") as mock_send:
        bot.handle_emergency_halt("123")
        text = mock_send.call_args[0][1]
        assert "已經是暫停" in text


def test_resume_command_via_bot(monkeypatch):
    import telegram_bot as bot
    halt.halt()
    monkeypatch.setattr(bot, "CHAT_ID", "123")

    update = {"update_id": 1, "message": {"chat": {"id": "123"}, "text": "恢復系統"}}
    with patch("telegram_bot.send_text") as mock_send:
        bot.process_update(update)
        assert not halt.is_halted()
        assert "已恢復" in mock_send.call_args[0][1]


def test_resume_when_not_halted(monkeypatch):
    import telegram_bot as bot
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    update = {"update_id": 1, "message": {"chat": {"id": "123"}, "text": "恢復系統"}}
    with patch("telegram_bot.send_text") as mock_send:
        bot.process_update(update)
        assert "正常運作" in mock_send.call_args[0][1]
