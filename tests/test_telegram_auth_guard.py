from __future__ import annotations

"""
tests/test_telegram_auth_guard.py — _is_authorized 單元測試 + 未授權 callback 攔截

測試 telegram_bot.py 的 _is_authorized() 函式本身（直接呼叫，非透過 process_update），
以及 process_update() 對未授權 callback_query 的攔截。

test_telegram_bot.py 已透過 process_update() 驗證基本路由，本文件補充：
  (a) 錯誤 chat_id 被 _is_authorized 直接拒絕（回傳 False）
  (b) 正確 chat_id 被 _is_authorized 直接接受（回傳 True）
  (c) TELEGRAM_USER_ID 設定後，chat_id 正確但 user_id 錯誤仍被拒絕
  (d) callback_query 路徑在未授權時不觸發 handle_callback

重要：telegram_bot 在 import 時依賴 google.genai，在測試環境中需先 stub
（conftest.py 的 _stub_google_genai() 已注入 sys.modules，
 但 conftest.py 的 autouse fixture _isolate_planset 也依賴 telegram_bot 已被 import，
 所以此處直接 import telegram_bot 即可安全使用）。
"""

from unittest.mock import MagicMock, patch

import pytest

import telegram_bot as bot


# ── 輔助：建立測試 update 字典 ────────────────────────────────────────────────

def _msg_update(chat_id: str, user_id: str | None = None) -> dict:
    """建立包含 message 的 update，可選填 from.id（sender user_id）。"""
    msg: dict = {"chat": {"id": chat_id}, "text": "test"}
    if user_id is not None:
        msg["from"] = {"id": user_id}
    return {"update_id": 1, "message": msg}


def _callback_update(chat_id: str, user_id: str | None = None,
                     data: str = "approve_all") -> dict:
    """建立包含 callback_query 的 update，可選填 from.id。"""
    cq: dict = {
        "id": "cq_test",
        "data": data,
        "message": {"chat": {"id": chat_id}},
    }
    if user_id is not None:
        cq["from"] = {"id": user_id}
    return {"update_id": 1, "callback_query": cq}


# ── (a) 錯誤 chat_id → _is_authorized 回傳 False ────────────────────────────


class TestIsAuthorizedWrongChatId:
    """_is_authorized() 在 chat_id 不符時必須直接回傳 False。"""

    def test_wrong_chat_id_returns_false_for_message(self, monkeypatch):
        """訊息的 chat.id 與 CHAT_ID 不符 → False。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        assert bot._is_authorized(_msg_update("888")) is False

    def test_wrong_chat_id_returns_false_for_callback(self, monkeypatch):
        """callback_query 的 chat.id 與 CHAT_ID 不符 → False。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        assert bot._is_authorized(_callback_update("888")) is False

    def test_empty_chat_id_in_update_returns_false(self, monkeypatch):
        """update 中 chat.id 為空字串 → False。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        update = {"update_id": 1, "message": {"chat": {"id": ""}, "text": "hi"}}
        assert bot._is_authorized(update) is False

    def test_numeric_vs_string_chat_id_mismatch(self, monkeypatch):
        """chat.id 以 int 傳入但 CHAT_ID 以 str 設定，不相符 → False。

        驗證 str() 轉換行為：str(888) != "999"。
        """
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        # Telegram update 的 chat.id 通常為 int
        update = {"update_id": 1, "message": {"chat": {"id": 888}, "text": "hi"}}
        assert bot._is_authorized(update) is False


# ── (b) 正確 chat_id → _is_authorized 回傳 True ─────────────────────────────


class TestIsAuthorizedCorrectChatId:
    """_is_authorized() 在 chat_id 相符（且未設定 USER_ID）時回傳 True。"""

    def test_correct_chat_id_returns_true_for_message(self, monkeypatch):
        """chat.id 相符，USER_ID 未設定 → True。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        assert bot._is_authorized(_msg_update("999")) is True

    def test_correct_chat_id_returns_true_for_callback(self, monkeypatch):
        """callback_query 的 chat.id 相符，USER_ID 未設定 → True。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        assert bot._is_authorized(_callback_update("999")) is True

    def test_correct_chat_id_as_int_returns_true(self, monkeypatch):
        """chat.id 以 int 傳入，轉成 str 後與 CHAT_ID 相符 → True。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        update = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "hi"}}
        assert bot._is_authorized(update) is True

    def test_user_id_not_set_ignores_from_field(self, monkeypatch):
        """USER_ID 未設定時，message.from 欄位的任意值不影響結果。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        assert bot._is_authorized(_msg_update("999", user_id="12345")) is True


# ── (c) TELEGRAM_USER_ID 設定後，user_id 不符仍被拒絕 ────────────────────────


class TestIsAuthorizedUserIdDualCheck:
    """USER_ID 設定後，chat_id 正確但 from.id 不符 → _is_authorized 回傳 False。"""

    def test_correct_chat_wrong_user_returns_false_for_message(self, monkeypatch):
        """chat_id 正確、user_id 錯誤 → False（雙重驗證失敗）。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        assert bot._is_authorized(_msg_update("111", user_id="999")) is False

    def test_correct_chat_correct_user_returns_true(self, monkeypatch):
        """chat_id 正確、user_id 也正確 → True。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        assert bot._is_authorized(_msg_update("111", user_id="42")) is True

    def test_correct_chat_wrong_user_callback_returns_false(self, monkeypatch):
        """callback_query 路徑：chat_id 正確、callback from.id 錯誤 → False。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        assert bot._is_authorized(_callback_update("111", user_id="999")) is False

    def test_correct_chat_correct_user_callback_returns_true(self, monkeypatch):
        """callback_query 路徑：chat_id + user_id 都正確 → True。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        assert bot._is_authorized(_callback_update("111", user_id="42")) is True

    def test_wrong_chat_correct_user_returns_false(self, monkeypatch):
        """chat_id 錯誤即使 user_id 正確也應被拒絕（chat_id 先驗）。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        assert bot._is_authorized(_msg_update("888", user_id="42")) is False

    def test_missing_from_field_with_user_id_configured_returns_false(
        self, monkeypatch
    ):
        """USER_ID 已設定，但 update 中沒有 from 欄位 → user_id 比較失敗 → False。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        # from 欄位缺失（sender.get("id", "") → "" != "42"）
        update = {"update_id": 1, "message": {"chat": {"id": "111"}, "text": "hi"}}
        assert bot._is_authorized(update) is False

    def test_user_id_as_int_in_from_field(self, monkeypatch):
        """from.id 以 int 傳入時，str() 轉換後應正確與 USER_ID 比較。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": "111"},
                "from": {"id": 42},  # int，非 str
                "text": "hi",
            },
        }
        assert bot._is_authorized(update) is True


