from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from strategy_planner import (
    StockPick,
    StrategyPlan,
    PlanSet,
    build_planner_prompt,
    parse_plan_response,
    generate_strategy_plans,
)
from strategy_tracker import StrategyGoal

START = date(2026, 4, 1)
END   = date(2026, 4, 30)

def make_goal(**kw) -> StrategyGoal:
    defaults = dict(
        target_multiplier=2.0,
        start_date=START,
        end_date=END,
        initial_capital=30_000.0,
        approach="短線技術分析 + AI 輔助決策",
    )
    defaults.update(kw)
    return StrategyGoal(**defaults)

SAMPLE_CANDIDATES = [
    {"code": "2330", "name": "台積電", "close": 850.0, "change_rate": 2.1,
     "analysis": "RSI 低檔反彈，法人買超，短線偏多"},
    {"code": "2454", "name": "聯發科", "close": 1100.0, "change_rate": -0.5,
     "analysis": "均線糾結，等待突破"},
    {"code": "2881", "name": "富邦金", "close": 85.0, "change_rate": 1.2,
     "analysis": "殖利率高，防禦型配置"},
]


# ── StockPick ─────────────────────────────────────────────────────────────────

class TestStockPick:
    def test_has_required_fields(self):
        p = StockPick(
            code="2330", name="台積電", action="buy",
            quantity=1, hold_days=3,
            expected_return_pct=5.0, reason="RSI 低點", confidence=8,
        )
        assert p.code == "2330"
        assert p.action in ("buy", "hold")
        assert p.hold_days > 0
        assert 0 <= p.confidence <= 10

    def test_quantity_at_least_one(self):
        p = StockPick("2330", "台積電", "buy", 1, 3, 5.0, "ok", 7)
        assert p.quantity >= 1

    def test_expected_return_can_be_negative(self):
        p = StockPick("2330", "台積電", "hold", 1, 1, -2.0, "觀望", 4)
        assert p.expected_return_pct < 0


# ── StrategyPlan ──────────────────────────────────────────────────────────────

class TestStrategyPlan:
    def _make_plan(self, plan_type="balanced") -> StrategyPlan:
        picks = [
            StockPick("2330", "台積電", "buy", 1, 3, 5.0, "RSI 低點", 8),
        ]
        return StrategyPlan(
            plan_type=plan_type,
            description="均衡策略，分散風險",
            picks=picks,
            capital_deployed_pct=0.5,
            expected_return_pct=10.0,
            risk_note="中等風險",
        )

    def test_plan_type_valid(self):
        for t in ("aggressive", "balanced", "conservative"):
            p = self._make_plan(t)
            assert p.plan_type == t

    def test_has_picks(self):
        p = self._make_plan()
        assert len(p.picks) >= 1

    def test_capital_deployed_pct_fraction(self):
        p = self._make_plan()
        assert 0.0 < p.capital_deployed_pct <= 1.0

    def test_has_description(self):
        p = self._make_plan()
        assert len(p.description) > 0


# ── PlanSet ───────────────────────────────────────────────────────────────────

class TestPlanSet:
    def _make_planset(self) -> PlanSet:
        def plan(t):
            return StrategyPlan(
                plan_type=t, description=f"{t} 策略", picks=[],
                capital_deployed_pct=0.5, expected_return_pct=5.0, risk_note="",
            )
        return PlanSet(
            aggressive=plan("aggressive"),
            balanced=plan("balanced"),
            conservative=plan("conservative"),
            generated_at=date.today(),
        )

    def test_has_three_plans(self):
        ps = self._make_planset()
        assert ps.aggressive.plan_type == "aggressive"
        assert ps.balanced.plan_type == "balanced"
        assert ps.conservative.plan_type == "conservative"

    def test_has_generated_at(self):
        ps = self._make_planset()
        assert isinstance(ps.generated_at, date)


# ── build_planner_prompt ──────────────────────────────────────────────────────

class TestBuildPlannerPrompt:
    def test_returns_string(self):
        prompt = build_planner_prompt(make_goal(), 30_000.0, SAMPLE_CANDIDATES)
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_includes_goal_multiplier(self):
        prompt = build_planner_prompt(make_goal(target_multiplier=2.0), 30_000.0, SAMPLE_CANDIDATES)
        assert "2.0" in prompt or "兩倍" in prompt or "60,000" in prompt

    def test_includes_current_capital(self):
        prompt = build_planner_prompt(make_goal(), 35_000.0, SAMPLE_CANDIDATES)
        assert "35,000" in prompt or "35000" in prompt

    def test_includes_candidate_stocks(self):
        prompt = build_planner_prompt(make_goal(), 30_000.0, SAMPLE_CANDIDATES)
        assert "2330" in prompt or "台積電" in prompt

    def test_includes_all_three_plan_types(self):
        prompt = build_planner_prompt(make_goal(), 30_000.0, SAMPLE_CANDIDATES)
        assert "aggressive" in prompt or "衝刺" in prompt
        assert "balanced" in prompt or "均衡" in prompt
        assert "conservative" in prompt or "保守" in prompt

    def test_includes_stock_analysis(self):
        prompt = build_planner_prompt(make_goal(), 30_000.0, SAMPLE_CANDIDATES)
        assert "RSI" in prompt or "法人" in prompt


