from __future__ import annotations

"""
TDD tests for user_confirm.py

Covers:
- send_confirmation(picks, chat_id) → sends Telegram inline keyboard message
- handle_callback(callback_query) → parses approval/rejection from callback data
- verify_user(user_id) → True only for authorized user
- format_picks_message(picks) → human-readable summary for Telegram
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ── verify_user ───────────────────────────────────────────────────────────────

class TestVerifyUser:
    def test_authorized_user_returns_true(self):
        from user_confirm import verify_user
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            assert verify_user("12345") is True

    def test_unauthorized_user_returns_false(self):
        from user_confirm import verify_user
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            assert verify_user("99999") is False

    def test_empty_user_id_returns_false(self):
        from user_confirm import verify_user
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            assert verify_user("") is False

    def test_int_user_id_as_string_matches(self):
        from user_confirm import verify_user
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            assert verify_user(12345) is True

    def test_none_user_id_returns_false(self):
        from user_confirm import verify_user
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            assert verify_user(None) is False


# ── format_picks_message ──────────────────────────────────────────────────────

class TestFormatPicksMessage:
    def _make_picks(self):
        return [
            {
                "code": "2330", "name": "台積電", "budget": 5000.0,
                "sector": "半導體", "signal": "buy", "confidence": 8,
            },
            {
                "code": "2454", "name": "聯發科", "budget": 3000.0,
                "sector": "半導體", "signal": "buy", "confidence": 7,
            },
        ]

    def test_contains_stock_code(self):
        from user_confirm import format_picks_message
        text = format_picks_message(self._make_picks())
        assert "2330" in text

    def test_contains_stock_name(self):
        from user_confirm import format_picks_message
        text = format_picks_message(self._make_picks())
        assert "台積電" in text

    def test_contains_budget(self):
        from user_confirm import format_picks_message
        text = format_picks_message(self._make_picks())
        assert "5000" in text or "5,000" in text

    def test_contains_confidence(self):
        from user_confirm import format_picks_message
        text = format_picks_message(self._make_picks())
        assert "8" in text

    def test_returns_nonempty_string(self):
        from user_confirm import format_picks_message
        text = format_picks_message(self._make_picks())
        assert isinstance(text, str) and len(text) > 10

    def test_empty_picks_returns_string(self):
        from user_confirm import format_picks_message
        text = format_picks_message([])
        assert isinstance(text, str)

    def test_multiple_picks_all_listed(self):
        from user_confirm import format_picks_message
        text = format_picks_message(self._make_picks())
        assert "2330" in text
        assert "2454" in text


# ── send_confirmation ─────────────────────────────────────────────────────────

class TestSendConfirmation:
    def _make_picks(self):
        return [
            {"code": "2330", "name": "台積電", "budget": 5000.0,
             "sector": "半導體", "signal": "buy", "confidence": 8},
        ]

    @patch("user_confirm.requests.post")
    def test_calls_telegram_send_message_api(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
        from user_confirm import send_confirmation
        send_confirmation(self._make_picks(), chat_id="12345")
        assert mock_post.called

    @patch("user_confirm.requests.post")
    def test_sends_to_correct_chat_id(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
        from user_confirm import send_confirmation
        send_confirmation(self._make_picks(), chat_id="12345")
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
        if not body:
            body = call_kwargs.kwargs.get("data", {})
        # chat_id should appear somewhere in the call
        assert "12345" in str(call_kwargs)

    @patch("user_confirm.requests.post")
    def test_includes_inline_keyboard_in_payload(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
        from user_confirm import send_confirmation
        send_confirmation(self._make_picks(), chat_id="12345")
        call_str = str(mock_post.call_args)
        assert "reply_markup" in call_str or "inline_keyboard" in call_str

    @patch("user_confirm.requests.post")
    def test_api_failure_does_not_raise(self, mock_post):
        mock_post.side_effect = Exception("network error")
        from user_confirm import send_confirmation
        send_confirmation(self._make_picks(), chat_id="12345")  # should not raise

    @patch("user_confirm.requests.post")
    def test_returns_message_id_on_success(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"ok": True, "result": {"message_id": 42}}
        from user_confirm import send_confirmation
        result = send_confirmation(self._make_picks(), chat_id="12345")
        assert result == 42

    @patch("user_confirm.requests.post")
    def test_returns_none_on_failure(self, mock_post):
        mock_post.side_effect = Exception("network error")
        from user_confirm import send_confirmation
        result = send_confirmation(self._make_picks(), chat_id="12345")
        assert result is None


# ── handle_callback ───────────────────────────────────────────────────────────

class TestHandleCallback:
    def _make_callback(self, data: str, user_id: str = "12345") -> dict:
        return {
            "id": "abc123",
            "from": {"id": user_id, "first_name": "Tim"},
            "message": {"message_id": 1, "chat": {"id": user_id}},
            "data": data,
        }

    def test_approve_all_returns_approved_action(self):
        from user_confirm import handle_callback
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            result = handle_callback(self._make_callback("approve_all"))
        assert result["action"] == "approve_all"

    def test_reject_all_returns_rejected_action(self):
        from user_confirm import handle_callback
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            result = handle_callback(self._make_callback("reject_all"))
        assert result["action"] == "reject_all"

    def test_unauthorized_user_returns_none(self):
        from user_confirm import handle_callback
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            result = handle_callback(self._make_callback("approve_all", user_id="99999"))
        assert result is None

    def test_approve_single_stock_parsed(self):
        from user_confirm import handle_callback
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            result = handle_callback(self._make_callback("approve:2330"))
        assert result["action"] == "approve"
        assert result["code"] == "2330"

    def test_reject_single_stock_parsed(self):
        from user_confirm import handle_callback
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            result = handle_callback(self._make_callback("reject:2330"))
        assert result["action"] == "reject"
        assert result["code"] == "2330"

    def test_unknown_callback_data_returns_none(self):
        from user_confirm import handle_callback
        with patch("user_confirm.AUTHORIZED_CHAT_ID", "12345"):
            result = handle_callback(self._make_callback("unknown_action"))
        assert result is None
