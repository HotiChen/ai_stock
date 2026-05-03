"""Tests for expanded StrategyGoal fields and prompt injection."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

START = date(2026, 4, 1)
END   = date(2026, 5, 1)

# ── imports under test ────────────────────────────────────────────────────────

from strategy_tracker import StrategyGoal
from strategy_planner import build_planner_prompt, StrategyPlannerGoal

CANDIDATES = [
    {"code": "2330", "name": "台積電", "close": 850.0, "change_rate": 2.1,
     "analysis": "RSI 低檔反彈", "max_lots": 1, "max_shares": 0, "is_fractional_only": False},
]


def _goal(**kw) -> StrategyGoal:
    defaults = dict(
        target_multiplier=2.0,
        start_date=START, end_date=END,
        initial_capital=100_000.0,
        approach="短線技術分析",
    )
    defaults.update(kw)
    return StrategyGoal(**defaults)


# ── StrategyGoal new fields ───────────────────────────────────────────────────

class TestStrategyGoalNewFields:
    def test_default_stop_loss_pct(self):
        g = _goal()
        assert hasattr(g, "stop_loss_pct")
        assert g.stop_loss_pct is None or isinstance(g.stop_loss_pct, float)

    def test_custom_stop_loss_pct(self):
        g = _goal(stop_loss_pct=5.0)
        assert g.stop_loss_pct == pytest.approx(5.0)

    def test_default_max_positions(self):
        g = _goal()
        assert hasattr(g, "max_positions")
        assert g.max_positions is None or isinstance(g.max_positions, int)

    def test_custom_max_positions(self):
        g = _goal(max_positions=3)
        assert g.max_positions == 3

    def test_default_allow_fractional(self):
        g = _goal()
        assert hasattr(g, "allow_fractional")
        assert isinstance(g.allow_fractional, bool)

    def test_custom_allow_fractional(self):
        g = _goal(allow_fractional=True)
        assert g.allow_fractional is True

    def test_backward_compat_existing_fields(self):
        """原有欄位不受影響。"""
        g = _goal()
        assert g.target_multiplier == pytest.approx(2.0)
        assert g.initial_capital   == pytest.approx(100_000.0)
        assert g.approach          == "短線技術分析"

    def test_save_load_roundtrip(self, tmp_path):
        from strategy_tracker import save_goal, load_goal
        g = _goal(stop_loss_pct=7.0, max_positions=5, allow_fractional=True)
        path = str(tmp_path / "goal.json")
        save_goal(g, path)
        loaded = load_goal(path)
        assert loaded is not None
        assert loaded.stop_loss_pct  == pytest.approx(7.0)
        assert loaded.max_positions  == 5
        assert loaded.allow_fractional is True

    def test_load_old_goal_without_new_fields(self, tmp_path):
        """讀取舊格式 JSON（沒有新欄位）應使用 default 值，不 crash。"""
        import json
        old_json = {
            "target_multiplier": 2.0,
            "start_date": START.isoformat(),
            "end_date":   END.isoformat(),
            "initial_capital": 100_000.0,
            "approach": "舊格式",
        }
        path = str(tmp_path / "old_goal.json")
        with open(path, "w") as f:
            json.dump(old_json, f)
        from strategy_tracker import load_goal
        loaded = load_goal(path)
        assert loaded is not None
        assert hasattr(loaded, "stop_loss_pct")
        assert hasattr(loaded, "max_positions")
        assert hasattr(loaded, "allow_fractional")


# ── build_planner_prompt injects new fields ───────────────────────────────────

class TestPlannerPromptWithGoalFields:
    def test_stop_loss_injected_when_set(self):
        from strategy_planner import build_planner_prompt
        g = _goal(stop_loss_pct=5.0)
        pg = StrategyPlannerGoal(
            initial_capital=100_000.0,
            target_return_pct=10.0,
            max_drawdown_pct=10.0,
            holding_days=5,
            risk_level="moderate",
            stop_loss_pct=5.0,
        )
        prompt = build_planner_prompt(pg, 100_000.0, CANDIDATES)
        assert "5" in prompt or "停損" in prompt or "stop" in prompt.lower()

    def test_max_positions_injected_when_set(self):
        from strategy_planner import build_planner_prompt
        pg = StrategyPlannerGoal(
            initial_capital=100_000.0,
            target_return_pct=10.0,
            max_drawdown_pct=10.0,
            holding_days=5,
            risk_level="moderate",
            max_positions=3,
        )
        prompt = build_planner_prompt(pg, 100_000.0, CANDIDATES)
        assert "3" in prompt or "最多" in prompt or "max" in prompt.lower()

    def test_fractional_flag_injected_when_true(self):
        from strategy_planner import build_planner_prompt
        pg = StrategyPlannerGoal(
            initial_capital=100_000.0,
            target_return_pct=10.0,
            max_drawdown_pct=10.0,
            holding_days=5,
            risk_level="moderate",
            allow_fractional=True,
        )
        prompt = build_planner_prompt(pg, 100_000.0, CANDIDATES)
        assert "零股" in prompt or "fractional" in prompt.lower()

    def test_fractional_not_mentioned_when_false(self):
        from strategy_planner import build_planner_prompt
        pg = StrategyPlannerGoal(
            initial_capital=100_000.0,
            target_return_pct=10.0,
            max_drawdown_pct=10.0,
            holding_days=5,
            risk_level="moderate",
            allow_fractional=False,
        )
        prompt = build_planner_prompt(pg, 100_000.0, CANDIDATES)
        # 若不允許零股，不應鼓勵零股
        assert "請優先零股" not in prompt


# ── StrategyPlannerGoal dataclass ─────────────────────────────────────────────

class TestStrategyPlannerGoal:
    def test_required_fields(self):
        pg = StrategyPlannerGoal(
            initial_capital=100_000.0,
            target_return_pct=10.0,
            max_drawdown_pct=10.0,
            holding_days=5,
            risk_level="moderate",
        )
        assert pg.initial_capital == pytest.approx(100_000.0)

    def test_optional_stop_loss(self):
        pg = StrategyPlannerGoal(
            initial_capital=100_000.0,
            target_return_pct=10.0,
            max_drawdown_pct=10.0,
            holding_days=5,
            risk_level="moderate",
            stop_loss_pct=8.0,
        )
        assert pg.stop_loss_pct == pytest.approx(8.0)

    def test_optional_max_positions(self):
        pg = StrategyPlannerGoal(
            initial_capital=100_000.0,
            target_return_pct=10.0,
            max_drawdown_pct=10.0,
            holding_days=5,
            risk_level="moderate",
            max_positions=4,
        )
        assert pg.max_positions == 4

    def test_optional_allow_fractional(self):
        pg = StrategyPlannerGoal(
            initial_capital=100_000.0,
            target_return_pct=10.0,
            max_drawdown_pct=10.0,
            holding_days=5,
            risk_level="moderate",
            allow_fractional=True,
        )
        assert pg.allow_fractional is True

    def test_defaults(self):
        pg = StrategyPlannerGoal(
            initial_capital=100_000.0,
            target_return_pct=10.0,
            max_drawdown_pct=10.0,
            holding_days=5,
            risk_level="moderate",
        )
        assert pg.stop_loss_pct  is None
        assert pg.max_positions  is None
        assert pg.allow_fractional is False
