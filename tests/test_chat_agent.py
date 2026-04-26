from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chat_agent import (
    ChatMessage,
    build_trading_advisor_prompt,
    extract_chat_reply,
    format_messages_for_ollama,
    get_quick_prompts,
    call_ollama_chat,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_position(code="2330", name="台積電", cost=800.0, current=850.0, qty=1000):
    return {"stock_code": code, "name": name, "cost": cost, "current": current, "quantity": qty}


def make_ai_log(action="hold", reason="持平觀望", confidence=7):
    return {"decision": {"action": action, "reason": reason, "confidence": confidence}}


# ── ChatMessage ───────────────────────────────────────────────────────────────

class TestChatMessage:
    def test_has_required_fields(self):
        msg = ChatMessage(role="user", content="你好")
        assert msg.role == "user"
        assert msg.content == "你好"

    def test_timestamp_auto_set(self):
        msg = ChatMessage(role="user", content="test")
        assert msg.timestamp != ""
        assert len(msg.timestamp) >= 10

    def test_explicit_timestamp(self):
        msg = ChatMessage(role="assistant", content="回應", timestamp="2026-01-01T00:00:00")
        assert msg.timestamp == "2026-01-01T00:00:00"

    def test_system_role_allowed(self):
        msg = ChatMessage(role="system", content="你是顧問")
        assert msg.role == "system"

    def test_assistant_role_allowed(self):
        msg = ChatMessage(role="assistant", content="好的")
        assert msg.role == "assistant"


# ── build_trading_advisor_prompt ──────────────────────────────────────────────

class TestBuildTradingAdvisorPrompt:
    def test_returns_non_empty_string(self):
        result = build_trading_advisor_prompt()
        assert isinstance(result, str)
        assert len(result) > 100

    def test_contains_taiwan_stock_context(self):
        result = build_trading_advisor_prompt()
        assert "台股" in result or "台灣" in result

    def test_includes_position_info(self):
        positions = [make_position("2330", "台積電")]
        result = build_trading_advisor_prompt(positions=positions)
        assert "台積電" in result
        assert "2330" in result

    def test_no_positions_shows_placeholder(self):
        result = build_trading_advisor_prompt(positions=[])
        assert "無持倉" in result or "無" in result

    def test_includes_ai_log_action(self):
        ai_log = make_ai_log(action="buy", reason="RSI 低檔")
        result = build_trading_advisor_prompt(ai_log=ai_log)
        assert "buy" in result or "RSI" in result

    def test_no_ai_log_handled_gracefully(self):
        result = build_trading_advisor_prompt(ai_log=None)
        assert isinstance(result, str)

    def test_mentions_strategy_frameworks(self):
        result = build_trading_advisor_prompt()
        assert any(kw in result for kw in ["RSI", "均線", "MA", "停損", "巴菲特"])

    def test_multiple_positions(self):
        positions = [make_position("2330", "台積電"), make_position("2454", "聯發科")]
        result = build_trading_advisor_prompt(positions=positions)
        assert "台積電" in result
        assert "聯發科" in result


# ── format_messages_for_ollama ────────────────────────────────────────────────

class TestFormatMessagesForOllama:
    def test_returns_list_of_dicts(self):
        msgs = [ChatMessage(role="user", content="你好")]
        result = format_messages_for_ollama(msgs)
        assert isinstance(result, list)
        assert isinstance(result[0], dict)

    def test_preserves_role_and_content(self):
        msgs = [
            ChatMessage(role="user", content="問題"),
            ChatMessage(role="assistant", content="回答"),
        ]
        result = format_messages_for_ollama(msgs)
        assert result[0] == {"role": "user", "content": "問題"}
        assert result[1] == {"role": "assistant", "content": "回答"}

    def test_excludes_timestamp(self):
        msgs = [ChatMessage(role="user", content="test", timestamp="2026-01-01")]
        result = format_messages_for_ollama(msgs)
        assert "timestamp" not in result[0]

    def test_empty_messages_returns_empty(self):
        assert format_messages_for_ollama([]) == []

    def test_system_message_included(self):
        msgs = [ChatMessage(role="system", content="你是顧問")]
        result = format_messages_for_ollama(msgs)
        assert result[0]["role"] == "system"

    def test_preserves_order(self):
        msgs = [
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content="second"),
            ChatMessage(role="user", content="third"),
        ]
        result = format_messages_for_ollama(msgs)
        assert [r["content"] for r in result] == ["first", "second", "third"]


