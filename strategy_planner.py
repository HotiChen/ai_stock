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


@dataclass
class StrategyPlan:
    plan_type:            str   # "aggressive" | "balanced" | "conservative"
    description:          str
    picks:                list[StockPick]
    capital_deployed_pct: float
    expected_return_pct:  float
    risk_note:            str


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
        stocks_text = "（無候選股票，請根據一般台股市場判斷）\n"

    return f"""你是一位台股投資策略師。請根據以下資訊，生成三套可執行的投資計劃。

=== 投資目標 ===
初始本金：{goal.initial_capital:,.0f} 元
目前資產：{current_value:,.0f} 元
目標資產：{goal.target_value:,.0f} 元（{goal.target_multiplier}x）
還需獲利：{target_pnl:,.0f} 元
剩餘天數：{days_left} 天
策略方向：{goal.approach}

=== AI 分析過的候選股票 ===
{stocks_text}
=== 任務 ===
請生成三套計劃：
- aggressive（衝刺版）：集中資金、短線操作，追求最大報酬，承受較高風險
- balanced（均衡版）：分散配置、中短線，平衡風險與報酬
- conservative（保守版）：少量資金、長線或高殖利率股，穩健保本優先

每支股票需指定：持有天數、預期報酬%、買幾張（考量本金限制）。

只回答 JSON，不要其他文字：
{{
  "aggressive": {{
    "description": "一句話說明此版本特色",
    "capital_deployed_pct": 0到1的小數,
    "expected_return_pct": 預期總報酬百分比,
    "risk_note": "風險提示",
    "picks": [
      {{"code": "股票代碼", "name": "股票名稱", "action": "buy",
        "quantity": 張數整數, "hold_days": 持有天數,
        "expected_return_pct": 單股預期報酬%, "reason": "繁體中文理由", "confidence": 0到10}}
    ]
  }},
  "balanced": {{ ... }},
  "conservative": {{ ... }}
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
