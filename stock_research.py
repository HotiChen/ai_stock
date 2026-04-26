from __future__ import annotations

import json
import re
from dataclasses import dataclass

import feedparser
import requests
import yfinance as yf

import config

_VALID_ACTIONS = {"buy", "sell", "hold"}

_NEWS_FEEDS = [
    "https://news.google.com/rss/search?q={name}+台股&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    "https://news.google.com/rss/search?q={code}+股票&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class StockFundamentals:
    pe_ratio:       float | None
    eps:            float | None
    market_cap:     float | None
    week52_high:    float | None
    week52_low:     float | None
    dividend_yield: float | None
    revenue_growth: float | None
    profit_margin:  float | None


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_stock_news(code: str, name: str, max_items: int = 5) -> list[str]:
    """Fetch deduplicated news headlines for a specific stock via Google News RSS."""
    headlines: list[str] = []
    for url_tpl in _NEWS_FEEDS:
        url = url_tpl.format(code=code, name=name)
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                headlines.append(entry.title)
        except Exception:
            pass
    seen: set[str] = set()
    unique = []
    for h in headlines:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique[:max_items]


def fetch_stock_fundamentals(code: str) -> StockFundamentals:
    """Fetch fundamentals from Yahoo Finance (code.TW format)."""
    try:
        ticker = yf.Ticker(f"{code}.TW")
        info = ticker.info
        return StockFundamentals(
            pe_ratio=info.get("trailingPE"),
            eps=info.get("trailingEps"),
            market_cap=info.get("marketCap"),
            week52_high=info.get("fiftyTwoWeekHigh"),
            week52_low=info.get("fiftyTwoWeekLow"),
            dividend_yield=info.get("dividendYield"),
            revenue_growth=info.get("revenueGrowth"),
            profit_margin=info.get("profitMargins"),
        )
    except Exception:
        return StockFundamentals(
            pe_ratio=None, eps=None, market_cap=None,
            week52_high=None, week52_low=None,
            dividend_yield=None, revenue_growth=None, profit_margin=None,
        )


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt(val: float | None, fmt: str = ".2f", suffix: str = "") -> str:
    return f"{val:{fmt}}{suffix}" if val is not None else "N/A"


def format_fundamentals_text(f: StockFundamentals) -> str:
    cap_str = "N/A"
    if f.market_cap is not None:
        cap_str = f"{f.market_cap / 1e8:.0f} 億"

    rev_str = "N/A"
    if f.revenue_growth is not None:
        rev_str = f"{f.revenue_growth * 100:+.1f}%"

    dy_str = "N/A"
    if f.dividend_yield is not None:
        dy_str = f"{f.dividend_yield * 100:.2f}%"

    pm_str = "N/A"
    if f.profit_margin is not None:
        pm_str = f"{f.profit_margin * 100:.1f}%"

    return (
        f"本益比(P/E)：{_fmt(f.pe_ratio, '.1f')}\n"
        f"EPS：{_fmt(f.eps, '.2f')} 元\n"
        f"市值：{cap_str}\n"
        f"52 週高/低：{_fmt(f.week52_high, '.2f')} / {_fmt(f.week52_low, '.2f')}\n"
        f"殖利率：{dy_str}\n"
        f"營收成長：{rev_str}\n"
        f"淨利率：{pm_str}"
    )


# ── Prompt ────────────────────────────────────────────────────────────────────

def build_stock_research_prompt(
    code: str,
    name: str,
    news: list[str],
    fundamentals: StockFundamentals,
    indicators: dict,
) -> str:
    news_text = "\n".join(f"- {h}" for h in news) if news else "（無近期新聞）"
    ind_lines = "\n".join(f"  {k}: {v}" for k, v in indicators.items()) if indicators else "  （無指標資料）"
    pe = fundamentals.pe_ratio
    pe_comment = ""
    if pe is not None:
        if pe < 10:
            pe_comment = "（P/E 低於 10，可能低估或產業低迷）"
        elif pe < 20:
            pe_comment = "（P/E 合理區間）"
        else:
            pe_comment = "（P/E 偏高，需謹慎）"

    return f"""你是專業台股分析師，請根據以下資料對 {name}（{code}）做出投資建議。

== 近期新聞 ==
{news_text}

== 基本面 ==
{format_fundamentals_text(fundamentals)}
{pe_comment}

== 技術指標 ==
{ind_lines}

== 分析框架 ==
- 技術面：RSI>70 超買（謹慎）、RSI<30 超賣（機會）、均線多頭排列看多
- 基本面：P/E<15 偏低估、殖利率>4% 具吸引力、營收成長正向加分
- 新聞面：利多強化買進信號、利空提高風險

請綜合三面向給出建議，只回答 JSON，不要其他文字：
{{"action": "buy/sell/hold", "reason": "繁體中文，具體說明買賣理由", "confidence": 0到10, "target_price": 目標價或null, "stop_loss": 建議停損價或null}}"""


# ── Parse ─────────────────────────────────────────────────────────────────────

def parse_analysis_result(raw: str) -> dict:
    """Parse AI JSON response into a structured analysis dict with safe defaults."""
    try:
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {}
    except Exception:
        data = {}

    action = data.get("action", "hold")
    if action not in _VALID_ACTIONS:
        action = "hold"

    confidence = data.get("confidence", 5)
    try:
        confidence = max(0, min(10, int(confidence)))
    except (TypeError, ValueError):
        confidence = 5

    return {
        "action":       action,
        "reason":       str(data.get("reason", "AI 分析無法取得建議")),
        "confidence":   confidence,
        "target_price": data.get("target_price"),
        "stop_loss":    data.get("stop_loss"),
    }


# ── AI analysis ───────────────────────────────────────────────────────────────

def analyze_stock_ai(
    code: str,
    name: str,
    news: list[str],
    fundamentals: StockFundamentals,
    indicators: dict,
    timeout: int | None = None,
) -> dict:
    """Call Ollama to analyze a stock and return structured recommendation."""
    prompt = build_stock_research_prompt(code, name, news, fundamentals, indicators)
    try:
        resp = requests.post(
            f"{config.OLLAMA_URL}/api/generate",
            json={"model": config.DECISION_MODEL, "prompt": prompt, "stream": False},
            timeout=timeout or config.OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        return parse_analysis_result(raw)
    except Exception:
        return parse_analysis_result("")
