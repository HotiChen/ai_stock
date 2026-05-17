"""
daytrading_analyzer.py — 當沖專屬 AI 分析器

輸入：技術指標、法人籌碼、大盤方向、當沖評分
輸出：DayTradingAnalysis（進場區間、目標價、停損、時機建議）
使用 call_haiku 確保速度快、成本低。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from ai_client import call_haiku

log = logging.getLogger(__name__)

_MIN_DT_SCORE = 4  # 低於此分數直接 skip，不呼叫 AI


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class DayTradingAnalysis:
    code:         str
    name:         str
    action:       str            # "long" | "skip"
    confidence:   int            # 0–10
    entry_low:    Optional[float]
    entry_high:   Optional[float]
    target_price: Optional[float]
    stop_loss:    Optional[float]
    timing:       str            # "開盤" | "拉回" | "突破" | "觀望"
    summary:      str


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_daytrading_prompt(
    code: str,
    name: str,
    indicators: Optional[dict],
    chip: Optional[dict],
    market: Optional[dict],
    dt_score: int,
) -> str:
    # ── 技術指標 ──
    if indicators:
        price  = indicators.get("current_price", "—")
        rsi    = indicators.get("RSI", "—")
        vr     = indicators.get("volume_ratio", "—")
        vwap   = indicators.get("VWAP", "—")
        atr    = indicators.get("ATR", "—")
        kd_k   = indicators.get("KD_K", "—")
        kd_d   = indicators.get("KD_D", "—")
        bb_pos = indicators.get("BB_position", "—")
        bull   = indicators.get("bullish_alignment", False)
        bear   = indicators.get("bearish_alignment", False)
        align  = "多頭排列" if bull else ("空頭排列" if bear else "無明顯排列")
        tech_text = (
            f"現價 {price}　RSI {rsi}　量比 {vr}x\n"
            f"VWAP {vwap}　ATR {atr}\n"
            f"KD K={kd_k} D={kd_d}　BB位置 {bb_pos}\n"
            f"均線：{align}"
        )
    else:
        tech_text = "技術指標無法取得"

    # ── 法人籌碼 ──
    if chip:
        fn   = chip.get("foreign_net", 0)
        tn   = chip.get("investment_trust_net", 0)
        cont = chip.get("foreign_continuous_buy", 0)
        chip_text = f"外資 {fn:+,.0f} 張　投信 {tn:+,.0f} 張　外資連買 {cont} 日"
    else:
        chip_text = "籌碼資料無法取得"

    # ── 大盤 ──
    if market:
        idx_pct = market.get("index_change_pct", 0.0)
        market_text = f"大盤漲跌 {idx_pct:+.2f}%"
    else:
        market_text = "大盤資料無法取得"

    return f"""你是一位台股當沖交易專家，請針對以下資料給出今日當沖操作建議。

股票：{code} {name}　當沖評分：{dt_score}/10

【技術指標】
{tech_text}

【法人籌碼】
{chip_text}

【大盤方向】
{market_text}

請依據以上資料，判斷今日是否適合當沖做多，並給出具體進場建議。
- 若不適合（評分低、籌碼差、大盤崩跌），action 填 "skip"
- 進場時機：開盤（直接進）、拉回（等回測支撐）、突破（等突破壓力）
- 進場區間參考現價 ± ATR / 2
- 目標參考 VWAP + ATR 或近期壓力
- 停損參考 VWAP 下方或近期支撐

只回答 JSON，不要其他文字：
{{
  "action": "long 或 skip",
  "confidence": 0到10,
  "entry_low": 進場低點或null,
  "entry_high": 進場高點或null,
  "target_price": 目標價或null,
  "stop_loss": 停損價或null,
  "timing": "開盤 或 拉回 或 突破 或 觀望",
  "summary": "一到兩句話的當沖建議"
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
    raise ValueError("no JSON found")


def parse_daytrading_response(code: str, name: str, raw: str) -> DayTradingAnalysis:
    _skip = DayTradingAnalysis(
        code=code, name=name, action="skip", confidence=0,
        entry_low=None, entry_high=None,
        target_price=None, stop_loss=None,
        timing="觀望", summary="AI 分析失敗或資料不足",
    )
    if not raw:
        return _skip
    try:
        data = _extract_json(raw)

        action = data.get("action", "skip")
        if action not in ("long", "skip"):
            action = "skip"

        confidence = max(0, min(10, int(data.get("confidence", 0))))

        def _float(val) -> Optional[float]:
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        timing = data.get("timing", "觀望")
        if timing not in ("開盤", "拉回", "突破", "觀望"):
            timing = "觀望"

        return DayTradingAnalysis(
            code=code, name=name,
            action=action, confidence=confidence,
            entry_low=_float(data.get("entry_low")),
            entry_high=_float(data.get("entry_high")),
            target_price=_float(data.get("target_price")),
            stop_loss=_float(data.get("stop_loss")),
            timing=timing,
            summary=data.get("summary", ""),
        )
    except Exception as e:
        log.debug("parse_daytrading_response failed: %s", e)
        return _skip


# ── Main entry ────────────────────────────────────────────────────────────────

def run_daytrading_analysis(
    code: str,
    name: str,
    indicators: Optional[dict],
    chip: Optional[dict],
    market: Optional[dict],
    dt_score: int,
) -> DayTradingAnalysis:
    """呼叫 Haiku 做當沖 AI 分析。dt_score < 4 直接回傳 skip，節省 API 成本。"""
    _skip = DayTradingAnalysis(
        code=code, name=name, action="skip", confidence=0,
        entry_low=None, entry_high=None,
        target_price=None, stop_loss=None,
        timing="觀望", summary="當沖評分偏低，不建議操作",
    )

    if dt_score < _MIN_DT_SCORE:
        return _skip

    try:
        prompt = build_daytrading_prompt(code, name, indicators, chip, market, dt_score)
        raw    = call_haiku(prompt)
        return parse_daytrading_response(code, name, raw)
    except Exception as e:
        log.warning("run_daytrading_analysis failed for %s: %s", code, e)
        return _skip
