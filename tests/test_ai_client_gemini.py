from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from ai_client import call_gemini, call_gemini_with_search


# ── call_gemini ───────────────────────────────────────────────────────────────

class TestCallGemini:
    def _mock_client(self, text="學習報告內容"):
        mock_resp = MagicMock()
        mock_resp.text = text
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_resp
        return mock_client

    @patch("ai_client._get_gemini_client")
    def test_returns_string(self, mock_get):
        mock_get.return_value = self._mock_client("學習報告")
        result = call_gemini("分析過去7天策略")
        assert isinstance(result, str)
        assert result == "學習報告"

    @patch("ai_client._get_gemini_client")
    def test_uses_gemini_25_pro(self, mock_get):
        mock_get.return_value = self._mock_client()
        call_gemini("prompt")
        call_args = mock_get.return_value.models.generate_content.call_args
        assert "gemini-2.5-pro" in call_args.kwargs.get("model", call_args.args[0] if call_args.args else "")

    @patch("ai_client._get_gemini_client")
    def test_prompt_passed_as_contents(self, mock_get):
        mock_get.return_value = self._mock_client()
        call_gemini("my report prompt")
        call_args = mock_get.return_value.models.generate_content.call_args
        # contents should contain the prompt
        contents = call_args.kwargs.get("contents", "")
        assert "my report prompt" in str(contents)

    @patch("ai_client._get_gemini_client")
    def test_api_error_returns_empty_string(self, mock_get):
        mock_get.return_value.models.generate_content.side_effect = Exception("API error")
        result = call_gemini("prompt")
        assert result == ""

    @patch("ai_client._get_gemini_client")
    def test_custom_max_tokens_passed(self, mock_get):
        mock_get.return_value = self._mock_client()
        call_gemini("prompt", max_tokens=50_000)
        call_args = mock_get.return_value.models.generate_content.call_args
        # config should carry max_output_tokens
        config = call_args.kwargs.get("config")
        assert config is not None
        assert config.max_output_tokens == 50_000

    @patch("ai_client._get_gemini_client")
    def test_default_max_tokens_is_large(self, mock_get):
        """預設 max_tokens 要夠大才能支援長報告 (>= 8192)"""
        mock_get.return_value = self._mock_client()
        call_gemini("prompt")
        call_args = mock_get.return_value.models.generate_content.call_args
        config = call_args.kwargs.get("config")
        assert config.max_output_tokens >= 8192

    @patch("ai_client._get_gemini_client")
    def test_empty_response_text_returns_empty(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = ""
        mock_get.return_value.models.generate_content.return_value = mock_resp
        result = call_gemini("prompt")
        assert result == ""


# ── call_gemini_with_search ───────────────────────────────────────────────────

class TestCallGeminiWithSearch:
    def _mock_client(self, text="新聞分析結果"):
        mock_resp = MagicMock()
        mock_resp.text = text
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_resp
        return mock_client

    @patch("ai_client._get_gemini_client")
    def test_returns_string(self, mock_get):
        mock_get.return_value = self._mock_client("台積電新聞分析")
        result = call_gemini_with_search("台積電最新消息")
        assert isinstance(result, str)
        assert result == "台積電新聞分析"

    @patch("ai_client._get_gemini_client")
    def test_uses_gemini_25_flash(self, mock_get):
        mock_get.return_value = self._mock_client()
        call_gemini_with_search("prompt")
        call_args = mock_get.return_value.models.generate_content.call_args
        model = call_args.kwargs.get("model", call_args.args[0] if call_args.args else "")
        assert "gemini-2.5-flash" in model

    @patch("ai_client._get_gemini_client")
    def test_search_tool_enabled(self, mock_get):
        """config 應包含 google_search tool"""
        mock_get.return_value = self._mock_client()
        call_gemini_with_search("台積電今日新聞")
        call_args = mock_get.return_value.models.generate_content.call_args
        config = call_args.kwargs.get("config")
        assert config is not None
        # tools 中應有 google_search
        assert config.tools is not None
        assert len(config.tools) >= 1

    @patch("ai_client._get_gemini_client")
    def test_prompt_passed_as_contents(self, mock_get):
        mock_get.return_value = self._mock_client()
        call_gemini_with_search("search query")
        call_args = mock_get.return_value.models.generate_content.call_args
        contents = call_args.kwargs.get("contents", "")
        assert "search query" in str(contents)

    @patch("ai_client._get_gemini_client")
    def test_api_error_returns_empty_string(self, mock_get):
        mock_get.return_value.models.generate_content.side_effect = Exception("search error")
        result = call_gemini_with_search("prompt")
        assert result == ""

    @patch("ai_client._get_gemini_client")
    def test_none_response_text_returns_empty(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = None
        mock_get.return_value.models.generate_content.return_value = mock_resp
        result = call_gemini_with_search("prompt")
        assert result == ""
