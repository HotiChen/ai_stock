from __future__ import annotations

import json
import re
from datetime import datetime

import feedparser
import requests

import config

RSS_FEEDS = [
    ("鉅亨網", "https://www.cnyes.com/rss/cat/tw_stock"),
    ("Yahoo財經", "https://tw.stock.yahoo.com/rss"),
    ("MoneyDJ", "https://www.moneydj.com/rss/news.aspx"),
]


def extract_json(text: str) -> dict:
    """Extract first valid JSON object from text."""
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def call_ollama(model: str, prompt: str, timeout: int | None = None) -> str:
    """Call Ollama generate API and return response text."""
    resp = requests.post(
        f"{config.OLLAMA_URL}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=timeout or config.OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def fetch_headlines(max_per_feed: int = 5) -> list[str]:
    """Fetch latest headlines from Taiwan stock RSS feeds (titles only).

    保留純標題回傳以維持既有 caller（analyze_sentiment 等）的向後相容。
    """
    return [item["title"] for item in fetch_headlines_structured(max_per_feed)]


def fetch_headlines_structured(max_per_feed: int = 5) -> list[dict]:
    """Fetch latest headlines as structured dicts.

    每則為 {"title", "url", "published", "source"}（依 design §2.2）。
    缺 link/published 時退化為空字串，不 raise。
    """
    items: list[dict] = []
    for name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                title = getattr(entry, "title", "") or ""
                link = getattr(entry, "link", "") or ""
                published = getattr(entry, "published", "") or ""
                items.append({
                    "title": title,
                    "url": link,
                    "published": published,
                    "source": name,
                })
        except Exception:
            pass
    return items


def analyze_sentiment(headlines: list[str]) -> dict:
    """Use NEWS_MODEL to classify market sentiment from headlines."""
    if not headlines:
        return {"sentiment": "neutral", "score": 5, "reason": "無新聞資料", "headlines_used": 0}

    sample = headlines[:8]
    prompt = f"""分析以下台股新聞標題的整體市場情緒。
只用 JSON 回答，不要其他文字。

新聞：
{chr(10).join(f"- {h}" for h in sample)}

回答格式（sentiment 只能填 positive/negative/neutral）：
{{"sentiment": "positive", "score": 7, "reason": "一句話說明"}}"""

    try:
        raw = call_ollama(config.NEWS_MODEL, prompt)
        result = extract_json(raw)
        if "sentiment" in result and result["sentiment"] in ("positive", "negative", "neutral"):
            result["headlines_used"] = len(sample)
            return result
    except Exception:
        pass

    return {"sentiment": "neutral", "score": 5, "reason": "分析失敗，預設中性", "headlines_used": 0}


def get_news_context() -> dict:
    """Full pipeline: fetch → [local LLM pre-filter] → analyze → return context dict.

    headlines 為結構化 dict list（title/url/published/source）；
    為向後相容保留 headlines_text（純標題 list[str]）。

    local LLM 前置過濾：僅在 LOCAL_LLM_BASE_URL 設定時啟用；任何失敗皆 fail-open
    （保留全部 headlines），不影響既有行為。
    """
    items = fetch_headlines_structured()

    # --- local LLM opt-in pre-filter（廉價前置過濾，fail-open）---
    try:
        import local_llm_client  # lazy import：未安裝時不影響載入
        if local_llm_client.is_enabled():
            items = local_llm_client.filter_stock_relevant_headlines(items)
    except Exception:  # noqa: BLE001
        pass  # fail-open：任何意外直接略過，保留原始 items

    titles = [item["title"] for item in items]
    sentiment = analyze_sentiment(titles)
    sentiment["fetched_at"] = datetime.now().isoformat()
    sentiment["headlines"] = items[:5]
    sentiment["headlines_text"] = titles[:5]
    return sentiment
