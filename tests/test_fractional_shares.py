from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from strategy_planner import (
    calc_max_lots,
    calc_max_shares,
    build_plan_prompt,
    generate_plan,
    StockPick,
    StrategyPlan,
)
from strategy_tracker import StrategyGoal

START = date(2026, 4, 1)
END   = date(2026, 4, 30)

def make_goal(capital=30_000.0) -> StrategyGoal:
    return StrategyGoal(
        target_multiplier=2.0, start_date=START, end_date=END,
        initial_capital=capital, approach="短線技術分析",
    )

CANDIDATES = [
    {"code": "2330", "name": "台積電", "close": 850.0, "change_rate": 2.1,
     "analysis": "AI 強勢，但一張 85 萬買不起", "max_lots": 0},
    {"code": "3706", "name": "神達",   "close": 25.0,  "change_rate": 1.2,
     "analysis": "低價反彈", "max_lots": 1},
]


# ── calc_max_shares ───────────────────────────────────────────────────────────

class TestCalcMaxShares:
    def test_tsmc_850_with_30k(self):
        # floor(30000 / 850) = 35 股
        assert calc_max_shares(price=850.0, capital=30_000.0) == 35

    def test_cheap_stock(self):
        # floor(30000 / 25) = 1200 股
        assert calc_max_shares(price=25.0, capital=30_000.0) == 1_200

    def test_zero_price_returns_zero(self):
        assert calc_max_shares(price=0.0, capital=30_000.0) == 0

    def test_zero_capital_returns_zero(self):
        assert calc_max_shares(price=850.0, capital=0.0) == 0

    def test_returns_integer(self):
        assert isinstance(calc_max_shares(850.0, 30_000.0), int)

    def test_more_shares_than_lots(self):
        # 零股讓你買到比整張更多
        lots   = calc_max_lots(850.0, 1_000_000.0)
        shares = calc_max_shares(850.0, 1_000_000.0)
        assert shares >= lots * 1000


# ── StockPick fractional fields ───────────────────────────────────────────────

class TestStockPickFractional:
    def test_has_is_fractional_field(self):
        p = StockPick("2330", "台積電", "buy", 0, 3, 5.0, "RSI 低", 8,
                      is_fractional=True, shares=35)
        assert p.is_fractional is True
        assert p.shares == 35

    def test_default_not_fractional(self):
        p = StockPick("3706", "神達", "buy", 1, 3, 5.0, "低價", 7)
        assert p.is_fractional is False
        assert p.shares == 0

    def test_fractional_cost(self):
        # 35 股 × 850 = 29,750
        p = StockPick("2330", "台積電", "buy", 0, 3, 5.0, "RSI", 8,
                      is_fractional=True, shares=35)
        assert p.total_cost(price=850.0) == pytest.approx(35 * 850.0)

    def test_lot_cost(self):
        # 1 張 = 1000 股 × 25 = 25,000
        p = StockPick("3706", "神達", "buy", 1, 3, 5.0, "低價", 7)
        assert p.total_cost(price=25.0) == pytest.approx(1000 * 25.0)


# ── build_plan_prompt ─────────────────────────────────────────────────────────

class TestBuildPlanPrompt:
    def test_aggressive_prompt_mentions_risk(self):
        prompt = build_plan_prompt("aggressive", make_goal(), 30_000.0, CANDIDATES)
        assert any(w in prompt for w in ("衝刺", "積極", "集中", "aggressive"))

    def test_balanced_prompt_mentions_balance(self):
        prompt = build_plan_prompt("balanced", make_goal(), 30_000.0, CANDIDATES)
        assert any(w in prompt for w in ("均衡", "分散", "平衡", "balanced"))

    def test_conservative_prompt_mentions_safety(self):
        prompt = build_plan_prompt("conservative", make_goal(), 30_000.0, CANDIDATES)
        assert any(w in prompt for w in ("保守", "穩健", "保本", "conservative"))

    def test_prompt_includes_fractional_option(self):
        prompt = build_plan_prompt("aggressive", make_goal(), 30_000.0, CANDIDATES)
        assert "零股" in prompt or "股" in prompt

    def test_prompt_includes_max_shares_for_expensive(self):
        prompt = build_plan_prompt("aggressive", make_goal(), 30_000.0, CANDIDATES)
        # 台積電 30000/850=35股
        assert "35" in prompt or "零股" in prompt

    def test_prompt_includes_max_lots_for_cheap(self):
        prompt = build_plan_prompt("balanced", make_goal(), 30_000.0, CANDIDATES)
        assert "3706" in prompt or "神達" in prompt

    def test_returns_string(self):
        prompt = build_plan_prompt("conservative", make_goal(), 30_000.0, CANDIDATES)
        assert isinstance(prompt, str) and len(prompt) > 100

    def test_three_prompts_are_different(self):
        p1 = build_plan_prompt("aggressive",   make_goal(), 30_000.0, CANDIDATES)
        p2 = build_plan_prompt("balanced",     make_goal(), 30_000.0, CANDIDATES)
        p3 = build_plan_prompt("conservative", make_goal(), 30_000.0, CANDIDATES)
        assert p1 != p2
        assert p2 != p3


# ── generate_plan (single plan) ───────────────────────────────────────────────

SINGLE_PLAN_JSON = """{
  "description": "衝刺高報酬",
  "thesis": "費半強勢，AI 題材持續",
  "macro_context": "美股多頭",
  "capital_deployed_pct": 0.9,
  "expected_return_pct": 20.0,
  "risk_note": "波動大",
  "picks": [
    {"code": "2330", "name": "台積電", "action": "buy",
     "quantity": 0, "hold_days": 3, "expected_return_pct": 8.0,
     "reason": "RSI 低點", "confidence": 8,
     "is_fractional": true, "shares": 35,
     "key_catalysts": ["AI 伺服器需求強"], "entry_logic": "RSI<30", "exit_logic": "+8% 出場"}
  ]
}"""

class TestGeneratePlan:
    @patch("strategy_planner.call_sonnet")
    def test_returns_strategy_plan(self, mock_ollama):
        mock_ollama.return_value = SINGLE_PLAN_JSON
        result = generate_plan("aggressive", make_goal(), 30_000.0, CANDIDATES)
        assert isinstance(result, StrategyPlan)
        assert result.plan_type == "aggressive"

    @patch("strategy_planner.call_sonnet")
    def test_fractional_pick_parsed(self, mock_ollama):
        mock_ollama.return_value = SINGLE_PLAN_JSON
        result = generate_plan("aggressive", make_goal(), 30_000.0, CANDIDATES)
        tsmc_pick = next((p for p in result.picks if p.code == "2330"), None)
        if tsmc_pick:
            assert tsmc_pick.is_fractional is True
            assert tsmc_pick.shares == 35

    @patch("strategy_planner.call_sonnet")
    def test_uses_correct_plan_type_prompt(self, mock_ollama):
        mock_ollama.return_value = SINGLE_PLAN_JSON
        generate_plan("conservative", make_goal(), 30_000.0, CANDIDATES)
        prompt = mock_ollama.call_args[0][0]
        assert any(w in prompt for w in ("保守", "穩健", "保本", "conservative"))

    @patch("strategy_planner.call_sonnet", side_effect=Exception("timeout"))
    def test_failure_returns_none(self, mock_ollama):
        result = generate_plan("balanced", make_goal(), 30_000.0, CANDIDATES)
        assert result is None
