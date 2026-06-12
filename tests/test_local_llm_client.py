"""
tests/test_local_llm_client.py — 本機 LLM 客戶端（local_llm_client）測試

涵蓋範圍：
  - is_enabled() 在 LOCAL_LLM_BASE_URL 未設定時回傳 False，且不發出 HTTP 請求
  - chat() 成功路徑：回傳 assistant 文字
  - chat() 失敗路徑（逾時、連線錯誤、non-2xx、格式錯誤）→ 回傳 None，不 raise
  - filter_stock_relevant_headlines() 快樂路徑：保留子集且維持原順序
  - filter_stock_relevant_headlines() 模型回傳格式錯誤 → 回傳原始 list
  - filter_stock_relevant_headlines() 模型會刪除全部 → 保護性回傳原始 list
  - news_agent hook：啟用時套用過濾器，停用時直接略過
  - news_agent hook：filter 拋出例外 → 回傳未過濾的 headlines
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest


# ════════════════════════════════════════════════════════════════════════════
# 輔助工具
# ════════════════════════════════════════════════════════════════════════════

def _reload_client():
    """重新載入模組，讓環境變數異動生效。"""
    if "local_llm_client" in sys.modules:
        del sys.modules["local_llm_client"]
    import local_llm_client
    return local_llm_client


def _make_headlines(n: int = 3) -> list[dict]:
    return [
        {"title": f"新聞標題{i}", "url": f"http://news/{i}",
         "published": "2026-06-12T09:00:00", "source": "鉅亨網"}
        for i in range(n)
    ]


def _mock_response(status_code: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock(
        side_effect=None if 200 <= status_code < 300
        else Exception(f"HTTP {status_code}")
    )
    resp.json.return_value = body or {}
    return resp


# ════════════════════════════════════════════════════════════════════════════
# 件1：is_enabled()
# ════════════════════════════════════════════════════════════════════════════

class TestIsEnabled:
    def test_disabled_when_env_unset(self, monkeypatch):
        """LOCAL_LLM_BASE_URL 未設定時 is_enabled() 回傳 False。"""
        monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
        client = _reload_client()
        assert client.is_enabled() is False

    def test_enabled_when_env_set(self, monkeypatch):
        """LOCAL_LLM_BASE_URL 有值時 is_enabled() 回傳 True。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        assert client.is_enabled() is True

    def test_disabled_no_http_call(self, monkeypatch):
        """停用時呼叫 chat() 不會發出任何 HTTP 請求。"""
        monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
        client = _reload_client()
        with patch("requests.post") as mock_post:
            result = client.chat("測試提示")
            mock_post.assert_not_called()
        assert result is None


# ════════════════════════════════════════════════════════════════════════════
# 件2：chat() — 成功路徑
# ════════════════════════════════════════════════════════════════════════════

class TestChatSuccess:
    def test_returns_assistant_text_on_200(self, monkeypatch):
        """2xx 回應且格式正確時，回傳 assistant content 字串。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        body = {
            "choices": [{"message": {"content": "這是回應"}}]
        }
        with patch("requests.post", return_value=_mock_response(200, body)):
            result = client.chat("你好")
        assert result == "這是回應"

    def test_sends_system_message_when_provided(self, monkeypatch):
        """有 system 參數時，請求 messages 包含 system role。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        body = {"choices": [{"message": {"content": "ok"}}]}
        with patch("requests.post", return_value=_mock_response(200, body)) as mock_post:
            client.chat("問題", system="你是台股分析師")
        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
        messages = payload["messages"]
        roles = [m["role"] for m in messages]
        assert "system" in roles

    def test_uses_configured_model(self, monkeypatch):
        """POST body 中 model 欄位使用 LOCAL_LLM_MODEL 環境變數。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LOCAL_LLM_MODEL", "my-model:7b")
        client = _reload_client()
        body = {"choices": [{"message": {"content": "ok"}}]}
        with patch("requests.post", return_value=_mock_response(200, body)) as mock_post:
            client.chat("問題")
        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
        assert payload["model"] == "my-model:7b"

    def test_default_model_is_gemma(self, monkeypatch):
        """未設定 LOCAL_LLM_MODEL 時，預設使用 gemma3:12b。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.delenv("LOCAL_LLM_MODEL", raising=False)
        client = _reload_client()
        body = {"choices": [{"message": {"content": "ok"}}]}
        with patch("requests.post", return_value=_mock_response(200, body)) as mock_post:
            client.chat("問題")
        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
        assert payload["model"] == "gemma3:12b"


