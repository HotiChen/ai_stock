from unittest.mock import MagicMock, patch

import pytest

from news_agent import analyze_sentiment, extract_json, fetch_headlines, get_news_context


class TestExtractJson:
    def test_extracts_clean_json(self):
        text = '{"sentiment": "positive", "score": 8}'
        assert extract_json(text) == {"sentiment": "positive", "score": 8}

    def test_extracts_json_embedded_in_text(self):
        text = 'Here is the answer: {"sentiment": "negative", "score": 3} done.'
        result = extract_json(text)
        assert result["sentiment"] == "negative"

    def test_returns_empty_on_no_json(self):
        assert extract_json("no json here") == {}

    def test_returns_empty_on_broken_json(self):
        assert extract_json("{broken: json}") == {}

    def test_handles_nested_json(self):
        text = '{"action": "buy", "reason": "上漲趨勢"}'
        result = extract_json(text)
        assert result["action"] == "buy"


class TestFetchHeadlines:
    @patch("news_agent.feedparser.parse")
    def test_returns_list_of_strings(self, mock_parse):
        mock_entry = MagicMock()
        mock_entry.title = "台積電創新高"
        mock_parse.return_value = MagicMock(entries=[mock_entry])
        headlines = fetch_headlines(max_per_feed=1)
        assert isinstance(headlines, list)
        assert any("台積電" in h for h in headlines)

    @patch("news_agent.feedparser.parse")
    def test_handles_feed_error_gracefully(self, mock_parse):
        mock_parse.side_effect = Exception("network error")
        headlines = fetch_headlines()
        assert headlines == []

    @patch("news_agent.feedparser.parse")
    def test_respects_max_per_feed(self, mock_parse):
        entries = [MagicMock(title=f"新聞{i}") for i in range(10)]
        mock_parse.return_value = MagicMock(entries=entries)
        headlines = fetch_headlines(max_per_feed=2)
        # 3 feeds × 2 per feed = max 6
        assert len(headlines) <= 6

    @patch("news_agent.feedparser.parse")
    def test_empty_feed_returns_empty(self, mock_parse):
        mock_parse.return_value = MagicMock(entries=[])
        headlines = fetch_headlines()
        assert headlines == []


class TestAnalyzeSentiment:
    @patch("news_agent.call_ollama")
    def test_positive_sentiment(self, mock_ollama):
        mock_ollama.return_value = '{"sentiment": "positive", "score": 8, "reason": "多檔漲停"}'
        result = analyze_sentiment(["台積電漲停", "外資大買超"])
        assert result["sentiment"] == "positive"
        assert result["score"] == 8

    @patch("news_agent.call_ollama")
    def test_negative_sentiment(self, mock_ollama):
        mock_ollama.return_value = '{"sentiment": "negative", "score": 2, "reason": "市場恐慌"}'
        result = analyze_sentiment(["美股重挫", "外資賣超創新高"])
        assert result["sentiment"] == "negative"

    @patch("news_agent.call_ollama")
    def test_ollama_failure_returns_neutral(self, mock_ollama):
        mock_ollama.side_effect = Exception("connection refused")
        result = analyze_sentiment(["任何新聞"])
        assert result["sentiment"] == "neutral"
        assert "score" in result

    def test_empty_headlines_returns_neutral(self):
        result = analyze_sentiment([])
        assert result["sentiment"] == "neutral"

    @patch("news_agent.call_ollama")
    def test_result_has_required_keys(self, mock_ollama):
        mock_ollama.return_value = '{"sentiment": "neutral", "score": 5, "reason": "平盤"}'
        result = analyze_sentiment(["平盤整理"])
        assert "sentiment" in result
        assert "score" in result
        assert "reason" in result
        assert "headlines_used" in result

    @patch("news_agent.call_ollama")
    def test_garbled_response_returns_neutral(self, mock_ollama):
        mock_ollama.return_value = "我無法分析這個問題"
        result = analyze_sentiment(["任何新聞"])
        assert result["sentiment"] == "neutral"


class TestGetNewsContext:
    @patch("news_agent.analyze_sentiment")
    @patch("news_agent.fetch_headlines")
    def test_returns_combined_result(self, mock_fetch, mock_analyze):
        mock_fetch.return_value = ["台積電漲停"]
        mock_analyze.return_value = {
            "sentiment": "positive", "score": 8, "reason": "利多", "headlines_used": 1
        }
        result = get_news_context()
        assert "sentiment" in result
        assert "headlines" in result
        assert "fetched_at" in result

    @patch("news_agent.analyze_sentiment")
    @patch("news_agent.fetch_headlines")
    def test_headlines_capped_at_5(self, mock_fetch, mock_analyze):
        mock_fetch.return_value = [f"新聞{i}" for i in range(20)]
        mock_analyze.return_value = {"sentiment": "neutral", "score": 5, "reason": ""}
        result = get_news_context()
        assert len(result["headlines"]) <= 5