# ── (d) 未授權 callback_query → handle_callback 不被呼叫 ─────────────────────


class TestUnauthorizedCallbackNotRouted:
    """
    process_update() 的 _is_authorized 攔截在 callback_query 分支之前：
    未授權的 callback_query 不應觸發 handle_callback。
    """

    def test_wrong_chat_id_callback_does_not_call_handle_callback(
        self, monkeypatch
    ):
        """chat_id 錯誤的 callback_query → handle_callback 不被呼叫。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        with patch("telegram_bot.handle_callback") as mock_cb:
            bot.process_update(_callback_update("888"))
            mock_cb.assert_not_called()

    def test_wrong_user_id_callback_does_not_call_handle_callback(
        self, monkeypatch
    ):
        """USER_ID 設定，chat_id 正確但 user_id 錯誤 → handle_callback 不被呼叫。"""
        monkeypatch.setattr(bot, "CHAT_ID", "111")
        monkeypatch.setattr(bot, "USER_ID", "42")
        with patch("telegram_bot.handle_callback") as mock_cb:
            bot.process_update(_callback_update("111", user_id="999"))
            mock_cb.assert_not_called()

    def test_wrong_chat_id_callback_does_not_call_any_send_text(
        self, monkeypatch
    ):
        """未授權的 callback_query → send_text 完全不被呼叫（無任何回應）。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        with patch("telegram_bot.send_text") as mock_send:
            bot.process_update(_callback_update("888", data="approve_all"))
            mock_send.assert_not_called()

    def test_authorized_callback_does_call_handle_callback(self, monkeypatch):
        """對照組：授權通過時 handle_callback 應被呼叫。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        with patch("telegram_bot.handle_callback") as mock_cb:
            bot.process_update(_callback_update("999"))
            mock_cb.assert_called_once()

    def test_authorized_callback_handle_callback_receives_callback_query_dict(
        self, monkeypatch
    ):
        """handle_callback は callback_query dict を受け取る
        （process_update は update["callback_query"] を渡す）。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        cq_dict = {
            "id": "cq_test",
            "data": "approve_all",
            "from": {"id": "999"},
            "message": {"chat": {"id": "999"}},
        }
        update = {"update_id": 1, "callback_query": cq_dict}
        with patch("telegram_bot.handle_callback") as mock_cb:
            bot.process_update(update)
            mock_cb.assert_called_once_with(cq_dict)
