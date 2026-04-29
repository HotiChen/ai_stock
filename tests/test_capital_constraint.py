from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from strategy_planner import (
    calc_max_lots,
    filter_affordable_candidates,
    build_planner_prompt,
    generate_strategy_plans,
    PlanSet,
)
from strategy_tracker import StrategyGoal

START = date(2026, 4, 1)
END   = date(2026, 4, 30)

def make_goal(capital=30_000.0, **kw) -> StrategyGoal:
    return StrategyGoal(
        target_multiplier=2.0, start_date=START, end_date=END,
        initial_capital=capital, approach="短線技術分析",
        **kw,
    )


# ── calc_max_lots ─────────────────────────────────────────────────────────────

class TestCalcMaxLots:
    def test_tsmc_850_with_30k_is_zero(self):
        # 850 * 1000 = 850,000 > 30,000
        assert calc_max_lots(price=850.0, capital=30_000.0) == 0

    def test_cheap_stock_10_with_30k(self):
        # 10 * 1000 = 10,000 → floor(30000/10000) = 3
        assert calc_max_lots(price=10.0, capital=30_000.0) == 3

    def test_exactly_one_lot(self):
        # 30 * 1000 = 30,000 == capital → 1 lot
        assert calc_max_lots(price=30.0, capital=30_000.0) == 1

    def test_zero_price_returns_zero(self):
        assert calc_max_lots(price=0.0, capital=30_000.0) == 0

    def test_zero_capital_returns_zero(self):
        assert calc_max_lots(price=50.0, capital=0.0) == 0

    def test_large_capital(self):
        # 500 * 1000 = 500,000; capital=1,000,000 → 2 lots
        assert calc_max_lots(price=500.0, capital=1_000_000.0) == 2

    def test_partial_lot_truncated(self):
        # 25 * 1000 = 25,000; capital=30,000 → floor(30000/25000) = 1
        assert calc_max_lots(price=25.0, capital=30_000.0) == 1


# ── filter_affordable_candidates ──────────────────────────────────────────────

class TestFilterAffordableCandidates:
    def _candidates(self):
        return [
            {"code": "2330", "name": "台積電",  "close": 850.0,  "change_rate": 2.1, "analysis": "強勢"},
            {"code": "2881", "name": "富邦金",  "close": 85.0,   "change_rate": 0.5, "analysis": "穩健"},
            {"code": "3706", "name": "神達",    "close": 25.0,   "change_rate": 1.2, "analysis": "低價"},
            {"code": "9999", "name": "超貴股",  "close": 5000.0, "change_rate": 0.1, "analysis": "貴"},
        ]

    def test_filters_out_unaffordable(self):
        # 連零股都買不起（price > capital）才排除
        # 9999: 5000/股，30,000/5000=6股，零股買得起，所以不應被排除
        # 若要測試真正買不起，需要 price > capital
        candidates = [
            {"code": "OVER", "name": "買不起", "close": 50_000.0, "change_rate": 0.0, "analysis": ""},
            {"code": "3706", "name": "神達",   "close": 25.0,     "change_rate": 1.2, "analysis": "低價"},
        ]
        result = filter_affordable_candidates(candidates, capital=30_000.0)
        codes = [c["code"] for c in result]
        assert "OVER" not in codes   # 50,000/股 > 30,000元 → 連1股都買不起
        assert "3706" in codes

    def test_keeps_affordable(self):
        result = filter_affordable_candidates(self._candidates(), capital=30_000.0)
        codes = [c["code"] for c in result]
        assert "3706" in codes      # 25*1000=25,000 < 30,000 → 整張可買
        # 2330(850/股)、2881(85/股) 整張買不起，但零股買得起，仍保留為 is_fractional_only
        assert "2330" in codes
        assert "2881" in codes

    def test_fractional_only_flag(self):
        result = filter_affordable_candidates(self._candidates(), capital=30_000.0)
        by_code = {c["code"]: c for c in result}
        assert by_code["3706"]["is_fractional_only"] is False   # 整張買得起
        assert by_code["2330"]["is_fractional_only"] is True    # 只能零股
        assert by_code["2881"]["is_fractional_only"] is True    # 只能零股

    def test_adds_max_lots_field(self):
        result = filter_affordable_candidates(self._candidates(), capital=30_000.0)
        for c in result:
            assert "max_lots" in c
            assert "max_shares" in c

    def test_empty_candidates_returns_empty(self):
        assert filter_affordable_candidates([], capital=30_000.0) == []

    def test_all_unaffordable_returns_empty(self):
        # 每股 50,000 元 → 連 1 股都買不起（capital=30,000）
        candidates = [{"code": "XXXX", "name": "超貴", "close": 50_000.0,
                       "change_rate": 0.0, "analysis": "貴"}]
        result = filter_affordable_candidates(candidates, capital=30_000.0)
        assert result == []

    def test_capital_100k_can_afford_tsmc(self):
        candidates = [{"code": "2330", "name": "台積電", "close": 85.0,
                       "change_rate": 2.1, "analysis": "強勢"}]
        result = filter_affordable_candidates(candidates, capital=100_000.0)
        assert len(result) == 1
        assert result[0]["max_lots"] == 1   # 85*1000=85k, floor(100k/85k)=1