# ════════════════════════════════════════════════════════════════════════════
# 件3：chat() — 失敗路徑 → 全部回傳 None，不 raise
# ════════════════════════════════════════════════════════════════════════════

class TestChatFailures:
    def setup_method(self, method):
        """每個測試前確保 LOCAL_LLM_BASE_URL 已設定（由各 test 用 monkeypatch 處理）。"""
        pass

    def test_timeout_returns_none(self, monkeypatch):
        """requests.Timeout 時回傳 None，不 raise。"""
        import requests as req_mod
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        with patch("requests.post", side_effect=req_mod.Timeout("逾時")):
            result = client.chat("問題")
        assert result is None

    def test_connection_error_returns_none(self, monkeypatch):
        """ConnectionError 時回傳 None，不 raise。"""
        import requests as req_mod
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        with patch("requests.post", side_effect=req_mod.ConnectionError("連線失敗")):
            result = client.chat("問題")
        assert result is None

    def test_non_2xx_returns_none(self, monkeypatch):
        """HTTP 500 時回傳 None，不 raise。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        with patch("requests.post", return_value=_mock_response(500)):
            result = client.chat("問題")
        assert result is None

    def test_malformed_json_returns_none(self, monkeypatch):
        """回應 body 格式錯誤（無 choices）時回傳 None，不 raise。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        resp = _mock_response(200, {"unexpected": "schema"})
        with patch("requests.post", return_value=resp):
            result = client.chat("問題")
        assert result is None

    def test_json_decode_error_returns_none(self, monkeypatch):
        """resp.json() 拋 JSONDecodeError 時回傳 None，不 raise。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("不合法 JSON")
        with patch("requests.post", return_value=resp):
            result = client.chat("問題")
        assert result is None

    def test_http_404_returns_none(self, monkeypatch):
        """HTTP 404 時回傳 None，不 raise。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        with patch("requests.post", return_value=_mock_response(404)):
            result = client.chat("問題")
        assert result is None


# ════════════════════════════════════════════════════════════════════════════
# 件4：filter_stock_relevant_headlines()
# ════════════════════════════════════════════════════════════════════════════

class TestFilterStockRelevantHeadlines:
    def test_disabled_returns_original_unchanged(self, monkeypatch):
        """停用時直接回傳原始 list，不呼叫 chat()。"""
        monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
        client = _reload_client()
        headlines = _make_headlines(3)
        with patch.object(client, "chat") as mock_chat:
            result = client.filter_stock_relevant_headlines(headlines)
            mock_chat.assert_not_called()
        assert result == headlines

    def test_empty_input_returns_empty(self, monkeypatch):
        """空 list 輸入直接回傳空 list，不呼叫 chat()。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        with patch.object(client, "chat", return_value="[]") as mock_chat:
            result = client.filter_stock_relevant_headlines([])
        assert result == []

    def test_happy_path_keeps_subset_in_order(self, monkeypatch):
        """模型回傳 [0, 2]，正確保留索引 0、2 的 headline，維持原順序。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        headlines = _make_headlines(3)
        with patch.object(client, "chat", return_value="[0, 2]"):
            result = client.filter_stock_relevant_headlines(headlines)
        assert len(result) == 2
        assert result[0] == headlines[0]
        assert result[1] == headlines[2]

    def test_all_kept_when_model_returns_all_indices(self, monkeypatch):
        """模型回傳全部索引 [0,1,2] 時，結果與原始 list 相同。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        headlines = _make_headlines(3)
        with patch.object(client, "chat", return_value="[0, 1, 2]"):
            result = client.filter_stock_relevant_headlines(headlines)
        assert result == headlines

    def test_malformed_reply_returns_original(self, monkeypatch):
        """模型回傳無法解析的文字 → fail-open，回傳原始 list。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        headlines = _make_headlines(3)
        with patch.object(client, "chat", return_value="我不知道要怎麼回答"):
            result = client.filter_stock_relevant_headlines(headlines)
        assert result == headlines

    def test_out_of_range_index_skipped(self, monkeypatch):
        """回傳索引超出範圍（如 [0, 99]），超出部分忽略，不 crash。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        headlines = _make_headlines(3)
        with patch.object(client, "chat", return_value="[0, 99]"):
            result = client.filter_stock_relevant_headlines(headlines)
        assert len(result) == 1
        assert result[0] == headlines[0]

    def test_all_dropped_guard_returns_original(self, monkeypatch):
        """模型回傳空陣列（會刪除全部），保護性回傳原始 list（fail-open）。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        headlines = _make_headlines(3)
        with patch.object(client, "chat", return_value="[]"):
            result = client.filter_stock_relevant_headlines(headlines)
        assert result == headlines

    def test_chat_none_returns_original(self, monkeypatch):
        """chat() 回傳 None（呼叫失敗）→ fail-open，回傳原始 list。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        headlines = _make_headlines(3)
        with patch.object(client, "chat", return_value=None):
            result = client.filter_stock_relevant_headlines(headlines)
        assert result == headlines

    def test_indices_embedded_in_text_still_parsed(self, monkeypatch):
        """模型在說明文字中夾帶 JSON 陣列，仍可正確解析。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = _reload_client()
        headlines = _make_headlines(3)
        with patch.object(client, "chat",
                          return_value="以下是與台股相關的新聞：[0, 1]\n其餘不相關。"):
            result = client.filter_stock_relevant_headlines(headlines)
        assert len(result) == 2


