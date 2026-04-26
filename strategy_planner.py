from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import config
from news_agent import call_ollama
from strategy_tracker import StrategyGoal


def _extract_json(raw: str) -> dict:
    """Try json.loads first; if that fails, find the outermost {...} block."""
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        return json.loads(m.group())
    raise ValueError("no JSON found")


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class StockPick:
    code:                str
    name:                str
    action:              str    # "buy" | "hold"
    quantity:            int
    hold_days:           int
    expected_return_pct: float
    reason:              str
    confidence:          int    # 0–10
    key_catalysts:       list[str] = field(default_factory=list)  # 具體催化劑清單
    entry_logic:         str = ""   # 進場邏輯（技術面 + 基本面）
    exit_logic:          str = ""   # 出場邏輯（目標價 / 停損條件）


@dataclass
class StrategyPlan:
    plan_type:            str   # "aggressive" | "balanced" | "conservative"
    description:          str
    picks:                list[StockPick]
    capital_deployed_pct: float
    expected_return_pct:  float
    risk_note:            str
    thesis:               str = ""  # 整體策略論述（總體環境 + 選股邏輯）
    macro_context:        str = ""  # 總體環境摘要（美股 / 政策 / 產業趨勢）


@dataclass
class PlanSet:
    aggressive:   StrategyPlan
    balanced:     StrategyPlan
    conservative: StrategyPlan
    generated_at: date


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_planner_prompt(
    goal: StrategyGoal,
    current_value: float,
    candidates: list[dict],
    market_summary: str = "",
) -> str:
    days_left = (goal.end_date - date.today()).days
    target_pnl = goal.target_value - current_value

    stocks_text = ""
    for c in candidates:
        analysis = c.get("analysis") or "無分析"
        stocks_text += (
            f"- {c['code']} {c.get('name', '')}　"
            f"現價 {c.get('close', 0):,.1f}　"
            f"漲跌 {c.get('change_rate', 0):+.1f}%　"
            f"分析：{analysis}\n"
        )
    if not stocks_text:
        stocks_text = "（無候選股票，請根據一般台股市場判斷合適標的）\n"

    market_section = f"\n=== 總體環境 ===\n{market_summary}\n" if market_summary else ""

    return f"""你是一位有野心的台股投資策略師，擅長把總體環境、產業趨勢、個股催化劑整合成具體可執行的投資計劃。

=== 投資目標 ===
初始本金：{goal.initial_capital:,.0f} 元
目前資產：{current_value:,.0f} 元
目標資產：{goal.target_value:,.0f} 元（{goal.target_multiplier}x）
還需獲利：{target_pnl:,.0f} 元
剩餘天數：{days_left} 天
策略方向：{goal.approach}
{market_section}
=== AI 分析過的候選股票 ===
{stocks_text}
=== 任務 ===
生成三套計劃（aggressive 衝刺 / balanced 均衡 / conservative 保守）。

**重要：每套計劃都必須寫出清楚的投資論述，包含：**
- 為什麼現在進場？（總體環境、美股影響、政策利多）
- 為什麼選這支股票？（訂單能見度、法人動向、產業趨勢）
- 何時出場？（目標價邏輯、停損條件）

只回答 JSON，不要其他文字：
{{
  "aggressive": {{
    "description": "一句話說明特色",
    "thesis": "詳細整體論述：總體環境如何、為何現在是好時機、選股邏輯是什麼（3到5句）",
    "macro_context": "總體環境摘要：美股走勢、Fed 政策、台灣/美國相關政策、產業趨勢",
    "capital_deployed_pct": 0到1,
    "expected_return_pct": 整體預期報酬百分比,
    "risk_note": "主要風險提示",
    "picks": [
      {{
        "code": "股票代碼",
        "name": "股票名稱",
        "action": "buy",
        "quantity": 張數整數,
        "hold_days": 持有天數,
        "expected_return_pct": 預期報酬百分比,
        "reason": "一句話核心理由",
        "confidence": 0到10,
        "key_catalysts": ["催化劑1（例：法人連買3日）", "催化劑2（例：AI伺服器訂單能見度至2027）", "催化劑3"],
        "entry_logic": "進場邏輯：技術面在哪個位置進？基本面支撐是什麼？",
        "exit_logic": "出場邏輯：目標價是多少？跌破哪裡停損？持有幾天後重新評估？"
      }}
    ]
  }},
  "balanced": {{ 同上格式 }},
  "conservative": {{ 同上格式 }}
}}"""


# ── Parser ────────────────────────────────────────────────────────────────────

def _parse_picks(raw_picks: list[dict]) -> list[StockPick]:
    picks = []
    for p in raw_picks:
        try:
            picks.append(StockPick(
                code=str(p["code"]),
                name=str(p.get("name", p["code"])),
                action=p.get("action", "buy"),
                quantity=max(1, int(p.get("quantity", 1))),
                hold_days=max(1, int(p.get("hold_days", 1))),
                expected_return_pct=float(p.get("expected_return_pct", 0.0)),
                reason=str(p.get("reason", "")),
                key_catalysts=p.get("key_catalysts", []),
                entry_logic=str(p.get("entry_logic", "")),
                exit_logic=str(p.get("exit_logic", "")),
                confidence=max(0, min(10, int(p.get("confidence", 5)))),
            ))
        except Exception:
            continue
    return picks


def _parse_one(data: dict, plan_type: str) -> Optional[StrategyPlan]:
    try:
        return StrategyPlan(
            plan_type=plan_type,
            description=str(data.get("description", "")),
            picks=_parse_picks(data.get("picks", [])),
            capital_deployed_pct=float(data.get("capital_deployed_pct", 0.5)),
            expected_return_pct=float(data.get("expected_return_pct", 0.0)),
            risk_note=str(data.get("risk_note", "")),
            thesis=str(data.get("thesis", "")),
            macro_context=str(data.get("macro_context", "")),
        )
    except Exception:
        return None


def parse_plan_response(raw: str) -> Optional[PlanSet]:
    try:
        data = _extract_json(raw)
        agg  = _parse_one(data["aggressive"],   "aggressive")
        bal  = _parse_one(data["balanced"],     "balanced")
        con  = _parse_one(data["conservative"], "conservative")
        if agg is None or bal is None or con is None:
            return None
        return PlanSet(
            aggressive=agg,
            balanced=bal,
            conservative=con,
            generated_at=date.today(),
        )
    except Exception:
        return None


# ── Main entry ────────────────────────────────────────────────────────────────

def generate_strategy_plans(
    goal: StrategyGoal,
    current_value: float,
    candidates: list[dict],
) -> Optional[PlanSet]:
    prompt = build_planner_prompt(goal, current_value, candidates)
    try:
        raw = call_ollama(config.DECISION_MODEL, prompt, timeout=120)
        return parse_plan_response(raw)
    except Exception:
        return None
