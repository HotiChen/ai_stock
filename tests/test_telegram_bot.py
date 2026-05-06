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
    ("🚨 緊急暫停",  "handle_emergency_halt"),
    ("🔄 撤銷所有委託", "handle_cancel_all"),
    ("💥 一鍵全平倉", "handle_liquidate_all"),
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


def test_callback_liquidate_all_confirm(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_text") as mock_send, \
         patch("monitor_agent.ensure_connected") as mock_conn, \
         patch("halt.liquidate_all_positions") as mock_liq:
        
        mock_api = MagicMock()
        mock_conn.return_value = mock_api
        mock_liq.return_value = {"liquidated": [{"code": "2330", "quantity": 1}], "failed": []}
        
        bot.process_update(_make_callback("123", "liquidate_all_confirm"))
        
        mock_liq.assert_called_once_with(mock_api)
        text = mock_send.call_args_list[-1][0][1]
        assert "全平倉完成" in text
        assert "1" in text

def test_callback_liquidate_all_abort(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_text") as mock_send, \
         patch("telegram_bot.send_main_menu"):
        bot.process_update(_make_callback("123", "liquidate_all_abort"))
        text = mock_send.call_args[0][1]
        assert "取消" in text


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
         patch("telegram_bot.reject_pick_from_plan"), \
         patch("telegram_bot.send_text") as mock_send:
        bot.process_update(_make_callback("123", f"reject:{code}"))
        text = mock_send.call_args[0][1]
        assert code in text
        assert "移除" in text or "拒絕" in text


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


# ── approve/reject persistence (#fix2) ───────────────────────────────────────

def test_reject_code_callback_updates_db(monkeypatch, tmp_path):
    from research_db import init_db, save_daily_plan, load_daily_plan
    from datetime import date
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    save_daily_plan(date.today(), [
        {"code": "2330", "name": "台積電"}, {"code": "2454", "name": "聯發科"}
    ], db_path)
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    monkeypatch.setattr(bot, "DB_PATH", db_path)
    with patch("telegram_bot._post"), patch("telegram_bot.send_text"):
        bot.process_update(_make_callback("123", "reject:2330"))
    remaining = load_daily_plan(date.today(), db_path)
    codes = [p["code"] for p in remaining]
    assert "2330" not in codes
    assert "2454" in codes


def test_reject_all_callback_clears_db(monkeypatch, tmp_path):
    from research_db import init_db, save_daily_plan, load_daily_plan
    from datetime import date
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    save_daily_plan(date.today(), [
        {"code": "2330", "name": "台積電"}, {"code": "2454", "name": "聯發科"}
    ], db_path)
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    monkeypatch.setattr(bot, "DB_PATH", db_path)
    with patch("telegram_bot._post"), patch("telegram_bot.send_text"):
        bot.process_update(_make_callback("123", "reject_all"))
    remaining = load_daily_plan(date.today(), db_path)
    assert remaining == []


def test_approve_code_callback_does_not_modify_db(monkeypatch, tmp_path):
    from research_db import init_db, save_daily_plan, load_daily_plan
    from datetime import date
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    save_daily_plan(date.today(), [{"code": "2330", "name": "台積電"}], db_path)
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    monkeypatch.setattr(bot, "DB_PATH", db_path)
    with patch("telegram_bot._post"), patch("telegram_bot.send_text"):
        bot.process_update(_make_callback("123", "approve:2330"))
    remaining = load_daily_plan(date.today(), db_path)
    assert len(remaining) == 1


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


# ── 停損 command parsing (#8) ─────────────────────────────────────────────────

def test_stop_loss_command_valid_replies_with_code_and_price(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_text") as mock_send, \
         patch("telegram_bot.send_main_menu") as mock_menu:
        bot.process_update(_make_update("123", "停損 2330 540"))
        mock_menu.assert_not_called()  # should NOT fall through to unknown-command menu
        text = mock_send.call_args[0][1]
        assert "2330" in text
        assert "540" in text
        assert "已設定" in text or "停損" in text


def test_stop_loss_command_missing_price_shows_format_hint(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_text") as mock_send, \
         patch("telegram_bot.send_main_menu") as mock_menu:
        bot.process_update(_make_update("123", "停損 2330"))
        mock_menu.assert_not_called()
        text = mock_send.call_args[0][1]
        assert "格式" in text


def test_stop_loss_command_non_numeric_price_shows_format_hint(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_text") as mock_send, \
         patch("telegram_bot.send_main_menu") as mock_menu:
        bot.process_update(_make_update("123", "停損 2330 abc"))
        mock_menu.assert_not_called()
        text = mock_send.call_args[0][1]
        assert "格式" in text


def test_stop_loss_command_decimal_price(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    with patch("telegram_bot.send_text") as mock_send, \
         patch("telegram_bot.send_main_menu") as mock_menu:
        bot.process_update(_make_update("123", "停損 0050 145.5"))
        mock_menu.assert_not_called()
        text = mock_send.call_args[0][1]
        assert "0050" in text
        assert "145.5" in text


# ── handle_status ─────────────────────────────────────────────────────────────

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
        # markup must be a dict (not a pre-serialized string) so Telegram
        # receives a proper JSON object inside the JSON body
        assert isinstance(markup, dict), "reply_markup must be a dict, not a JSON string"
        flat = str(markup)
        assert "📊 今日狀態" in flat
        assert "💼 持倉" in flat
        assert "📈 選股計劃" in flat
        assert "⚡ 快速下單" in flat
        assert "🛡️ 停損設定" in flat
        assert "❓ 說明" in flat
        assert "💥 一鍵全平倉" in flat


def test_send_text_reply_markup_passed_as_dict():
    """reply_markup must reach _post as a dict, not a pre-serialised string."""
    with patch("telegram_bot._post") as mock_post:
        keyboard = {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]}
        bot.send_text("123", "test", reply_markup=keyboard)
        payload = mock_post.call_args[0][1]
        assert isinstance(payload["reply_markup"], dict)


# ── H9: USER_ID 雙重驗證 ──────────────────────────────────────────────────────

class TestUserIdDualAuth:
    """_is_authorized() 在 USER_ID 設定後需同時驗證 chat_id 與 from_user.id。"""

    def _make_update_with_user(self, chat_id: str, user_id: str) -> dict:
        return {
            "update_id": 1,
            "message": {
                "chat": {"id": chat_id},
                "from": {"id": user_id},
                "text": "📊 今日狀態",
            },
        }

    def _make_callback_with_user(self, chat_id: str, user_id: str) -> dict:
        return {
            "update_id": 1,
            "callback_query": {
                "id": "cq1",
                "data": "approve:2330",
                "from": {"id": user_id},
                "message": {"chat": {"id": chat_id}},
            },
        }

    def test_user_id_not_set_only_checks_chat_id(self, monkeypatch):
        """USER_ID 未設定時，任何 user_id 都通過（只查 chat_id）。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "")
        with patch("telegram_bot.handle_status") as mock_h:
            bot.process_update(self._make_update_with_user("111", "999"))
            mock_h.assert_called_once()

    def test_correct_user_id_passes(self, monkeypatch):
        """CHAT_ID + USER_ID 都正確 → 通過。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        with patch("telegram_bot.handle_status") as mock_h:
            bot.process_update(self._make_update_with_user("111", "42"))
            mock_h.assert_called_once()

    def test_wrong_user_id_blocked(self, monkeypatch):
        """CHAT_ID 正確但 USER_ID 不符 → 拒絕。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        with patch("telegram_bot.handle_status") as mock_h, \
             patch("telegram_bot.send_text") as mock_send:
            bot.process_update(self._make_update_with_user("111", "999"))
            mock_h.assert_not_called()
            mock_send.assert_not_called()

    def test_callback_wrong_user_id_blocked(self, monkeypatch):
        """callback_query 發送者 user_id 不符 → 拒絕，send_text 不被呼叫。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        with patch("telegram_bot.send_text") as mock_send:
            bot.process_update(self._make_callback_with_user("111", "999"))
            mock_send.assert_not_called()

    def test_callback_correct_user_id_passes(self, monkeypatch):
        """callback_query + 正確 USER_ID → 通過路由，回覆確認訊息。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        with patch("telegram_bot.send_text") as mock_send:
            bot.process_update(self._make_callback_with_user("111", "42"))
            # approve:2330 callback 應回覆批准確認訊息
            mock_send.assert_called()


# ── H5: Stop-loss 持久化 ──────────────────────────────────────────────────────

class TestStopLossPersistence:
    """_save_stop_loss / _load_stop_losses 的 JSON 讀寫行為。"""

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        """寫入後可以讀回相同數值。"""
        sl_path = tmp_path / "stop_loss.json"
        monkeypatch.setattr(bot, "_STOP_LOSS_PATH", sl_path)

        bot._save_stop_loss("2330", 540.0)
        result = bot._load_stop_losses()

        assert result["2330"] == pytest.approx(540.0)

    def test_save_multiple_stocks(self, tmp_path, monkeypatch):
        """多支股票各自儲存，不互相覆蓋。"""
        sl_path = tmp_path / "stop_loss.json"
        monkeypatch.setattr(bot, "_STOP_LOSS_PATH", sl_path)

        bot._save_stop_loss("2330", 540.0)
        bot._save_stop_loss("2454", 800.0)
        bot._save_stop_loss("6505", 95.5)

        result = bot._load_stop_losses()
        assert result["2330"] == pytest.approx(540.0)
        assert result["2454"] == pytest.approx(800.0)
        assert result["6505"] == pytest.approx(95.5)

    def test_overwrite_existing_entry(self, tmp_path, monkeypatch):
        """同一股票再次儲存會覆蓋舊值。"""
        sl_path = tmp_path / "stop_loss.json"
        monkeypatch.setattr(bot, "_STOP_LOSS_PATH", sl_path)

        bot._save_stop_loss("2330", 540.0)
        bot._save_stop_loss("2330", 520.0)

        result = bot._load_stop_losses()
        assert result["2330"] == pytest.approx(520.0)

    def test_load_returns_empty_if_file_missing(self, tmp_path, monkeypatch):
        """檔案不存在時回傳空 dict，不拋例外。"""
        sl_path = tmp_path / "nonexistent.json"
        monkeypatch.setattr(bot, "_STOP_LOSS_PATH", sl_path)

        result = bot._load_stop_losses()
        assert result == {}

    def test_save_uses_atomic_write(self, tmp_path, monkeypatch):
        """_save_stop_loss 透過 atomic_write_json 寫入（不遺留 .tmp 檔）。"""
        import os
        sl_path = tmp_path / "stop_loss.json"
        monkeypatch.setattr(bot, "_STOP_LOSS_PATH", sl_path)

        bot._save_stop_loss("2330", 540.0)

        # .tmp 不應殘留
        assert not (tmp_path / "stop_loss.json.tmp").exists()
        # 正式檔案存在
        assert sl_path.exists()
