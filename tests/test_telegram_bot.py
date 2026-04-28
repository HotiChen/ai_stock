from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

import telegram_bot as bot


# ── verify_user (via _is_authorized) ─────────────────────────────────────────

def _make_update(chat_id: str, text: str = "hi") -> dict:
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def _make_callback(chat_id: str, data: str) -> dict:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cq1",
            "data": data,
            "from": {"id": chat_id},
            "message": {"chat": {"id": chat_id}},
        },
    }


def test_unauthorized_chat_id_is_blocked(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "999")
    with patch("telegram_bot.send_text") as mock_send:
        bot.process_update(_make_update("888", "📊 今日狀態"))
        mock_send.assert_not_called()


def test_authorized_chat_id_is_allowed(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "999")
    with patch("telegram_bot.handle_status") as mock_handler:
        bot.process_update(_make_update("999", "📊 今日狀態"))
        mock_handler.assert_called_once_with("999")


# ── Button routing ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,handler_name", [
    ("📊 今日狀態",  "handle_status"),
    ("💼 持倉",      "handle_holdings"),
    ("📈 選股計劃",  "handle_plan"),
    ("⚡ 快速下單",  "handle_quick_order"),
    ("🛡️ 停損設定", "handle_stop_loss"),
    ("❓ 說明",      "handle_help"),
])
def test_button_routes_to_correct_handler(text, handler_name, monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch(f"telegram_bot.{handler_name}") as mock_handler:
        bot.process_update(_make_update("123", text))
        mock_handler.assert_called_once_with("123")


def test_unknown_text_shows_main_menu(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_main_menu") as mock_menu:
        bot.process_update(_make_update("123", "隨便亂打"))
        mock_menu.assert_called_once()


def test_start_command_shows_main_menu(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_main_menu") as mock_menu:
        bot.process_update(_make_update("123", "/start"))
        mock_menu.assert_called_once()


# ── handle_callback ───────────────────────────────────────────────────────────

def test_callback_order_confirm(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot._post"), \
         patch("telegram_bot.send_text") as mock_send:
        bot.process_update(_make_callback("123", "order_confirm"))
        text = mock_send.call_args[0][1]
        assert "確認" in text or "下單" in text


def test_callback_order_cancel(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot._post"), \
         patch("telegram_bot.send_text") as mock_send, \
         patch("telegram_bot.send_main_menu"):
        bot.process_update(_make_callback("123", "order_cancel"))
        text = mock_send.call_args[0][1]
        assert "取消" in text


def test_callback_unauthorized_is_blocked(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "999")
    with patch("telegram_bot._post") as mock_post, \
         patch("telegram_bot.send_text") as mock_send:
        bot.process_update(_make_callback("888", "order_confirm"))
        mock_send.assert_not_called()


# ── approve / reject per-stock callbacks ─────────────────────────────────────

@pytest.mark.parametrize("code", ["2330", "2317", "0050", "2454", "3008", "9999"])
def test_approve_any_stock_replies_with_code(code, monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot._post"), \
         patch("telegram_bot.send_text") as mock_send:
        bot.process_update(_make_callback("123", f"approve:{code}"))
        text = mock_send.call_args[0][1]
        assert code in text
        assert "批准" in text


@pytest.mark.parametrize("code", ["2330", "2317", "0050", "2454", "3008", "9999"])
def test_reject_any_stock_replies_with_code(code, monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot._post"), \
         patch("telegram_bot.send_text") as mock_send:
        bot.process_update(_make_callback("123", f"reject:{code}"))
        text = mock_send.call_args[0][1]
        assert code in text
        assert "拒絕" in text


def test_approve_all_callback(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot._post"), \
         patch("telegram_bot.send_text") as mock_send:
        bot.process_update(_make_callback("123", "approve_all"))
        text = mock_send.call_args[0][1]
        assert "全部批准" in text or "全部" in text


def test_reject_all_callback(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot._post"), \
         patch("telegram_bot.send_text") as mock_send:
        bot.process_update(_make_callback("123", "reject_all"))
        text = mock_send.call_args[0][1]
        assert "拒絕" in text


def test_approve_reject_unauthorized_blocked(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "999")
    with patch("telegram_bot._post"), \
         patch("telegram_bot.send_text") as mock_send:
        bot.process_update(_make_callback("888", "approve:2330"))
        mock_send.assert_not_called()
        bot.process_update(_make_callback("888", "reject_all"))
        mock_send.assert_not_called()


# ── handle_plan ───────────────────────────────────────────────────────────────

def test_handle_plan_no_picks_shows_notice(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.load_daily_plan", return_value=[]), \
         patch("telegram_bot.send_text") as mock_send:
        bot.handle_plan("123")
        assert "尚無" in mock_send.call_args[0][1]


def test_handle_plan_shows_picks(monkeypatch):
    picks = [{"code": "2330", "name": "台積電", "confidence": 8,
               "budget": 5000, "target_price": 1000, "stop_loss_price": 900, "sector": "半導體"}]
    with patch("telegram_bot.load_daily_plan", return_value=picks), \
         patch("telegram_bot.send_text") as mock_send:
        bot.handle_plan("123")
        text = mock_send.call_args[0][1]
        assert "2330" in text
        assert "台積電" in text


# ── handle_holdings ───────────────────────────────────────────────────────────

def test_handle_holdings_no_trades(monkeypatch):
    with patch("telegram_bot.load_daily_trades", return_value=[]), \
         patch("telegram_bot.send_text") as mock_send:
        bot.handle_holdings("123")
        assert "尚無" in mock_send.call_args[0][1]


def test_handle_holdings_shows_buy_trades(monkeypatch):
    trades = [{"code": "2330", "name": "台積電", "action": "buy",
               "quantity": 1000, "price": 975.0, "pnl": 500}]
    with patch("telegram_bot.load_daily_trades", return_value=trades), \
         patch("telegram_bot.send_text") as mock_send:
        bot.handle_holdings("123")
        assert "2330" in mock_send.call_args[0][1]


# ── handle_status ─────────────────────────────────────────────────────────────

def test_handle_status_shows_date():
    with patch("telegram_bot.load_daily_trades", return_value=[]), \
         patch("telegram_bot.send_text") as mock_send:
        bot.handle_status("123")
        text = mock_send.call_args[0][1]
        assert "今日狀態" in text


def test_handle_status_sums_pnl():
    trades = [
        {"action": "buy", "amount": 10000, "pnl": 200},
        {"action": "buy", "amount": 5000,  "pnl": -100},
    ]
    with patch("telegram_bot.load_daily_trades", return_value=trades), \
         patch("telegram_bot.send_text") as mock_send:
        bot.handle_status("123")
        text = mock_send.call_args[0][1]
        assert "100" in text  # net pnl = 100


# ── send_main_menu ────────────────────────────────────────────────────────────

def test_send_main_menu_includes_all_buttons():
    with patch("telegram_bot._post") as mock_post:
        bot.send_main_menu("123")
        payload = mock_post.call_args[0][1]
        markup = payload["reply_markup"]
        assert "📊 今日狀態" in markup
        assert "💼 持倉" in markup
        assert "📈 選股計劃" in markup
        assert "⚡ 快速下單" in markup
        assert "🛡️ 停損設定" in markup
        assert "❓ 說明" in markup