# ── extract_chat_reply ────────────────────────────────────────────────────────

class TestExtractChatReply:
    def test_extracts_content_from_valid_response(self):
        response = {"message": {"role": "assistant", "content": "這是回答"}}
        assert extract_chat_reply(response) == "這是回答"

    def test_returns_empty_on_missing_message_key(self):
        assert extract_chat_reply({}) == ""

    def test_returns_empty_on_missing_content_key(self):
        assert extract_chat_reply({"message": {"role": "assistant"}}) == ""

    def test_returns_empty_on_none_response(self):
        assert extract_chat_reply(None) == ""

    def test_handles_nested_none(self):
        assert extract_chat_reply({"message": None}) == ""

    def test_returns_full_content(self):
        long_text = "A" * 500
        response = {"message": {"content": long_text}}
        assert extract_chat_reply(response) == long_text


# ── get_quick_prompts ─────────────────────────────────────────────────────────

class TestGetQuickPrompts:
    def test_returns_list(self):
        result = get_quick_prompts()
        assert isinstance(result, list)

    def test_at_least_4_prompts(self):
        result = get_quick_prompts()
        assert len(result) >= 4

    def test_each_has_label_and_prompt(self):
        for item in get_quick_prompts():
            assert "label" in item
            assert "prompt" in item

    def test_labels_are_non_empty(self):
        for item in get_quick_prompts():
            assert len(item["label"]) > 0

    def test_prompts_are_non_empty(self):
        for item in get_quick_prompts():
            assert len(item["prompt"]) > 10

    def test_covers_key_topics(self):
        all_text = " ".join(p["prompt"] for p in get_quick_prompts())
        assert any(kw in all_text for kw in ["RSI", "停損", "均線", "選股", "策略"])


# ── call_ollama_chat ──────────────────────────────────────────────────────────

class TestCallOllamaChat:
    def test_posts_to_chat_endpoint(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"role": "assistant", "content": "好的"}}
        mock_response.raise_for_status = MagicMock()

        with patch("chat_agent.requests.post", return_value=mock_response) as mock_post:
            result = call_ollama_chat([{"role": "user", "content": "你好"}], model="test-model")

        call_url = mock_post.call_args[0][0]
        assert "/api/chat" in call_url

    def test_sends_messages_in_payload(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"content": "ok"}}
        mock_response.raise_for_status = MagicMock()

        messages = [{"role": "user", "content": "你好"}]
        with patch("chat_agent.requests.post", return_value=mock_response) as mock_post:
            call_ollama_chat(messages, model="test-model")

        payload = mock_post.call_args[1]["json"]
        assert payload["messages"] == messages

    def test_sets_stream_false(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"content": "ok"}}
        mock_response.raise_for_status = MagicMock()

        with patch("chat_agent.requests.post", return_value=mock_response) as mock_post:
            call_ollama_chat([{"role": "user", "content": "test"}], model="m")

        payload = mock_post.call_args[1]["json"]
        assert payload["stream"] is False

    def test_returns_response_json(self):
        expected = {"message": {"role": "assistant", "content": "回答"}}
        mock_response = MagicMock()
        mock_response.json.return_value = expected
        mock_response.raise_for_status = MagicMock()

        with patch("chat_agent.requests.post", return_value=mock_response):
            result = call_ollama_chat([{"role": "user", "content": "q"}], model="m")

        assert result == expected
