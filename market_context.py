from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import feedparser
import requests

_US_TICKERS = {
    "sp500":  "^GSPC",
    "nasdaq": "^IXIC",
    "sox":    "^SOX",
    "vix":    "^VIX",
}

_NEWS_QUERIES = [
    ("us_policy",     "Fed 利率 聯準會"),
    ("tw_policy",     "台灣 政策 補貼 半導體"),
    ("us_policy",     "美國 關稅 tariff 台灣"),
    ("semiconductor", "台積電 TSMC 半導體 AI 伺服器"),
    ("market",        "台股 外資 法人 買超"),
]


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class USMarketSnapshot:
    sp500_change:  float
    nasdaq_change: float
    sox_change:    float
    vix:           float


@dataclass
class MacroHeadline:
    title:    str
    source:   str
    category: str   # "us_policy" | "tw_policy" | "market" | "semiconductor"


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_us_market() -> Optional[USMarketSnapshot]:
    try:
        changes: dict[str, float] = {}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        for key, ticker in _US_TICKERS.items():
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=2d&interval=1d"
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                closes = data.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                closes = [c for c in closes if c is not None]
                if len(closes) >= 2:
                    changes[key] = float((closes[-1] - closes[-2]) / closes[-2] * 100)
                else:
                    changes[key] = 0.0
            else:
                changes[key] = 0.0

        return USMarketSnapshot(
            sp500_change=changes.get("sp500", 0.0),
            nasdaq_change=changes.get("nasdaq", 0.0),
            sox_change=changes.get("sox", 0.0),
            vix=changes.get("vix", 20.0),
        )
    except Exception:
        return None


def fetch_macro_news(max_per_query: int = 3) -> list[MacroHeadline]:
    headlines: list[MacroHeadline] = []
    seen: set[str] = set()
    for category, query in _NEWS_QUERIES:
        try:
            url = f"https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_query]:
                title = getattr(entry, "title", "").strip()
                if title and title not in seen:
                    seen.add(title)
                    headlines.append(MacroHeadline(
                        title=title,
                        source=getattr(entry, "source", {}).get("title", "") if hasattr(entry, "source") else "",
                        category=category,
                    ))
        except Exception:
            continue
    return headlines


# ── Classify ──────────────────────────────────────────────────────────────────

def classify_market_sentiment(snapshot: USMarketSnapshot) -> str:
    score = (
        (1 if snapshot.sp500_change > 0.3 else -1 if snapshot.sp500_change < -0.3 else 0)
        + (1 if snapshot.nasdaq_change > 0.5 else -1 if snapshot.nasdaq_change < -0.5 else 0)
        + (1 if snapshot.sox_change > 1.0 else -1 if snapshot.sox_change < -1.0 else 0)
        + (-1 if snapshot.vix > 25 else 1 if snapshot.vix < 18 else 0)
    )
    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


# ── Build summary text ────────────────────────────────────────────────────────

def build_market_summary(snapshot: USMarketSnapshot, headlines: list[MacroHeadline]) -> str:
    sentiment = classify_market_sentiment(snapshot)
    sentiment_zh = {"bullish": "偏多", "bearish": "偏空", "neutral": "震盪"}.get(sentiment, sentiment)

    lines = [
        f"【美股行情】S&P500 {snapshot.sp500_change:+.2f}%　"
        f"NASDAQ {snapshot.nasdaq_change:+.2f}%　"
        f"費半 {snapshot.sox_change:+.2f}%　"
        f"VIX {snapshot.vix:.1f}　整體情緒：{sentiment_zh}（{sentiment}）",
    ]
    if headlines:
        lines.append("【總體新聞】")
        for h in headlines[:8]:
            lines.append(f"- [{h.category}] {h.title}")

    return "\n".join(lines)
