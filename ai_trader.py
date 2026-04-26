from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import config
from news_agent import call_ollama, extract_json
from stock_research import StockFundamentals, fetch_stock_fundamentals, fetch_stock_news, format_fundamentals_text

WATCHLIST = ["2330", "2317", "2454", "2382", "3711", "2308", "00631L"]
_VALID_ACTIONS = {"buy", "sell", "hold"}


def build_decision_prompt(
    indicators: dict,
    news: dict,
    position: dict | None,
    cash: float,
    already_bought_today: bool,
    already_sold_today: bool,
) -> str:
    pos_str = json.dumps(position, ensure_ascii=False) if position else "無持倉"
    return f"""你是台股當沖交易 AI，根據資料做出今天的操作決定。

=== 目前狀態 ===
可用現金：{cash:.0f} 台幣（上限 {config.BUDGET:.0f}）
今日已買入：{"是" if already_bought_today else "否"}
今日已賣出：{"是" if already_sold_today else "否"}
持倉：{pos_str}

=== 技術指標 ===
{json.dumps(indicators, ensure_ascii=False, indent=2)}

=== 新聞情緒 ===
情緒：{news.get("sentiment")}，分數：{news.get("score")}/10
理由：{news.get("reason")}
近期新聞：{", ".join(news.get("headlines", [])[:3])}

=== 交易規則 ===
- 每天只能買一次、賣一次
- 預算上限 {config.BUDGET:.0f} 台幣
- 台股一張 = 1000 股，quantity 填張數
- {config.FORCE_CLOSE_TIME} 前必須平倉（當沖）
- 虧損超過 {config.STOP_LOSS_PCT}% 立刻停損
- 今日已買且已賣 → 只能 hold

只回答 JSON，不要任何其他文字：
{{"action": "buy/sell/hold", "stock_code": "代碼或空字串", "quantity": 張數, "reason": "繁體中文理由", "confidence": 0到10}}"""


def sanitize_decision(raw: dict) -> dict:
    """Ensure decision dict has all required keys and valid values."""
    action = raw.get("action", "hold")
    if action not in _VALID_ACTIONS:
        action = "hold"

    quantity = raw.get("quantity", 1)
    try:
        quantity = max(1, int(quantity))
    except (TypeError, ValueError):
        quantity = 1

    confidence = raw.get("confidence", 5)
    try:
        confidence = max(0, min(10, int(confidence)))
    except (TypeError, ValueError):
        confidence = 5

    return {
        "action": action,
        "stock_code": str(raw.get("stock_code", "")),
        "quantity": quantity,
        "reason": str(raw.get("reason", "")),
        "confidence": confidence,
    }


def make_decision(
    indicators: dict,
    news: dict,
    position: dict | None,
    cash: float,
    already_bought_today: bool = False,
    already_sold_today: bool = False,
) -> dict:
    """Ask DECISION_MODEL to make a trading decision."""
    if already_bought_today and already_sold_today:
        return sanitize_decision({
            "action": "hold",
            "reason": "今日已完成一買一賣，停止操作",
            "confidence": 10,
        })

    prompt = build_decision_prompt(
        indicators, news, position, cash, already_bought_today, already_sold_today
    )

    try:
        raw = call_ollama(config.DECISION_MODEL, prompt, timeout=60)
        result = extract_json(raw)
        if result.get("action") in _VALID_ACTIONS:
            return sanitize_decision(result)
    except Exception:
        pass

    return sanitize_decision({"action": "hold", "reason": "AI 分析失敗，保守持有", "confidence": 0})


def build_enriched_decision_prompt(
    code: str,
    name: str,
    indicators: dict,
    market_news: dict,
    stock_news: list[str],
    fundamentals: StockFundamentals | None,
    position: dict | None,
    cash: float,
    already_bought_today: bool = False,
    already_sold_today: bool = False,
) -> str:
    pos_str = json.dumps(position, ensure_ascii=False) if position else "無持倉"
    stock_news_text = "\n".join(f"- {h}" for h in stock_news) if stock_news else "（無近期個股新聞）"
    fund_text = format_fundamentals_text(fundamentals) if fundamentals else "（基本面資料無法取得）"

    return f"""你是台股交易 AI，根據以下三面向資料做出今天的操作決定。

=== 分析標的 ===
股票：{name}（{code}）

=== 個股近期新聞 ===
{stock_news_text}

=== 基本面 ===
{fund_text}

=== 技術指標 ===
{json.dumps(indicators, ensure_ascii=False, indent=2)}

=== 大盤市場情緒 ===
情緒：{market_news.get("sentiment")}，分數：{market_news.get("score")}/10
理由：{market_news.get("reason")}
近期新聞：{", ".join(market_news.get("headlines", [])[:3])}

=== 目前帳戶狀態 ===
可用現金：{cash:.0f} 台幣（上限 {config.BUDGET:.0f}）
今日已買入：{"是" if already_bought_today else "否"}
今日已賣出：{"是" if already_sold_today else "否"}
持倉：{pos_str}

=== 分析框架 ===
- 個股新聞（利多/利空消息、主力動向）
- 基本面（P/E<15 低估、殖利率>4% 吸引力、營收成長正向）
- 技術面（RSI<30 超賣機會、RSI>70 超買謹慎、均線多頭排列看多）
- 大盤情緒（整體市場多空氛圍）

=== 交易規則 ===
- 每天只能買一次、賣一次
- 預算上限 {config.BUDGET:.0f} 台幣
- 台股一張 = 1000 股，quantity 填張數
- {config.FORCE_CLOSE_TIME} 前必須平倉
- 虧損超過 {config.STOP_LOSS_PCT}% 立刻停損

只回答 JSON，不要任何其他文字：
{{"action": "buy/sell/hold", "stock_code": "代碼或空字串", "quantity": 張數, "reason": "繁體中文，三面向綜合理由", "confidence": 0到10}}"""


def make_enriched_decision(
    code: str,
    name: str,
    indicators: dict,
    market_news: dict,
    position: dict | None,
    cash: float,
    already_bought_today: bool = False,
    already_sold_today: bool = False,
) -> dict:
    """Make decision enriched with stock-specific news and fundamentals."""
    if already_bought_today and already_sold_today:
        return sanitize_decision({"action": "hold", "reason": "今日已完成一買一賣，停止操作", "confidence": 10})

    try:
        stock_news = fetch_stock_news(code, name)
    except Exception:
        stock_news = []

    try:
        fundamentals = fetch_stock_fundamentals(code)
    except Exception:
        fundamentals = None

    prompt = build_enriched_decision_prompt(
        code=code, name=name,
        indicators=indicators, market_news=market_news,
        stock_news=stock_news, fundamentals=fundamentals,
        position=position, cash=cash,
        already_bought_today=already_bought_today,
        already_sold_today=already_sold_today,
    )

    try:
        raw = call_ollama(config.DECISION_MODEL, prompt, timeout=90)
        result = extract_json(raw)
        if result.get("action") in _VALID_ACTIONS:
            return sanitize_decision(result)
    except Exception:
        pass

    return sanitize_decision({"action": "hold", "reason": "AI 分析失敗，保守持有", "confidence": 0})


def log_decision(decision: dict, news: dict, path: str = "data/ai_log.jsonl") -> None:
    """Append decision to JSONL log file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "decision": decision,
        "news_sentiment": news.get("sentiment"),
        "news_score": news.get("score"),
        "model_used": config.DECISION_MODEL,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
