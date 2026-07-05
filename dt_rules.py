"""dt_rules.py — 當沖確定性決策規則（llm_mode="advisor" 的核心）

背景
----
LLM（Haiku）目前在兩處是決策核心：8:30 選股 action long/skip + 進場區間、
9:05 開盤再確認 proceed true/false。缺點：不可重現、模型改版行為漂移、
績效無法歸因（賠錢時分不清是選股爛還是 LLM 過濾爛）。

本模組提供「零 LLM、純函數、可完整單元測試」的規則版本，取代 LLM 做決策
（llm_mode="advisor"）。所有門檻集中定義在模組層，方便之後依實盤數據調整，
也方便 dt_counterfactual.py 之類的反事實分析工具引用同一組門檻。

三個函數對應既有 LLM 流程的三個決策點：
  - rule_decide_action     對應 daytrading_analyzer.run_daytrading_analysis 的 action
  - rule_entry_range       對應 daytrading_analyzer.parse_daytrading_response 的 entry_low/high
  - rule_opening_reconfirm 對應 daytrading_analyzer.run_opening_reconfirm 的 proceed
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


# ══════════════════════════════════════════════════════════════════════════════
# 門檻常數（之後可依實盤數據調整；集中放這裡方便回測/反事實分析引用）
# ══════════════════════════════════════════════════════════════════════════════

# ── rule_decide_action ──
MIN_DT_SCORE_FOR_LONG = 6
"""技術評分門檻。低於此分數的候選歷史勝率不足，直接排除，理由同
daytrading_report._MIN_DT_SCORE（qualified 門檻 4）再收斂一層，只有更強訊號才做多。"""

MIN_VOLUME_RATIO = 1.5
"""量比門檻。當沖需要足夠成交量才有出場流動性；量比 < 1.5 代表今日買氣
未明顯放大，容易進場後量縮出不掉。indicators 缺資料時不擋（資料源本身
不穩定不該因此錯殺訊號），但在 reason 中註記，供事後歸因。"""

MIN_INDEX_CHANGE_PCT_FOR_LONG = -0.5
"""大盤門檻。大盤跌幅超過 0.5% 時個股逆勢做多當沖風險偏高（順勢原則）。
market 缺資料時不擋，同上在 reason 中註記。"""

RSI_BULLISH_LOW = 45
RSI_BULLISH_HIGH = 75
"""多頭排列 / RSI 區間擇一即可。RSI 45–75 是「未超賣、未過熱」的健康區間；
配合 bullish_alignment（均線多頭排列）擇一成立，避免錯過均線未及排列
但動能健康的個股，也避免追高（RSI > 75 過熱）或摸刀（RSI < 45 偏弱）。"""

# ── rule_entry_range ──
ENTRY_LOW_FACTOR = 0.997
ENTRY_HIGH_FACTOR = 1.003
"""以現價與 VWAP 兩者的較低/較高值為錨，各自再留 0.3% 緩衝，形成一個
「兩個參考價中間偏保守」的進場帶：entry_low 用較低值再往下留緩衝（更好的
進場價才觸價），entry_high 用較高值再往上留緩衝（避免區間過窄追不到）。"""

# ── rule_opening_reconfirm ──
RECONFIRM_LOW_TOLERANCE = 0.99
RECONFIRM_HIGH_TOLERANCE = 1.02
"""開盤實際成交價不會剛好落在 8:30 預估的區間內，容忍 entry_low 下方 1%、
entry_high 上方 2%（開盤上衝比下探更容易錯過，故上緣容忍較寬）。"""

RECONFIRM_MIN_INDEX_CHANGE_PCT = -0.8
"""9:05 大盤門檻比 8:30（-0.5%）更嚴格：已經是開盤實況，若大盤仍偏弱
（< -0.8%），代表今日系統性風險偏高，即使個股訊號成立也應放棄。"""

RECONFIRM_MAX_OPENING_DROP_PCT = 1.5
"""開盤跌幅門檻（%）。change_price（開盤絕對漲跌價）換算成現價的 1.5%
作為門檻——開盤下跌超過現價 1.5% 代表開盤氣氛與盤前預測不符，放棄進場。"""


# ══════════════════════════════════════════════════════════════════════════════
# 資料模型
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RuleDecision:
    action: str   # "long" | "skip"
    reason: str


@dataclass
class RuleReconfirm:
    proceed: bool
    reason: str


# ══════════════════════════════════════════════════════════════════════════════
# 內部小工具
# ══════════════════════════════════════════════════════════════════════════════

def _field(pos: Union[dict, object], name: str) -> Optional[float]:
    """相容 dataclass（DaytradingPosition）與 dict 兩種 pos 型別。"""
    if isinstance(pos, dict):
        return pos.get(name)
    return getattr(pos, name, None)


def _to_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
# rule_decide_action — 8:30 是否做多
# ══════════════════════════════════════════════════════════════════════════════

def rule_decide_action(
    dt_score: int,
    indicators: Optional[dict],
    market: Optional[dict],
) -> RuleDecision:
    """確定性版本的 8:30 做多判斷，取代 LLM。

    action == "long" 若且唯若全部成立：
      1. dt_score >= MIN_DT_SCORE_FOR_LONG
      2. 量比 >= MIN_VOLUME_RATIO（indicators 缺資料不擋，僅註記）
      3. 大盤 index_change_pct > MIN_INDEX_CHANGE_PCT_FOR_LONG（market 缺資料不擋，僅註記）
      4. bullish_alignment 為 True，或 RSI 落在 [RSI_BULLISH_LOW, RSI_BULLISH_HIGH]

    reason 會列出所有未過的條件，供人工/反事實分析追溯。
    """
    notes: list[str] = []
    unmet: list[str] = []

    # 1. dt_score
    score_ok = dt_score >= MIN_DT_SCORE_FOR_LONG
    if not score_ok:
        unmet.append(f"dt_score {dt_score} < {MIN_DT_SCORE_FOR_LONG}")

    # 2. 量比
    vr = indicators.get("volume_ratio") if indicators else None
    if vr is None:
        notes.append("量比資料缺，不擋")
        volume_ok = True
    else:
        vr = _to_float(vr)
        volume_ok = vr is not None and vr >= MIN_VOLUME_RATIO
        if not volume_ok:
            unmet.append(f"量比 {vr} < {MIN_VOLUME_RATIO}")

    # 3. 大盤
    idx = market.get("index_change_pct") if market else None
    if idx is None:
        notes.append("大盤資料缺，不擋")
        market_ok = True
    else:
        idx = _to_float(idx)
        market_ok = idx is not None and idx > MIN_INDEX_CHANGE_PCT_FOR_LONG
        if not market_ok:
            unmet.append(f"大盤 {idx:+.2f}% <= {MIN_INDEX_CHANGE_PCT_FOR_LONG}%")

    # 4. 多頭排列 或 RSI 區間
    bull = bool(indicators.get("bullish_alignment")) if indicators else False
    rsi = _to_float(indicators.get("RSI")) if indicators else None
    rsi_ok = rsi is not None and RSI_BULLISH_LOW <= rsi <= RSI_BULLISH_HIGH
    align_ok = bull or rsi_ok
    if not align_ok:
        unmet.append(
            f"非多頭排列且 RSI（{rsi if rsi is not None else '缺'}）"
            f"不在 {RSI_BULLISH_LOW}-{RSI_BULLISH_HIGH} 區間"
        )

    action = "long" if (score_ok and volume_ok and market_ok and align_ok) else "skip"

    reason_parts = list(notes)
    if unmet:
        reason_parts.append("未過條件：" + "；".join(unmet))
    else:
        reason_parts.append("全部條件通過")
    reason = "；".join(reason_parts)

    return RuleDecision(action=action, reason=reason)


# ══════════════════════════════════════════════════════════════════════════════
# rule_entry_range — 8:30 進場區間
# ══════════════════════════════════════════════════════════════════════════════

def rule_entry_range(
    indicators: Optional[dict],
) -> tuple[Optional[float], Optional[float]]:
    """以現價與 VWAP 定錨計算進場區間。資料缺回傳 (None, None)。"""
    if not indicators:
        return None, None

    price = _to_float(indicators.get("current_price"))
    vwap = _to_float(indicators.get("VWAP"))
    if price is None or vwap is None or price <= 0 or vwap <= 0:
        return None, None

    entry_low = round(min(price, vwap) * ENTRY_LOW_FACTOR, 2)
    entry_high = round(max(price, vwap) * ENTRY_HIGH_FACTOR, 2)
    return entry_low, entry_high


# ══════════════════════════════════════════════════════════════════════════════
# rule_opening_reconfirm — 9:05 是否繼續進場
# ══════════════════════════════════════════════════════════════════════════════

def rule_opening_reconfirm(
    pos: Union[dict, object],
    current_price: float,
    change_price: float,
    volume: int,
    market: Optional[dict],
) -> RuleReconfirm:
    """確定性版本的 9:05 開盤再確認，取代 LLM。

    proceed 若且唯若全部成立：
      1. current_price > 0
      2. current_price 落在 [entry_low * RECONFIRM_LOW_TOLERANCE,
         entry_high * RECONFIRM_HIGH_TOLERANCE]（進場區間缺失 → 直接 False）
      3. 大盤 index_change_pct > RECONFIRM_MIN_INDEX_CHANGE_PCT
      4. 開盤未跌破：change_price > -RECONFIRM_MAX_OPENING_DROP_PCT% 對應現價

    volume 目前不參與門檻（保留參數是為了與既有 run_opening_reconfirm 呼叫
    介面一致，未來若要加量能門檻可直接擴充，不須改呼叫端）。
    """
    reasons: list[str] = []

    if current_price is None or current_price <= 0:
        return RuleReconfirm(proceed=False, reason="現價無效（<= 0）")

    entry_low = _to_float(_field(pos, "entry_low"))
    entry_high = _to_float(_field(pos, "entry_high"))
    if entry_low is None or entry_high is None or entry_low <= 0 or entry_high <= 0:
        reasons.append("進場區間缺失")
        range_ok = False
    else:
        low_bound = entry_low * RECONFIRM_LOW_TOLERANCE
        high_bound = entry_high * RECONFIRM_HIGH_TOLERANCE
        range_ok = low_bound <= current_price <= high_bound
        if not range_ok:
            reasons.append(
                f"現價 {current_price:.2f} 不在容忍區間 {low_bound:.2f}–{high_bound:.2f}"
            )

    idx = _to_float(market.get("index_change_pct")) if market else None
    market_ok = idx is not None and idx > RECONFIRM_MIN_INDEX_CHANGE_PCT
    if not market_ok:
        reasons.append(
            f"大盤（{idx if idx is not None else '缺'}）未過門檻 "
            f"{RECONFIRM_MIN_INDEX_CHANGE_PCT}%"
        )

    drop_threshold = -RECONFIRM_MAX_OPENING_DROP_PCT / 100.0 * current_price
    change_ok = (change_price or 0.0) > drop_threshold
    if not change_ok:
        reasons.append(f"開盤跌幅 {change_price:+.2f} 超過門檻")

    proceed = range_ok and market_ok and change_ok
    reason = "；".join(reasons) if reasons else "全部條件通過"
    return RuleReconfirm(proceed=proceed, reason=reason)
