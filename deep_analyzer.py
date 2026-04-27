from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

import config
from ai_client import call_haiku
from technical_indicators import fetch_indicators, format_indicators_text


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class AnalysisFactor:
    historical_trend: str
    news:             str
    theme:            str
    us_market:        str
    tw_policy:        str
    us_policy:        str


@dataclass
class DeepAnalysis:
    code:            str
    name:            str
    signal:          str   # "buy" | "hold" | "sell"
    confidence:      int   # 0–10
    summary:         str
    factors:         AnalysisFactor
    hold_days:       int   = 1
    target_price:    Optional[float] = None
    stop_loss_price: Optional[float] = None


# ── Historical price trend ────────────────────────────────────────────────────

def get_price_trend_summary(code: str, days: int = 20) -> str:
    try:
        df = yf.download(f"{code}.TW", period=f"{days + 5}d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 5:
            return "歷史資料不足"
        closes = df["Close"].dropna().values[-days:]
        ma5  = float(closes[-5:].mean())
        ma20 = float(closes.mean())
        latest = float(closes[-1])
        trend = "向上" if ma5 > ma20 else "向下" if ma5 < ma20 else "橫盤"
        pct = (latest - closes[0]) / closes[0] * 100
        return f"{days}日走勢：{trend}，區間漲跌 {pct:+.1f}%，MA5={ma5:.1f} MA20={ma20:.1f}"
    except Exception:
        return "歷史走勢資料無法取得"


# ── Prompt ────────────────────────────────────────────────────────────────────

def build_deep_prompt(
    code: str,
    name: str,
    price_trend: str,
    news: list[str],
    fundamentals_text: str,
    market_summary: str,
    theme_info: str,
    technical_text: str = "",
) -> str:
    news_text = "\n".join(f"- {n}" for n in news[:5]) if news else "（無近期新聞）"

    return f"""你是一位專業台股分析師，請從以下維度深度分析 {code} {name}，給出買賣建議。

=== 1. 技術指標（量化數據）===
{technical_text or price_trend or '無資料'}

=== 2. 價格走勢摘要 ===
{price_trend or '無資料'}

=== 3. 個股新聞 ===
{news_text}

=== 4. 題材關聯 ===
{theme_info or '無題材資訊'}

=== 5. 美股市場影響 ===
{market_summary or '無美股資料'}

=== 6. 台灣 / 美國政策影響 ===
（請從市場摘要中提取，特別留意 Fed、關稅、台灣政府政策）

=== 7. 基本面 ===
{fundamentals_text or '無基本面資料'}

請綜合以上分析，特別注意：
- 技術指標的多頭排列、帶量突破、BBU 過熱、KD/RSI 超買超賣信號
- 不要追 BBU 上軌外的過熱股
- 帶量突破（量比 ≥ 1.5x）是強勢信號

只回答 JSON，不要其他文字：
{{
  "signal": "buy/hold/sell",
  "confidence": 0到10,
  "summary": "兩到三句話的綜合分析",
  "factors": {{
    "historical_trend": "技術面分析（含均線、KD、RSI、布林、量比）一句話",
    "news": "新聞面分析一句話",
    "theme": "題材面分析一句話",
    "us_market": "美股影響一句話",
    "tw_policy": "台灣政策影響一句話",
    "us_policy": "美國政策影響一句話"
  }},
  "hold_days": 建議持有天數整數,
  "target_price": 目標價或null,
  "stop_loss_price": 停損價或null
}}"""


# ── Parser ────────────────────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        return json.loads(m.group())
    raise ValueError("no JSON")


def parse_deep_response(code: str, name: str, raw: str) -> DeepAnalysis:
    _default_factors = AnalysisFactor("", "", "", "", "", "")
    try:
        data = _extract_json(raw)
        signal = data.get("signal", "hold")
        if signal not in ("buy", "hold", "sell"):
            signal = "hold"
        confidence = max(0, min(10, int(data.get("confidence", 5))))
        f = data.get("factors", {})
        factors = AnalysisFactor(
            historical_trend=f.get("historical_trend", ""),
            news=f.get("news", ""),
            theme=f.get("theme", ""),
            us_market=f.get("us_market", ""),
            tw_policy=f.get("tw_policy", ""),
            us_policy=f.get("us_policy", ""),
        )
        return DeepAnalysis(
            code=code, name=name, signal=signal, confidence=confidence,
            summary=data.get("summary", ""),
            factors=factors,
            hold_days=max(1, int(data.get("hold_days", 1))),
            target_price=data.get("target_price"),
            stop_loss_price=data.get("stop_loss_price"),
        )
    except Exception:
        return DeepAnalysis(code=code, name=name, signal="hold", confidence=0,
                            summary="分析失敗，預設持有", factors=_default_factors)


# ── Main entry ────────────────────────────────────────────────────────────────

def run_deep_analysis(
    code: str,
    name: str,
    news: list[str],
    fundamentals_text: str,
    market_summary: str,
    theme_info: str,
) -> DeepAnalysis:
    price_trend = get_price_trend_summary(code)
    indicators  = fetch_indicators(code)
    tech_text   = format_indicators_text(indicators) if indicators else ""
    prompt = build_deep_prompt(code, name, price_trend, news,
                               fundamentals_text, market_summary, theme_info,
                               technical_text=tech_text)
    try:
        raw = call_haiku(prompt)
        return parse_deep_response(code, name, raw)
    except Exception:
        from dataclasses import fields
        return DeepAnalysis(code=code, name=name, signal="hold", confidence=0,
                            summary="AI 分析失敗，預設持有",
                            factors=AnalysisFactor("", "", "", "", "", ""))
