from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from strategy_planner import (
    build_planner_prompt,
    build_plan_prompt,
    generate_strategy_plans,
    PlanSet,
)
from strategy_tracker import StrategyGoal
from portfolio import Position, SimulatedPortfolio

TODAY = date(2026, 5, 3)
END   = date(2026, 5, 31)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_goal(**kw) -> StrategyGoal:
    defaults = dict(
        target_multiplier=2.0, start_date=TODAY, end_date=END,
        initial_capital=50_000.0, approach="短線技術分析",
    )
    defaults.update(kw)
    return StrategyGoal(**defaults)


def _make_position(
    code="2330", name="台積電",
    qty=1, avg_cost=850.0, current_price=862.0,
    reason="RSI低檔反彈，外資連買",
    entry_date=TODAY,
) -> Position:
    return Position(
        code=code, name=name,
        quantity=qty, is_fractional=False, shares=0,
        avg_cost=avg_cost, entry_date=entry_date,
        entry_reason=reason, current_price=current_price,
    )


def _make_portfolio_with_positions() -> SimulatedPortfolio:
    p = SimulatedPortfolio(initial_capital=200_000.0)
    p.open_position("2330", "台積電", quantity=1, price=850.0,
                    is_fractional=False, shares=0,
                    reason="RSI低檔反彈，外資連買", entry_date=TODAY)
    p.open_position("2454", "聯發科", quantity=2, price=520.0,
                    is_fractional=False, shares=0,
                    reason="月線突破，籌碼集中", entry_date=TODAY)
    p.update_prices({"2330": 862.0, "2454": 498.0})
    return p


CANDIDATES = [
    {"code": "3706", "close": 25.0, "name": "神達", "change_rate": 1.2, "analysis": "低價反彈"},
]


# ── build_planner_prompt with positions ──────────────────────────────────────

