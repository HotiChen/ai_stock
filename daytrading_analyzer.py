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
from dataclasses import dataclass, field
from typing import Optional

from ai_client import call_haiku

log = logging.getLogger(__name__)

_MIN_DT_SCORE = 4  # 低於此分數直接 skip，不呼叫 AI


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class DayTradingAnalysis:
    code:               str
    name:               str
    action:             str            # "long" | "skip"
    confidence:         int            # 0–10
    entry_low:          Optional[float]
    entry_high:         Optional[float]
    target_price:       Optional[float]
    stop_loss:          Optional[float]
    timing:             str            # "開盤" | "拉回" | "突破" | "觀望"
    summary:            str
    data_quality_score: int = 5        # 老薑評分：0-10
    missing_data:       list[str] = field(default_factory=list)


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_daytrading_prompt(
    code: str,
    name: str,
    indicators: Optional[dict],
    chip: Optional[dict],
    market: Optional[dict],
    dt_score: int,
    extra_context: str = "",
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

    try:
        from super_trader import get_persona
        persona = get_persona()
    except Exception:
        persona = "你是一位台股當沖交易專家。"

    extra_section = f"\n\n【Gemini 補充資料】\n{extra_context}" if extra_context else ""

    return f"""{persona}

---
股票：{code} {name}　技術評分：{dt_score}/10

【技術指標】
{tech_text}

【法人籌碼】
{chip_text}

【大盤方向】
{market_text}{extra_section}

---
請依鐵律與分析框架判斷今日是否做多，只回傳 JSON，不要其他文字：
{{
  "action": "long 或 skip",
  "confidence": 0到10,
  "entry_low": 進場低點或null,
  "entry_high": 進場高點或null,
  "price_hint": "簡短說明壓力支撐或null",
  "resistance": 近期壓力價或null,
  "support": 近期支撐價或null,
  "timing": "開盤 或 拉回 或 突破 或 觀望",
  "summary": "一句話，像老操盤手說話，要有數字",
  "data_quality_score": 0到10的整數,
  "missing_data": ["缺少的資料項目（具體說明）"]
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


def _is_valid_entry(
    entry_low: Optional[float],
    entry_high: Optional[float],
) -> bool:
    """Return True iff entry range fields are present and logically consistent."""
    if any(v is None or v <= 0 for v in (entry_low, entry_high)):
        return False
    return entry_low <= entry_high


def _calc_prices_from_atr(
    entry_price: float,
    atr: Optional[float],
    resistance: Optional[float] = None,
    support: Optional[float] = None,
) -> tuple[Optional[float], Optional[float]]:
    """用 ATR 公式計算目標價與停損，取代 LLM 給的原始數字。

    公式
    ----
    stop_loss   = entry_price - 1.5 * atr
    target_price = min(entry_price + 2.5 * atr, resistance * 0.995)  若 resistance 存在
                 = entry_price + 2.5 * atr                           若 resistance 不存在

    若 atr 不存在，回傳 (None, None)，呼叫方應保留 LLM 原值作為 fallback。
    """
    if atr is None or atr <= 0:
        return None, None

    stop_loss    = round(entry_price - 1.5 * atr, 2)
    target_price = round(entry_price + 2.5 * atr, 2)

    # 修正一：套用壓力價 cap（resistance 過低時不套用）
    if resistance is not None and resistance > 0:
        capped = round(min(target_price, resistance * 0.995), 2)
        if capped > entry_price:
            target_price = capped

    # 修正一補充：最終保證 target_price > entry_price
    if target_price <= entry_price:
        target_price = round(entry_price + 2.5 * atr, 2)

    # 修正五：若 support 存在且合理，stop_loss 不低於 support 下方 0.5%
    if support is not None and support > 0:
        support_floor = round(support * 0.995, 2)
        if stop_loss < support_floor:
            stop_loss = support_floor

    return target_price, stop_loss


def parse_daytrading_response(
    code: str,
    name: str,
    raw: str,
    indicators: Optional[dict] = None,
) -> DayTradingAnalysis:
    """Parse LLM response; target_price / stop_loss are calculated from ATR, not LLM."""
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

        el = _float(data.get("entry_low"))
        eh = _float(data.get("entry_high"))

        # LLM 不再給 target_price / stop_loss；用 ATR 公式計算
        resistance = _float(data.get("resistance"))
        support    = _float(data.get("support"))

        if action == "long" and not _is_valid_entry(el, eh):
            log.debug(
                "parse_daytrading_response: invalid entry range (%s el=%s eh=%s) → skip",
                code, el, eh,
            )
            return DayTradingAnalysis(
                code=code, name=name,
                action="skip", confidence=0,
                entry_low=None, entry_high=None,
                target_price=None, stop_loss=None,
                timing="觀望",
                summary="AI 回傳進場區間無效，已保守跳過",
            )

        # 用 ATR 計算目標 / 停損
        tp, sl = None, None
        if action == "long" and indicators and eh is not None:
            atr = _float(indicators.get("ATR"))
            entry_ref = eh  # 用進場高點作為 ATR 計算基準（保守）
            tp, sl = _calc_prices_from_atr(entry_ref, atr, resistance=resistance, support=support)
            if tp is None:
                log.debug("_calc_prices_from_atr: ATR 不存在，target/stop 為 None")

        # 修正二：ATR 不存在時，用固定百分比保底
        if action == "long":
            if sl is None and el is not None:
                sl = round(el * 0.97, 2)
            if tp is None and eh is not None:
                tp = round(eh * 1.03, 2)

            # 修正三：確保 stop_loss < entry_low（停損不落在進場區間內）
            if sl is not None and el is not None and sl >= el:
                sl = round(el * 0.97, 2)

        dqs     = int(data.get("data_quality_score") or 5)
        missing = [str(x) for x in (data.get("missing_data") or [])]

        # 老薑鐵律：資料品質 < 6 → 強制 skip
        if dqs < 6 and action == "long":
            log.info("parse_daytrading_response: %s data_quality=%d < 6 → skip", code, dqs)
            return DayTradingAnalysis(
                code=code, name=name,
                action="skip", confidence=0,
                entry_low=None, entry_high=None,
                target_price=None, stop_loss=None,
                timing="觀望",
                summary=f"資料品質不足（{dqs}/10），老薑建議跳過",
                data_quality_score=dqs,
                missing_data=missing,
            )

        return DayTradingAnalysis(
            code=code, name=name,
            action=action, confidence=confidence,
            entry_low=el, entry_high=eh,
            target_price=tp, stop_loss=sl,
            timing=timing,
            summary=data.get("summary", ""),
            data_quality_score=dqs,
            missing_data=missing,
        )
    except Exception as e:
        log.debug("parse_daytrading_response failed: %s", e)
        return _skip


# ── Opening Re-confirmation (9:05) ───────────────────────────────────────────

@dataclass
class OpeningReconfirm:
    code:               str
    name:               str
    proceed:            bool
    reason:             str
    updated_entry_low:  Optional[float]  # None = 維持 8:30 原值
    updated_entry_high: Optional[float]  # None = 維持 8:30 原值


def build_opening_reconfirm_prompt(
    code: str,
    name: str,
    dt_score: int,
    entry_low: Optional[float],
    entry_high: Optional[float],
    target_price: Optional[float],
    stop_loss: Optional[float],
    ai_summary: str,
    current_price: float,
    change_price: float,
    volume: int,
    market: dict,
) -> str:
    idx_pct = market.get("index_change_pct", 0.0)
    fp_pct  = market.get("futures_premium_pct", 0.0)

    entry_str  = f"{entry_low:,.1f}–{entry_high:,.1f}" if entry_low and entry_high else "未設定"
    target_str = f"{target_price:,.1f}" if target_price else "未設定"
    stop_str   = f"{stop_loss:,.1f}"    if stop_loss    else "未設定"

    if entry_low and entry_high:
        if current_price < entry_low:
            position_hint = f"（低於進場區間 {entry_low - current_price:.1f} 元，尚未到位）"
        elif current_price > entry_high:
            position_hint = f"（超出進場區間 {current_price - entry_high:.1f} 元，可能錯過）"
        else:
            position_hint = "（在進場區間內）"
    else:
        position_hint = ""

    return f"""你是台股當沖交易專家。以下是早上 8:30 的盤前預測，現在是 9:05（開盤 5 分鐘後），請結合大盤氣氛判斷是否繼續進場。

【8:30 盤前預測】
{code} {name}　評分 {dt_score}/10
進場區間 {entry_str}　目標 {target_str}　停損 {stop_str}
AI 結論：{ai_summary or "無"}

【9:05 開盤實況】
現價 {current_price:,.1f} {position_hint}
開盤漲跌 {change_price:+.2f} 元　成交量 {volume} 張

【大盤氣氛】
加權指數 {idx_pct:+.2f}%　台指期溢貼水 {fp_pct:+.2f}%

請綜合判斷：
1. 現價是否仍在合理進場位置
2. 大盤方向是否支持做多
3. 量能與開盤氣氛是否確認盤前預測

只回答 JSON（不要其他文字）：
{{
  "proceed": true 或 false,
  "reason": "一句話說明繼續或放棄的原因",
  "updated_entry_low": 更新的進場低點（若維持原值填 null）,
  "updated_entry_high": 更新的進場高點（若維持原值填 null）
}}"""


def run_opening_reconfirm(
    code: str,
    name: str,
    dt_score: int,
    entry_low: Optional[float],
    entry_high: Optional[float],
    target_price: Optional[float],
    stop_loss: Optional[float],
    ai_summary: str,
    current_price: float,
    change_price: float,
    volume: int,
    market: dict,
    capture: Optional[dict] = None,
) -> OpeningReconfirm:
    """9:05 開盤再確認：結合 8:30 預測 + 當前大盤氣氛，AI 判斷是否繼續進場。

    capture（可選）：呼叫端傳入空 dict，成功時會被填入
    {"prompt": ..., "raw": ..., "parsed_action": "proceed"|"skip"}，供
    ai_decision_log 落庫使用。不傳（預設 None）時行為與現狀完全相同。
    失敗（例外）不會填入 capture，代表 LLM 從未真正做出決策。
    """
    _skip = OpeningReconfirm(
        code=code, name=name, proceed=False,
        reason="AI 分析失敗，保守放棄",
        updated_entry_low=None, updated_entry_high=None,
    )
    try:
        prompt = build_opening_reconfirm_prompt(
            code, name, dt_score,
            entry_low, entry_high, target_price, stop_loss, ai_summary,
            current_price, change_price, volume, market,
        )
        raw  = call_haiku(prompt)
        data = _extract_json(raw)

        def _f(v) -> Optional[float]:
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        proceed = bool(data.get("proceed", False))

        if capture is not None:
            capture["prompt"] = prompt
            capture["raw"] = raw
            capture["parsed_action"] = "proceed" if proceed else "skip"

        return OpeningReconfirm(
            code=code, name=name,
            proceed=proceed,
            reason=str(data.get("reason", "")),
            updated_entry_low=_f(data.get("updated_entry_low")),
            updated_entry_high=_f(data.get("updated_entry_high")),
        )
    except Exception as e:
        log.warning("run_opening_reconfirm(%s) failed: %s", code, e)
        return _skip


# ── Main entry ────────────────────────────────────────────────────────────────

def run_daytrading_analysis(
    code: str,
    name: str,
    indicators: Optional[dict],
    chip: Optional[dict],
    market: Optional[dict],
    dt_score: int,
    extra_context: str = "",
    capture: Optional[dict] = None,
) -> DayTradingAnalysis:
    """呼叫 Haiku 做當沖 AI 分析。dt_score < 4 直接回傳 skip，節省 API 成本。

    capture（可選）：呼叫端傳入空 dict，成功時會被填入
    {"prompt": ..., "raw": ..., "parsed_action": ...}，供 ai_decision_log 落庫
    使用。不傳（預設 None）時行為與現狀完全相同。dt_score 太低或呼叫失敗都
    不會填入 capture，代表 LLM 從未真正做出決策。
    """
    _skip = DayTradingAnalysis(
        code=code, name=name, action="skip", confidence=0,
        entry_low=None, entry_high=None,
        target_price=None, stop_loss=None,
        timing="觀望", summary="當沖評分偏低，不建議操作",
    )

    if dt_score < _MIN_DT_SCORE:
        return _skip

    try:
        prompt = build_daytrading_prompt(
            code, name, indicators, chip, market, dt_score,
            extra_context=extra_context,
        )
        raw = call_haiku(prompt)
        result = parse_daytrading_response(code, name, raw, indicators=indicators)
        if capture is not None:
            capture["prompt"] = prompt
            capture["raw"] = raw
            capture["parsed_action"] = result.action
        return result
    except Exception as e:
        log.warning("run_daytrading_analysis failed for %s: %s", code, e)
        return _skip
