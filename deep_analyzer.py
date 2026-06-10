from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

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
    # AI 實際引用的新聞（依 news_refs_used 還原為完整 dict），預設空 list
    news_refs:       list = field(default_factory=list)


# ── Historical price trend ────────────────────────────────────────────────────

def get_price_trend_summary(api, code: str, days: int = 20) -> str:
    from datetime import date, timedelta
    try:
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            return "歷史資料不足"
        end = date.today()
        start = end - timedelta(days=days + 15)
        kbars = api.kbars(contract, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
        
        if not kbars or not kbars.get('Close') or len(kbars['Close']) < 5:
            return "歷史資料不足"
            
        closes = kbars["Close"][-days:]
        import numpy as np
        ma5  = float(np.mean(closes[-5:]))
        ma20 = float(np.mean(closes))
        latest = float(closes[-1])
        trend = "向上" if ma5 > ma20 else "向下" if ma5 < ma20 else "橫盤"
        pct = (latest - closes[0]) / closes[0] * 100
        return f"{days}日走勢：{trend}，區間漲跌 {pct:+.1f}%，MA5={ma5:.1f} MA20={ma20:.1f}"
    except Exception:
        return "歷史走勢資料無法取得"


# ── Prompt ────────────────────────────────────────────────────────────────────

def _normalize_news(news) -> list[dict]:
    """將 news 正規化為帶編號的 dict list（最多 5 則）。

    支援兩種輸入：
      - 新格式：dict（title/url/published/source）
      - legacy：純字串（視為 title-only）
    回傳每則含 id（N1..N5）、title、published。
    """
    normalized: list[dict] = []
    for i, item in enumerate((news or [])[:5], start=1):
        if isinstance(item, dict):
            d = dict(item)
        else:
            d = {"title": str(item), "url": "", "published": "", "source": ""}
        d["id"] = f"N{i}"
        normalized.append(d)
    return normalized


def build_deep_prompt(
    code: str,
    name: str,
    price_trend: str,
    news: list,
    fundamentals_text: str,
    market_summary: str,
    theme_info: str,
    technical_text: str = "",
) -> str:
    numbered = _normalize_news(news)
    if numbered:
        lines = []
        for n in numbered:
            pub = n.get("published") or "時間未知"
            lines.append(f"[{n['id']}]（{pub}）{n.get('title', '')}")
        news_text = "\n".join(lines)
    else:
        news_text = "（無近期新聞）"

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

=== 新聞 / 影片來源使用規則（務必遵守）===
1. 新聞與影片的情緒僅作為 catalyst（催化劑）確認與風險檢核之用；單獨一則訊息不得使 confidence 提升超過 +1。
2. 當新聞 / 影片訊號與技術面 / 籌碼面訊號矛盾時，以技術面 / 籌碼面為準。
3. 發布時間超過 24 小時的訊息視為已被市場 price-in，僅作背景參考，不得作為主要進場理由。

請在輸出中以 "news_refs_used" 列出你「實際引用」的新聞編號（如 ["N1", "N3"]）；若未引用任何新聞，回傳空陣列 []。

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
  "stop_loss_price": 停損價或null,
  "news_refs_used": ["N1", "N3"]
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


def _resolve_news_refs(data: dict, news) -> list[dict]:
    """把 AI 回傳的 news_refs_used（如 ["N1"]）還原為完整來源 dict。

    依 build_deep_prompt 的編號規則（_normalize_news），news 可為 dict 或
    legacy str。AI 未提供 news_refs_used 時回傳空 list（容忍欄位缺漏）。
    """
    used = data.get("news_refs_used")
    if not isinstance(used, list) or not used:
        return []
    by_id = {n["id"]: n for n in _normalize_news(news)}
    resolved: list[dict] = []
    for ref in used:
        item = by_id.get(ref)
        if item is not None:
            resolved.append(item)
    return resolved


def parse_deep_response(
    code: str, name: str, raw: str, news=None
) -> DeepAnalysis:
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
            news_refs=_resolve_news_refs(data, news),
        )
    except Exception:
        return DeepAnalysis(code=code, name=name, signal="hold", confidence=0,
                            summary="分析失敗，預設持有", factors=_default_factors)


# ── Main entry ────────────────────────────────────────────────────────────────

def run_deep_analysis(
    api,
    code: str,
    name: str,
    news: list[str],
    fundamentals_text: str,
    market_summary: str,
    theme_info: str,
) -> DeepAnalysis:
    from notifier import notify_analysis_result

    price_trend = get_price_trend_summary(api, code)
    indicators  = fetch_indicators(api, code)
    
    # ── Pre-filter: 節省 API 呼叫成本 ──
    if indicators:
        price = indicators.get("current_price", 0)
        ma20  = indicators.get("MA20", 0)
        vol_r = indicators.get("volume_ratio", 0)
        # 若股價低於月線，或量能極度萎縮，直接判定為觀望，不呼叫 AI
        if price < ma20 or vol_r < 0.5:
            reason = "股價低於月線" if price < ma20 else "量能極度萎縮 (量比 < 0.5)"
            result = DeepAnalysis(
                code=code, name=name, signal="hold", confidence=0,
                summary=f"未達 AI 分析門檻（前置過濾：{reason}）",
                factors=AnalysisFactor("", "", "", "", "", "")
            )
            return result

    tech_text   = format_indicators_text(indicators) if indicators else ""
    prompt = build_deep_prompt(code, name, price_trend, news,
                               fundamentals_text, market_summary, theme_info,
                               technical_text=tech_text)
    try:
        raw    = call_haiku(prompt)
        result = parse_deep_response(code, name, raw, news=news)
    except Exception:
        result = DeepAnalysis(code=code, name=name, signal="hold", confidence=0,
                              summary="AI 分析失敗，預設持有",
                              factors=AnalysisFactor("", "", "", "", "", ""))

    notify_analysis_result(
        code=result.code,
        name=result.name,
        signal=result.signal,
        confidence=result.confidence,
        summary=result.summary,
        factors={
            "historical_trend": result.factors.historical_trend,
            "news":             result.factors.news,
            "theme":            result.factors.theme,
            "us_market":        result.factors.us_market,
            "tw_policy":        result.factors.tw_policy,
        },
        target_price=result.target_price,
        stop_loss_price=result.stop_loss_price,
    )
    return result
