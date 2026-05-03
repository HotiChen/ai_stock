from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from strategy_planner import (
    StockPick,
    StrategyPlan,
    PlanSet,
    _parse_picks,
    _clamp_quantities,
    calc_max_lots,
)

TODAY = date(2026, 5, 3)

# ── StockPick 新 action 類型 ──────────────────────────────────────────────────

class TestStockPickActions:
    def _make(self, action: str, switch_from: str = "") -> StockPick:
        return StockPick(
            code="2330", name="台積電", action=action,
            quantity=1, hold_days=3, expected_return_pct=5.0,
            reason="測試", confidence=7,
            switch_from=switch_from,
        )

    def test_open_action_valid(self):
        p = self._make("open")
        assert p.action == "open"

    def test_hold_action_valid(self):
        p = self._make("hold")
        assert p.action == "hold"

    def test_add_action_valid(self):
        p = self._make("add")
        assert p.action == "add"

    def test_reduce_action_valid(self):
        p = self._make("reduce")
        assert p.action == "reduce"

    def test_close_action_valid(self):
        p = self._make("close")
        assert p.action == "close"

    def test_switch_action_valid(self):
        p = self._make("switch", switch_from="2454")
        assert p.action == "switch"
        assert p.switch_from == "2454"

    def test_switch_from_default_empty(self):
        p = self._make("open")
        assert p.switch_from == ""

    def test_total_cost_open(self):
        # open 1張 @ 100 = 100,000
        p = self._make("open")
        assert p.total_cost(100.0) == pytest.approx(100_000.0)

    def test_total_cost_hold_is_zero(self):
        # hold 不花錢
        p = self._make("hold")
        assert p.total_cost(100.0) == pytest.approx(0.0)

    def test_total_cost_close_is_zero(self):
        # close 不花錢（平倉，資金回來）
        p = self._make("close")
        assert p.total_cost(100.0) == pytest.approx(0.0)

    def test_total_cost_reduce_is_zero(self):
        # reduce 不花錢
        p = self._make("reduce")
        assert p.total_cost(100.0) == pytest.approx(0.0)

    def test_total_cost_switch_is_zero(self):
        # switch = 賣 A 買 B，整體資金中性
        p = self._make("switch", switch_from="2454")
        assert p.total_cost(100.0) == pytest.approx(0.0)

    def test_total_cost_add_same_as_open(self):
        # add 加碼，需要資金
        p_open = self._make("open")
        p_add  = self._make("add")
        assert p_add.total_cost(100.0) == pytest.approx(p_open.total_cost(100.0))


# ── _parse_picks 新 action ───────────────────────────────────────────────────

class TestParsePicksNewActions:
    def _raw(self, action: str, switch_from: str = "") -> dict:
        return {
            "code": "2330", "name": "台積電",
            "action": action, "quantity": 1, "hold_days": 3,
            "expected_return_pct": 5.0, "reason": "test", "confidence": 7,
            "switch_from": switch_from,
        }

    def test_parses_open(self):
        picks = _parse_picks([self._raw("open")])
        assert picks[0].action == "open"

    def test_parses_hold(self):
        picks = _parse_picks([self._raw("hold")])
        assert picks[0].action == "hold"

    def test_parses_add(self):
        picks = _parse_picks([self._raw("add")])
        assert picks[0].action == "add"

    def test_parses_reduce(self):
        picks = _parse_picks([self._raw("reduce")])
        assert picks[0].action == "reduce"

    def test_parses_close(self):
        picks = _parse_picks([self._raw("close")])
        assert picks[0].action == "close"

    def test_parses_switch_with_switch_from(self):
        picks = _parse_picks([self._raw("switch", switch_from="2454")])
        assert picks[0].action == "switch"
        assert picks[0].switch_from == "2454"

    def test_parses_buy_backward_compat(self):
        """舊的 buy 仍然可以解析（向後兼容）"""
        picks = _parse_picks([self._raw("buy")])
        assert picks[0].action == "buy"

    def test_switch_from_defaults_empty(self):
        raw = self._raw("open")
        raw.pop("switch_from", None)
        picks = _parse_picks([raw])
        assert picks[0].switch_from == ""


# ── _clamp_quantities 新 action 資金邏輯 ─────────────────────────────────────

def _make_plan(plan_type: str, picks: list[StockPick]) -> StrategyPlan:
    return StrategyPlan(
        plan_type=plan_type, description="test",
        picks=picks, capital_deployed_pct=0.5,
        expected_return_pct=5.0, risk_note="",
    )


def _make_planset(picks_agg=None, picks_bal=None, picks_con=None) -> PlanSet:
    return PlanSet(
        aggressive=_make_plan("aggressive", picks_agg or []),
        balanced=_make_plan("balanced", picks_bal or []),
        conservative=_make_plan("conservative", picks_con or []),
        generated_at=TODAY,
    )