# ── build_planner_prompt capital awareness ────────────────────────────────────

class TestBuildPlannerPromptCapital:
    def test_prompt_includes_capital(self):
        goal = make_goal(capital=30_000.0)
        candidates = [{"code": "3706", "name": "神達", "close": 25.0,
                       "change_rate": 1.2, "analysis": "低價", "max_lots": 1}]
        prompt = build_planner_prompt(goal, 30_000.0, candidates)
        assert "30,000" in prompt or "30000" in prompt

    def test_prompt_includes_max_lots(self):
        goal = make_goal(capital=30_000.0)
        candidates = [{"code": "3706", "name": "神達", "close": 25.0,
                       "change_rate": 1.2, "analysis": "低價", "max_lots": 1}]
        prompt = build_planner_prompt(goal, 30_000.0, candidates)
        assert "max_lots" in prompt or "最多" in prompt or "1 張" in prompt

    def test_prompt_warns_about_lot_constraint(self):
        goal = make_goal(capital=30_000.0)
        candidates = [{"code": "3706", "name": "神達", "close": 25.0,
                       "change_rate": 1.2, "analysis": "低價", "max_lots": 1}]
        prompt = build_planner_prompt(goal, 30_000.0, candidates)
        assert "張" in prompt and ("1000" in prompt or "一張" in prompt or "lot" in prompt.lower() or "上限" in prompt)


# ── generate_strategy_plans capital filtering ─────────────────────────────────

CHEAP_SAMPLE_JSON = """{
  "aggressive": {
    "description": "低價股衝刺",
    "thesis": "低價股波動大",
    "macro_context": "市場震盪",
    "capital_deployed_pct": 0.9,
    "expected_return_pct": 20.0,
    "risk_note": "波動大",
    "picks": [
      {"code": "3706", "name": "神達", "action": "buy", "quantity": 1,
       "hold_days": 3, "expected_return_pct": 8.0, "reason": "低價反彈", "confidence": 7,
       "key_catalysts": ["低價反彈"], "entry_logic": "RSI低", "exit_logic": "漲10%出場"}
    ]
  },
  "balanced": {
    "description": "均衡低價",
    "thesis": "穩健配置",
    "macro_context": "市場震盪",
    "capital_deployed_pct": 0.6,
    "expected_return_pct": 10.0,
    "risk_note": "中等",
    "picks": [
      {"code": "3706", "name": "神達", "action": "buy", "quantity": 1,
       "hold_days": 5, "expected_return_pct": 5.0, "reason": "穩健", "confidence": 6,
       "key_catalysts": [], "entry_logic": "", "exit_logic": ""}
    ]
  },
  "conservative": {
    "description": "保守低價",
    "thesis": "保本優先",
    "macro_context": "謹慎",
    "capital_deployed_pct": 0.3,
    "expected_return_pct": 5.0,
    "risk_note": "低",
    "picks": [
      {"code": "3706", "name": "神達", "action": "buy", "quantity": 1,
       "hold_days": 10, "expected_return_pct": 3.0, "reason": "低風險", "confidence": 5,
       "key_catalysts": [], "entry_logic": "", "exit_logic": ""}
    ]
  }
}"""

class TestGenerateStrategyPlansCapital:
    @patch("strategy_planner.call_sonnet")
    def test_filters_unaffordable_before_sending(self, mock_ollama):
        mock_ollama.return_value = CHEAP_SAMPLE_JSON
        candidates = [
            {"code": "2330", "name": "台積電", "close": 850.0, "change_rate": 2.1, "analysis": "強勢"},
            {"code": "3706", "name": "神達",   "close": 25.0,  "change_rate": 1.2, "analysis": "低價"},
        ]
        generate_strategy_plans(make_goal(capital=30_000.0), 30_000.0, candidates)
        prompt = mock_ollama.call_args[0][0]
        # 台積電 should not be in prompt (can't afford), 神達 should be
        assert "3706" in prompt or "神達" in prompt
        assert "2330" not in prompt or "買不起" in prompt or "max_lots: 0" in prompt

    @patch("strategy_planner.call_sonnet")
    def test_quantity_in_plan_respects_max_lots(self, mock_ollama):
        mock_ollama.return_value = CHEAP_SAMPLE_JSON
        candidates = [{"code": "3706", "name": "神達", "close": 25.0,
                       "change_rate": 1.2, "analysis": "低價"}]
        result = generate_strategy_plans(make_goal(capital=30_000.0), 30_000.0, candidates)
        assert isinstance(result, PlanSet)
        for pick in result.aggressive.picks:
            max_lots = calc_max_lots(25.0, 30_000.0)
            assert pick.quantity <= max_lots

    @patch("strategy_planner.call_sonnet")
    def test_all_candidates_unaffordable_still_calls_ollama(self, mock_ollama):
        mock_ollama.return_value = CHEAP_SAMPLE_JSON
        candidates = [{"code": "2330", "name": "台積電", "close": 850.0,
                       "change_rate": 2.1, "analysis": "強勢"}]
        generate_strategy_plans(make_goal(capital=30_000.0), 30_000.0, candidates)
        assert mock_ollama.called
