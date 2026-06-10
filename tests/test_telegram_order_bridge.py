from __future__ import annotations

"""
tests/test_telegram_order_bridge.py — telegram_bot ↔ backend 下單橋接測試

驗證 telegram_bot._notify_backend_order() 以及 handle_callback 的
approve/reject 分支正確呼叫橋接：

  (a) _notify_backend_order 以正確的 URL / X-Internal-Token header / body 送 POST
  (b) 2xx 回傳 JSON body
  (c) 404 / timeout / 連線錯誤 / 未設定 token → 回傳 None 且不 raise
  (d) handle_callback 的 approve:{code} 分支以 (code, True) 呼叫橋接
  (e) handle_callback 的 reject:{code} 分支以 (code, False) 呼叫橋接，
      且仍呼叫既有的 reject_pick_from_plan
  (f) 未授權 callback 永遠不會走到橋接（process_update 在授權前攔截）

重要：telegram_bot 在 import 時依賴 google.genai，conftest.py 已注入 stub，
此處比照 test_telegram_auth_guard.py 直接 import telegram_bot 即可。
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

import telegram_bot as bot


# ── (a)(b) _notify_backend_order：正確 URL/header/body，2xx 回傳 JSON ──────────


class TestNotifyBackendOrderRequest:
    def test_posts_correct_url_header_body(self, monkeypatch):
        """以正確的 URL、X-Internal-Token header、{code, confirmed} body 送出。"""
        monkeypatch.setattr(bot, "BACKEND_URL", "http://127.0.0.1:8000")
        monkeypatch.setattr(bot, "BACKEND_INTERNAL_TOKEN", "tok-abc")

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"status": "filled", "order_id": "ord-1"}

        with patch("telegram_bot.requests.post", return_value=fake_resp) as mock_post:
            result = bot._notify_backend_order("2330", True)

        assert result == {"status": "filled", "order_id": "ord-1"}
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        url = args[0] if args else kwargs.get("url")
        assert url == "http://127.0.0.1:8000/api/order/internal/confirm-by-code"
        assert kwargs["headers"]["X-Internal-Token"] == "tok-abc"
        assert kwargs["json"] == {"code": "2330", "confirmed": True}
        assert kwargs["timeout"] == 5

    def test_reject_sends_confirmed_false(self, monkeypatch):
        """confirmed=False 時 body 帶 confirmed: False。"""
        monkeypatch.setattr(bot, "BACKEND_URL", "http://127.0.0.1:8000")
        monkeypatch.setattr(bot, "BACKEND_INTERNAL_TOKEN", "tok-abc")

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"status": "rejected"}

        with patch("telegram_bot.requests.post", return_value=fake_resp) as mock_post:
            result = bot._notify_backend_order("2454", False)

        assert result == {"status": "rejected"}
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {"code": "2454", "confirmed": False}


# ── (c) None on 404 / timeout / connection error / unset token ──────────────


class TestNotifyBackendOrderNone:
    def test_unset_token_returns_none_without_request(self, monkeypatch):
        """BACKEND_INTERNAL_TOKEN 未設定 → 回傳 None 且完全不發出請求。"""
        monkeypatch.setattr(bot, "BACKEND_URL", "http://127.0.0.1:8000")
        monkeypatch.setattr(bot, "BACKEND_INTERNAL_TOKEN", "")

        with patch("telegram_bot.requests.post") as mock_post:
            result = bot._notify_backend_order("2330", True)

        assert result is None
        mock_post.assert_not_called()

    def test_404_returns_none(self, monkeypatch):
        """後端回 404（無對應 pending order）→ 回傳 None，不 raise。"""
        monkeypatch.setattr(bot, "BACKEND_URL", "http://127.0.0.1:8000")
        monkeypatch.setattr(bot, "BACKEND_INTERNAL_TOKEN", "tok-abc")

        fake_resp = MagicMock()
        fake_resp.status_code = 404
        fake_resp.json.return_value = {"detail": "not found"}

        with patch("telegram_bot.requests.post", return_value=fake_resp):
            result = bot._notify_backend_order("2330", True)

        assert result is None

    def test_timeout_returns_none(self, monkeypatch):
        """requests 逾時 → 回傳 None，不 raise。"""
        monkeypatch.setattr(bot, "BACKEND_URL", "http://127.0.0.1:8000")
        monkeypatch.setattr(bot, "BACKEND_INTERNAL_TOKEN", "tok-abc")

        with patch("telegram_bot.requests.post",
                   side_effect=requests.exceptions.Timeout("timed out")):
            result = bot._notify_backend_order("2330", True)

        assert result is None

    def test_connection_error_returns_none(self, monkeypatch):
        """連線錯誤 → 回傳 None，不 raise。"""
        monkeypatch.setattr(bot, "BACKEND_URL", "http://127.0.0.1:8000")
        monkeypatch.setattr(bot, "BACKEND_INTERNAL_TOKEN", "tok-abc")

        with patch("telegram_bot.requests.post",
                   side_effect=requests.exceptions.ConnectionError("refused")):
            result = bot._notify_backend_order("2330", True)

        assert result is None


# ── (d)(e) handle_callback approve / reject 走橋接 ───────────────────────────


def _cq(data: str, chat_id: str = "999") -> dict:
    return {"id": "cq1", "data": data, "message": {"chat": {"id": chat_id}}}


class TestHandleCallbackBridge:
    def test_approve_calls_bridge_with_true(self, monkeypatch):
        """approve:{code} 分支以 (code, True) 呼叫 _notify_backend_order。"""
        monkeypatch.setattr(bot, "_post", lambda *a, **k: {})
        monkeypatch.setattr(bot, "send_text", lambda *a, **k: None)
        with patch("telegram_bot._notify_backend_order",
                   return_value={"status": "filled"}) as mock_bridge:
            bot.handle_callback(_cq("approve:2330"))
        mock_bridge.assert_called_once_with("2330", True)

    def test_reject_calls_bridge_with_false_and_keeps_reject_pick(self, monkeypatch):
        """reject:{code} 分支以 (code, False) 呼叫橋接，且仍呼叫 reject_pick_from_plan。"""
        monkeypatch.setattr(bot, "_post", lambda *a, **k: {})
        monkeypatch.setattr(bot, "send_text", lambda *a, **k: None)
        with patch("telegram_bot.reject_pick_from_plan") as mock_reject, \
                patch("telegram_bot._notify_backend_order",
                      return_value={"status": "rejected"}) as mock_bridge:
            bot.handle_callback(_cq("reject:2454"))
        mock_bridge.assert_called_once_with("2454", False)
        mock_reject.assert_called_once()

    def test_approve_bridge_none_keeps_existing_reply(self, monkeypatch):
        """橋接回 None 時 handle_callback 不 raise，仍送出既有回覆。"""
        monkeypatch.setattr(bot, "_post", lambda *a, **k: {})
        sent: list = []
        monkeypatch.setattr(bot, "send_text", lambda chat_id, text, *a, **k: sent.append(text))
        with patch("telegram_bot._notify_backend_order", return_value=None):
            bot.handle_callback(_cq("approve:2330"))
        assert sent  # 仍有送出訊息


# ── (f) 未授權 callback 不會走到橋接 ─────────────────────────────────────────


class TestUnauthorizedNeverReachesBridge:
    def test_wrong_chat_callback_does_not_call_bridge(self, monkeypatch):
        """未授權 callback（chat_id 不符）→ _notify_backend_order 永不被呼叫。"""
        monkeypatch.setattr(bot, "CHAT_ID", "999")
        monkeypatch.setattr(bot, "USER_ID", "")
        update = {"update_id": 1, "callback_query": _cq("approve:2330", chat_id="888")}
        with patch("telegram_bot._notify_backend_order") as mock_bridge:
            bot.process_update(update)
        mock_bridge.assert_not_called()
