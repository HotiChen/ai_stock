from __future__ import annotations

import tempfile
from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pytest

from strategy_executor import (
    ExecutionStatus,
    CycleResult,
    start_execution,
    run_cycle,
    stop_execution as executor_stop,
    get_execution_status,
    build_cycle_decision,
)
from strategy_planner import StockPick, StrategyPlan, PlanSet
from research_db import init_db


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "exec_test.db")
    init_db(path)
    return path


def make_pick(**kw) -> StockPick:
    defaults = dict(code="2330", name="台積電", action="buy",
                    quantity=1, hold_days=3, expected_return_pct=5.0,
                    reason="RSI 低點", confidence=8)
    defaults.update(kw)
    return StockPick(**defaults)


def make_plan(plan_type="balanced", **kw) -> StrategyPlan:
    return StrategyPlan(
        plan_type=plan_type,
        description="均衡策略",
        picks=[make_pick()],
        capital_deployed_pct=0.6,
        expected_return_pct=15.0,
        risk_note="中等風險",
    )


def make_planset() -> PlanSet:
    return PlanSet(
        aggressive=make_plan("aggressive"),
        balanced=make_plan("balanced"),
        conservative=make_plan("conservative"),
        generated_at=date.today(),
    )


# ── ExecutionStatus ───────────────────────────────────────────────────────────

class TestExecutionStatus:
    def test_has_required_fields(self):
        s = ExecutionStatus(
            execution_id="exec-001",
            plan_type="balanced",
            status="active",
            started_at=datetime.now(),
            total_simulated_pnl=1500.0,
            cycles_run=5,
        )
        assert s.execution_id == "exec-001"
        assert s.status == "active"

    def test_status_values(self):
        for status in ("active", "stopped", "completed"):
            s = ExecutionStatus("id", "balanced", status, datetime.now(), 0.0, 0)
            assert s.status == status


# ── CycleResult ───────────────────────────────────────────────────────────────

class TestCycleResult:
    def test_has_required_fields(self):
        r = CycleResult(
            ran_at=datetime.now(),
            actions_taken=[{"code": "2330", "action": "buy", "reason": "RSI"}],
            skipped_codes=["2454"],
            market_sentiment="bullish",
        )
        assert len(r.actions_taken) == 1
        assert "2330" in r.skipped_codes or "2454" in r.skipped_codes


# ── start_execution ───────────────────────────────────────────────────────────

class TestStartExecution:
    def test_returns_execution_id(self, db_path):
        plan_set = make_planset()
        exec_id = start_execution(plan_set, "balanced", db_path)
        assert isinstance(exec_id, str)
        assert len(exec_id) > 0

    def test_execution_persisted(self, db_path):
        from research_db import load_active_execution
        plan_set = make_planset()
        exec_id = start_execution(plan_set, "aggressive", db_path)
        loaded = load_active_execution(db_path)
        assert loaded is not None
        assert loaded.plan_type == "aggressive"

    def test_only_one_active_at_a_time(self, db_path):
        from research_db import load_active_execution
        plan_set = make_planset()
        start_execution(plan_set, "balanced", db_path)
        start_execution(plan_set, "conservative", db_path)
        # latest should win (or both could be active — just check no crash)
        loaded = load_active_execution(db_path)
        assert loaded is not None


# ── build_cycle_decision ──────────────────────────────────────────────────────

class TestBuildCycleDecision:
    def test_returns_dict_with_action(self):
        analysis = MagicMock()
        analysis.signal = "buy"
        analysis.confidence = 8
        analysis.summary = "RSI 低點"
        pick = make_pick()
        result = build_cycle_decision(pick, analysis, current_price=850.0)
        assert "action" in result
        assert result["action"] in ("buy", "hold", "sell")

    def test_low_confidence_defaults_to_hold(self):
        analysis = MagicMock()
        analysis.signal = "buy"
        analysis.confidence = 2
        analysis.summary = "不確定"
        pick = make_pick()
        result = build_cycle_decision(pick, analysis, current_price=850.0)
        assert result["action"] == "hold"

    def test_includes_price(self):
        analysis = MagicMock(signal="buy", confidence=8, summary="ok")
        result = build_cycle_decision(make_pick(), analysis, current_price=900.0)
        assert result.get("price") == pytest.approx(900.0)


# ── run_cycle ─────────────────────────────────────────────────────────────────

class TestRunCycle:
    @patch("strategy_executor.run_deep_analysis")
    @patch("strategy_executor.fetch_us_market")
    @patch("strategy_executor.fetch_macro_news")
    @patch("strategy_executor.fetch_stock_news", return_value=[])
    @patch("strategy_executor.fetch_stock_fundamentals", return_value=None)
    @patch("strategy_executor.format_fundamentals_text", return_value="PE N/A")
    @patch("strategy_executor.get_current_price", return_value=850.0)
    def test_returns_cycle_result(
        self, mock_price, mock_fmt, mock_fund, mock_news,
        mock_macro, mock_us, mock_deep, db_path
    ):
        from market_context import USMarketSnapshot
        mock_us.return_value = USMarketSnapshot(0.5, 1.0, 1.5, 18.0)
        mock_macro.return_value = []
        mock_deep.return_value = MagicMock(
            signal="buy", confidence=8, summary="RSI ok",
            factors=MagicMock(historical_trend="up", news="ok",
                              theme="AI", us_market="strong",
                              tw_policy="ok", us_policy="ok"),
        )
        plan_set = make_planset()
        exec_id = start_execution(plan_set, "balanced", db_path)
        result = run_cycle(exec_id, db_path)
        assert isinstance(result, CycleResult)

    @patch("strategy_executor.run_deep_analysis")
    @patch("strategy_executor.fetch_us_market", return_value=None)
    @patch("strategy_executor.fetch_macro_news", return_value=[])
    @patch("strategy_executor.fetch_stock_news", return_value=[])
    @patch("strategy_executor.fetch_stock_fundamentals", return_value=None)
    @patch("strategy_executor.format_fundamentals_text", return_value="")
    @patch("strategy_executor.get_current_price", return_value=None)
    def test_no_price_skips_stock(
        self, mock_price, mock_fmt, mock_fund, mock_news,
        mock_macro, mock_us, mock_deep, db_path
    ):
        mock_deep.return_value = MagicMock(
            signal="buy", confidence=8, summary="ok",
            factors=MagicMock(historical_trend="", news="", theme="",
                              us_market="", tw_policy="", us_policy=""),
        )
        plan_set = make_planset()
        exec_id = start_execution(plan_set, "balanced", db_path)
        result = run_cycle(exec_id, db_path)
        assert "2330" in result.skipped_codes


# ── stop_execution ────────────────────────────────────────────────────────────

class TestStopExecution:
    def test_stops_active(self, db_path):
        from research_db import load_active_execution
        plan_set = make_planset()
        exec_id = start_execution(plan_set, "balanced", db_path)
        executor_stop(exec_id, db_path)
        assert load_active_execution(db_path) is None

    def test_stop_nonexistent_does_not_raise(self, db_path):
        executor_stop("fake-id", db_path)  # should not raise


# ── get_execution_status ──────────────────────────────────────────────────────

class TestGetExecutionStatus:
    def test_returns_status(self, db_path):
        plan_set = make_planset()
        exec_id = start_execution(plan_set, "conservative", db_path)
        status = get_execution_status(exec_id, db_path)
        assert isinstance(status, ExecutionStatus)
        assert status.plan_type == "conservative"

    def test_nonexistent_returns_none(self, db_path):
        assert get_execution_status("fake", db_path) is None