# ── parse_plan_response ───────────────────────────────────────────────────────

SAMPLE_JSON = """{
  "aggressive": {
    "description": "高風險高報酬，集中資金衝刺強勢股",
    "capital_deployed_pct": 0.9,
    "expected_return_pct": 30.0,
    "risk_note": "波動大，需設停損",
    "picks": [
      {"code": "2330", "name": "台積電", "action": "buy", "quantity": 2,
       "hold_days": 3, "expected_return_pct": 8.0, "reason": "RSI 低點強彈", "confidence": 8}
    ]
  },
  "balanced": {
    "description": "均衡配置",
    "capital_deployed_pct": 0.6,
    "expected_return_pct": 15.0,
    "risk_note": "中等風險",
    "picks": [
      {"code": "2330", "name": "台積電", "action": "buy", "quantity": 1,
       "hold_days": 5, "expected_return_pct": 5.0, "reason": "基本面佳", "confidence": 7}
    ]
  },
  "conservative": {
    "description": "保守防禦",
    "capital_deployed_pct": 0.3,
    "expected_return_pct": 5.0,
    "risk_note": "低風險",
    "picks": [
      {"code": "2881", "name": "富邦金", "action": "buy", "quantity": 1,
       "hold_days": 14, "expected_return_pct": 3.0, "reason": "高殖利率", "confidence": 6}
    ]
  }
}"""

class TestParsePlanResponse:
    def test_returns_planset(self):
        result = parse_plan_response(SAMPLE_JSON)
        assert isinstance(result, PlanSet)

    def test_three_plans_parsed(self):
        result = parse_plan_response(SAMPLE_JSON)
        assert result.aggressive is not None
        assert result.balanced is not None
        assert result.conservative is not None

    def test_picks_parsed(self):
        result = parse_plan_response(SAMPLE_JSON)
        assert len(result.aggressive.picks) >= 1
        assert result.aggressive.picks[0].code == "2330"

    def test_hold_days_preserved(self):
        result = parse_plan_response(SAMPLE_JSON)
        assert result.aggressive.picks[0].hold_days == 3

    def test_invalid_json_returns_none(self):
        result = parse_plan_response("not json at all")
        assert result is None

    def test_partial_json_returns_none(self):
        result = parse_plan_response('{"aggressive": {}}')
        assert result is None


# ── generate_strategy_plans ───────────────────────────────────────────────────

class TestGenerateStrategyPlans:
    @patch("strategy_planner.call_ollama")
    def test_returns_planset(self, mock_ollama):
        mock_ollama.return_value = SAMPLE_JSON
        result = generate_strategy_plans(make_goal(), 30_000.0, SAMPLE_CANDIDATES)
        assert isinstance(result, PlanSet)

    @patch("strategy_planner.call_ollama")
    def test_all_three_variants_present(self, mock_ollama):
        mock_ollama.return_value = SAMPLE_JSON
        result = generate_strategy_plans(make_goal(), 30_000.0, SAMPLE_CANDIDATES)
        assert result.aggressive.plan_type == "aggressive"
        assert result.balanced.plan_type == "balanced"
        assert result.conservative.plan_type == "conservative"

    @patch("strategy_planner.call_ollama")
    def test_prompt_sent_to_ollama(self, mock_ollama):
        mock_ollama.return_value = SAMPLE_JSON
        generate_strategy_plans(make_goal(), 30_000.0, SAMPLE_CANDIDATES)
        assert mock_ollama.called
        prompt = mock_ollama.call_args[0][1]
        assert "2330" in prompt or "台積電" in prompt

    @patch("strategy_planner.call_ollama", side_effect=Exception("timeout"))
    def test_ollama_failure_returns_none(self, mock_ollama):
        result = generate_strategy_plans(make_goal(), 30_000.0, SAMPLE_CANDIDATES)
        assert result is None

    @patch("strategy_planner.call_ollama")
    def test_empty_candidates_still_calls_ollama(self, mock_ollama):
        mock_ollama.return_value = SAMPLE_JSON
        result = generate_strategy_plans(make_goal(), 30_000.0, [])
        assert mock_ollama.called