def _pick(code: str, action: str, qty: int = 1, switch_from: str = "") -> StockPick:
    return StockPick(
        code=code, name=code, action=action,
        quantity=qty, hold_days=3,
        expected_return_pct=5.0, reason="", confidence=7,
        switch_from=switch_from,
    )


# 候選股：每股 25 元（整張 25,000 元）
CANDIDATES = [
    {"code": "2330", "close": 25.0, "name": "A", "change_rate": 1.0, "analysis": ""},
    {"code": "2454", "close": 25.0, "name": "B", "change_rate": 0.5, "analysis": ""},
]
CAPITAL = 30_000.0   # 只夠買 1 張


class TestClampQuantitiesNewActions:
    def test_hold_always_included_regardless_of_capital(self):
        """hold 不消耗資金，永遠保留"""
        picks = [_pick("2330", "hold")]
        ps = _make_planset(picks_agg=picks)
        result = _clamp_quantities(ps, CANDIDATES, CAPITAL)
        assert len(result.aggressive.picks) == 1
        assert result.aggressive.picks[0].action == "hold"

    def test_close_always_included(self):
        """close 平倉，不消耗資金"""
        picks = [_pick("2330", "close")]
        ps = _make_planset(picks_agg=picks)
        result = _clamp_quantities(ps, CANDIDATES, CAPITAL)
        assert len(result.aggressive.picks) == 1
        assert result.aggressive.picks[0].action == "close"

    def test_reduce_always_included(self):
        """reduce 減碼，不消耗資金"""
        picks = [_pick("2330", "reduce")]
        ps = _make_planset(picks_agg=picks)
        result = _clamp_quantities(ps, CANDIDATES, CAPITAL)
        assert len(result.aggressive.picks) == 1
        assert result.aggressive.picks[0].action == "reduce"

    def test_switch_always_included(self):
        """switch = 賣 A 買 B，資金中性，永遠保留"""
        picks = [_pick("2330", "switch", switch_from="2454")]
        ps = _make_planset(picks_agg=picks)
        result = _clamp_quantities(ps, CANDIDATES, CAPITAL)
        assert len(result.aggressive.picks) == 1
        assert result.aggressive.picks[0].action == "switch"

    def test_open_consumes_capital(self):
        """open/add 消耗資金；超出本金的被丟棄"""
        # 兩個 open，各 25,000 → 合計 50,000 > 30,000
        picks = [
            _pick("2330", "open", qty=1),   # 25,000
            _pick("2454", "open", qty=1),   # 25,000
        ]
        ps = _make_planset(picks_agg=picks)
        result = _clamp_quantities(ps, CANDIDATES, CAPITAL)
        # 只能保留 1 張
        assert len(result.aggressive.picks) == 1

    def test_add_consumes_capital(self):
        """add 加碼也消耗資金"""
        picks = [
            _pick("2330", "add", qty=1),   # 25,000
            _pick("2454", "add", qty=1),   # 25,000
        ]
        ps = _make_planset(picks_agg=picks)
        result = _clamp_quantities(ps, CANDIDATES, CAPITAL)
        assert len(result.aggressive.picks) == 1

    def test_hold_and_open_mixed(self):
        """hold 不消耗資金，open 消耗；兩者混合時 hold 一定保留"""
        picks = [
            _pick("2330", "hold"),           # 不花錢，應保留
            _pick("2454", "open", qty=1),   # 25,000
        ]
        ps = _make_planset(picks_agg=picks)
        result = _clamp_quantities(ps, CANDIDATES, CAPITAL)
        actions = {p.action for p in result.aggressive.picks}
        assert "hold" in actions

    def test_close_and_open_mixed(self):
        """close + open 混合；close 不消耗資金"""
        picks = [
            _pick("2330", "close"),          # 不花錢
            _pick("2454", "open", qty=1),   # 25,000 < 30,000 → 可進
        ]
        ps = _make_planset(picks_agg=picks)
        result = _clamp_quantities(ps, CANDIDATES, CAPITAL)
        assert len(result.aggressive.picks) == 2

    def test_backward_compat_buy_still_works(self):
        """舊的 buy action 仍然被當作 open 處理"""
        picks = [_pick("2330", "buy", qty=1)]
        ps = _make_planset(picks_agg=picks)
        result = _clamp_quantities(ps, CANDIDATES, CAPITAL)
        assert len(result.aggressive.picks) == 1

    def test_prompt_contains_new_action_descriptions(self):
        """build_plan_prompt 應該說明所有 action 類型"""
        from strategy_planner import build_plan_prompt
        from strategy_tracker import StrategyGoal
        goal = StrategyGoal(
            target_multiplier=2.0, start_date=TODAY, end_date=TODAY,
            initial_capital=30_000.0, approach="短線",
        )
        prompt = build_plan_prompt("balanced", goal, 30_000.0, CANDIDATES)
        assert "open"   in prompt
        assert "add"    in prompt
        assert "reduce" in prompt
        assert "close"  in prompt
        assert "switch" in prompt
        assert "hold"   in prompt