# ════════════════════════════════════════════════════════════════════════════
# 件5：news_agent hook — filter 應用與略過
# ════════════════════════════════════════════════════════════════════════════

class TestNewsAgentHook:
    """驗證 news_agent.get_news_context() 的 local LLM hook 行為。"""

    @patch("news_agent.feedparser.parse")
    def test_filter_applied_when_enabled(self, mock_parse, monkeypatch):
        """LOCAL_LLM_BASE_URL 設定時，headlines 通過 filter_stock_relevant_headlines()。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")

        entry = MagicMock()
        entry.title = "台積電法說會"
        entry.link = "http://news/1"
        entry.published = "2026-06-12T09:00:00"
        mock_parse.return_value = MagicMock(entries=[entry])

        # filter 只保留索引 0（第一則）
        filter_result = [
            {"title": "台積電法說會", "url": "http://news/1",
             "published": "2026-06-12T09:00:00", "source": "鉅亨網"}
        ]

        # patch local_llm_client 模組層面（news_agent 用 lazy import）
        fake_client = MagicMock()
        fake_client.is_enabled.return_value = True
        fake_client.filter_stock_relevant_headlines.return_value = filter_result

        with patch.dict("sys.modules", {"local_llm_client": fake_client}):
            # 重新載入 news_agent 讓 lazy import 拿到 fake
            if "news_agent" in sys.modules:
                del sys.modules["news_agent"]
            import news_agent as na
            with patch.object(na, "analyze_sentiment", return_value={
                "sentiment": "positive", "score": 8, "reason": "利多", "headlines_used": 1
            }):
                ctx = na.get_news_context()

        fake_client.filter_stock_relevant_headlines.assert_called_once()

    @patch("news_agent.feedparser.parse")
    def test_filter_bypassed_when_disabled(self, mock_parse, monkeypatch):
        """LOCAL_LLM_BASE_URL 未設定時，filter 完全不呼叫。"""
        monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)

        entry = MagicMock()
        entry.title = "鴻海新廠落成"
        entry.link = "http://news/2"
        entry.published = "2026-06-12T09:00:00"
        mock_parse.return_value = MagicMock(entries=[entry])

        fake_client = MagicMock()
        fake_client.is_enabled.return_value = False

        with patch.dict("sys.modules", {"local_llm_client": fake_client}):
            if "news_agent" in sys.modules:
                del sys.modules["news_agent"]
            import news_agent as na
            with patch.object(na, "analyze_sentiment", return_value={
                "sentiment": "neutral", "score": 5, "reason": "", "headlines_used": 1
            }):
                ctx = na.get_news_context()

        fake_client.filter_stock_relevant_headlines.assert_not_called()

    @patch("news_agent.feedparser.parse")
    def test_hook_exception_returns_unfiltered(self, mock_parse, monkeypatch):
        """filter 拋出例外時，回傳未過濾的原始 headlines，不 raise。"""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")

        entry = MagicMock()
        entry.title = "聯發科業績亮眼"
        entry.link = "http://news/3"
        entry.published = "2026-06-12T09:00:00"
        mock_parse.return_value = MagicMock(entries=[entry])

        fake_client = MagicMock()
        fake_client.is_enabled.return_value = True
        fake_client.filter_stock_relevant_headlines.side_effect = RuntimeError("意外錯誤")

        with patch.dict("sys.modules", {"local_llm_client": fake_client}):
            if "news_agent" in sys.modules:
                del sys.modules["news_agent"]
            import news_agent as na
            with patch.object(na, "analyze_sentiment", return_value={
                "sentiment": "neutral", "score": 5, "reason": "", "headlines_used": 1
            }):
                # 不應 raise
                ctx = na.get_news_context()

        assert "headlines" in ctx