class TestBuildPlannerPromptWithPositions:
    def test_accepts_positions_param(self):
        """不傳 positions 仍然正常運作（向後兼容）"""
        goal = _make_goal()
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES)
        assert isinstance(prompt, str)

    def test_prompt_includes_position_code(self):
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        assert "2330" in prompt
        assert "2454" in prompt

    def test_prompt_includes_position_name(self):
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        assert "台積電" in prompt
        assert "聯發科" in prompt

    def test_prompt_includes_avg_cost(self):
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        assert "850" in prompt   # avg_cost of 2330

    def test_prompt_includes_unrealized_pnl(self):
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        # 2330: (862-850)*1000 = +12,000
        assert "12,000" in prompt or "12000" in prompt or "+12" in prompt

    def test_prompt_includes_entry_reason(self):
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        assert "RSI低檔反彈" in prompt
        assert "月線突破" in prompt

    def test_prompt_includes_holdings_section(self):
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        assert "現有持倉" in prompt or "持倉" in prompt

    def test_prompt_suggests_position_actions(self):
        """prompt 應提示 Claude 可以對現有持倉給 add/reduce/close/hold 建議"""
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        assert any(a in prompt for a in ["add", "reduce", "close", "hold", "加碼", "減碼", "平倉"])

    def test_no_positions_shows_no_holdings_section(self):
        """空 portfolio → 不顯示持倉區段（或顯示空）"""
        goal = _make_goal()
        empty_portfolio = SimulatedPortfolio(30_000.0)
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=empty_portfolio)
        # 不應包含持倉代碼（沒有持倉）
        assert "2330" not in prompt or "現有持倉" not in prompt

    def test_none_portfolio_backward_compat(self):
        """portfolio=None 時行為與不傳一樣"""
        goal = _make_goal()
        prompt_no_arg  = build_planner_prompt(goal, 30_000.0, CANDIDATES)
        prompt_none    = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=None)
        assert prompt_no_arg == prompt_none

    def test_available_capital_shown(self):
        """可用資金（現金）要顯示在 prompt 裡"""
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        # initial 200k - 2330(850k) - 2×2454(1040k) = shouldn't matter, just check cash is shown
        prompt = build_planner_prompt(goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        cash = portfolio.get_available_capital()
        assert str(int(cash)) in prompt or f"{cash:,.0f}" in prompt


# ── build_plan_prompt with positions ─────────────────────────────────────────

class TestBuildPlanPromptWithPositions:
    def test_accepts_positions_param(self):
        goal = _make_goal()
        prompt = build_plan_prompt("balanced", goal, 30_000.0, CANDIDATES)
        assert isinstance(prompt, str)

    def test_prompt_includes_position_code(self):
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_plan_prompt("balanced", goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        assert "2330" in prompt

    def test_prompt_includes_entry_reason(self):
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_plan_prompt("aggressive", goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        assert "RSI低檔反彈" in prompt

    def test_prompt_includes_unrealized_pnl(self):
        goal = _make_goal()
        portfolio = _make_portfolio_with_positions()
        prompt = build_plan_prompt("conservative", goal, 30_000.0, CANDIDATES, portfolio=portfolio)
        assert "12,000" in prompt or "12000" in prompt or "+12" in prompt

    def test_none_portfolio_backward_compat(self):
        goal = _make_goal()
        prompt_no_arg = build_plan_prompt("balanced", goal, 30_000.0, CANDIDATES)
        prompt_none   = build_plan_prompt("balanced", goal, 30_000.0, CANDIDATES, portfolio=None)
        assert prompt_no_arg == prompt_none


# ── generate_strategy_plans with portfolio ────────────────────────────────────

SAMPLE_JSON = """{
  "aggressive": {
    "description": "加碼台積電",
    "thesis": "外資持續買超",
    "macro_context": "美股強勁",
    "capital_deployed_pct": 0.8,
    "expected_return_pct": 15.0,
    "risk_note": "波動大",
    "picks": [
      {"code": "2330", "name": "台積電", "action": "add", "quantity": 1,
       "hold_days": 3, "expected_return_pct": 8.0, "reason": "加碼", "confidence": 8,
       "switch_from": "", "key_catalysts": [], "entry_logic": "", "exit_logic": ""}
    ]
  },
  "balanced": {
    "description": "持有觀望",
    "thesis": "等待訊號",
    "macro_context": "震盪",
    "capital_deployed_pct": 0.5,
    "expected_return_pct": 5.0,
    "risk_note": "中等",
    "picks": [
      {"code": "2330", "name": "台積電", "action": "hold", "quantity": 0,
       "hold_days": 3, "expected_return_pct": 3.0, "reason": "觀望", "confidence": 6,
       "switch_from": "", "key_catalysts": [], "entry_logic": "", "exit_logic": ""}
    ]
  },
  "conservative": {
    "description": "減碼保本",
    "thesis": "獲利先落袋",
    "macro_context": "謹慎",
    "capital_deployed_pct": 0.3,
    "expected_return_pct": 3.0,
    "risk_note": "低",
    "picks": [
      {"code": "2330", "name": "台積電", "action": "reduce", "quantity": 1,
       "hold_days": 1, "expected_return_pct": 1.4, "reason": "落袋為安", "confidence": 5,
       "switch_from": "", "key_catalysts": [], "entry_logic": "", "exit_logic": ""}
    ]
  }
}"""


class TestGenerateStrategyPlansWithPortfolio:
    @patch("strategy_planner.call_sonnet")
    def test_accepts_portfolio_param(self, mock_sonnet):
        mock_sonnet.return_value = SAMPLE_JSON
        portfolio = _make_portfolio_with_positions()
        result = generate_strategy_plans(
            _make_goal(), 30_000.0, CANDIDATES, portfolio=portfolio
        )
        assert isinstance(result, PlanSet)

    @patch("strategy_planner.call_sonnet")
    def test_portfolio_positions_in_prompt(self, mock_sonnet):
        mock_sonnet.return_value = SAMPLE_JSON
        portfolio = _make_portfolio_with_positions()
        generate_strategy_plans(_make_goal(), 30_000.0, CANDIDATES, portfolio=portfolio)
        prompt = mock_sonnet.call_args[0][0]
        assert "2330" in prompt   # 持倉代碼出現在 prompt 中
        assert "RSI低檔反彈" in prompt   # entry_reason 也出現

    @patch("strategy_planner.call_sonnet")
    def test_none_portfolio_still_works(self, mock_sonnet):
        """不傳 portfolio 仍然正常（向後兼容）"""
        mock_sonnet.return_value = SAMPLE_JSON
        result = generate_strategy_plans(_make_goal(), 30_000.0, CANDIDATES)
        assert isinstance(result, PlanSet)

    @patch("strategy_planner.call_sonnet")
    def test_hold_pick_preserved_in_result(self, mock_sonnet):
        """hold action 的 pick 不被 clamp 丟棄"""
        mock_sonnet.return_value = SAMPLE_JSON
        portfolio = _make_portfolio_with_positions()
        result = generate_strategy_plans(
            _make_goal(), 30_000.0, CANDIDATES, portfolio=portfolio
        )
        hold_picks = [p for p in result.balanced.picks if p.action == "hold"]
        assert len(hold_picks) >= 1

    @patch("strategy_planner.call_sonnet")
    def test_reduce_pick_preserved_in_result(self, mock_sonnet):
        """reduce action 不被 clamp 丟棄"""
        mock_sonnet.return_value = SAMPLE_JSON
        portfolio = _make_portfolio_with_positions()
        result = generate_strategy_plans(
            _make_goal(), 30_000.0, CANDIDATES, portfolio=portfolio
        )
        reduce_picks = [p for p in result.conservative.picks if p.action == "reduce"]
        assert len(reduce_picks) >= 1

    @patch("strategy_planner.call_sonnet")
    def test_available_capital_passed_correctly(self, mock_sonnet):
        """portfolio 的可用現金要傳給 prompt，而非 current_value"""
        mock_sonnet.return_value = SAMPLE_JSON
        portfolio = _make_portfolio_with_positions()
        generate_strategy_plans(_make_goal(), 999_999.0, CANDIDATES, portfolio=portfolio)
        prompt = mock_sonnet.call_args[0][0]
        # prompt 應包含 portfolio 的實際可用資金，而非傳入的 999,999
        cash_str = f"{portfolio.get_available_capital():,.0f}"
        assert cash_str in prompt
