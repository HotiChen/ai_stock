from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pytest

from sim_engine import (
    SimPosition,
    SimPortfolio,
    SimReport,
    open_position,
    close_position,
    update_unrealized,
    get_portfolio,
    generate_sim_report,
    compare_plans,
)
from research_db import init_db


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "sim_test.db")
    init_db(path)
    return path


# ── SimPosition ───────────────────────────────────────────────────────────────

class TestSimPosition:
    def test_has_required_fields(self):
        p = SimPosition(
            execution_id="exec-001",
            code="2330", name="台積電",
            quantity=1, avg_cost=850.0,
            opened_at=datetime.now(),
            status="open",
        )
        assert p.code == "2330"
        assert p.status == "open"
        assert p.quantity == 1

    def test_unrealized_pnl_calculated(self):
        p = SimPosition(
            execution_id="exec-001",
            code="2330", name="台積電",
            quantity=2, avg_cost=850.0,
            opened_at=datetime.now(),
            status="open",
            current_price=900.0,
        )
        # (900-850) * 2 * 1000 shares/lot = 100,000
        assert p.unrealized_pnl == pytest.approx(100_000.0)

    def test_unrealized_zero_when_no_current_price(self):
        p = SimPosition(
            execution_id="exec-001",
            code="2330", name="台積電",
            quantity=1, avg_cost=850.0,
            opened_at=datetime.now(),
            status="open",
        )
        assert p.unrealized_pnl == 0.0


# ── SimPortfolio ──────────────────────────────────────────────────────────────

class TestSimPortfolio:
    def test_has_required_fields(self):
        port = SimPortfolio(
            execution_id="exec-001",
            plan_type="balanced",
            open_positions=[],
            realized_pnl=5000.0,
            total_trades=3,
        )
        assert port.realized_pnl == pytest.approx(5000.0)
        assert port.total_trades == 3

    def test_total_pnl_includes_unrealized(self):
        pos = SimPosition(
            "exec-001", "2330", "台積電", 1, 850.0,
            datetime.now(), "open", current_price=900.0,
        )
        port = SimPortfolio(
            execution_id="exec-001",
            plan_type="balanced",
            open_positions=[pos],
            realized_pnl=10_000.0,
            total_trades=2,
        )
        # realized 10000 + unrealized (900-850)*1*1000 = 50000
        assert port.total_pnl == pytest.approx(60_000.0)


# ── open_position ─────────────────────────────────────────────────────────────

class TestOpenPosition:
    def test_creates_position(self, db_path):
        open_position("exec-001", "2330", "台積電", quantity=1, price=850.0, db_path=db_path)
        port = get_portfolio("exec-001", db_path)
        assert len(port.open_positions) == 1
        assert port.open_positions[0].avg_cost == pytest.approx(850.0)

    def test_same_code_averages_cost(self, db_path):
        open_position("exec-001", "2330", "台積電", 1, 800.0, db_path)
        open_position("exec-001", "2330", "台積電", 1, 900.0, db_path)
        port = get_portfolio("exec-001", db_path)
        pos = port.open_positions[0]
        assert pos.quantity == 2
        assert pos.avg_cost == pytest.approx(850.0)

    def test_different_codes_separate(self, db_path):
        open_position("exec-001", "2330", "台積電", 1, 850.0, db_path)
        open_position("exec-001", "2454", "聯發科", 1, 1100.0, db_path)
        port = get_portfolio("exec-001", db_path)
        assert len(port.open_positions) == 2


# ── close_position ────────────────────────────────────────────────────────────

class TestClosePosition:
    def test_closes_and_calculates_pnl(self, db_path):
        open_position("exec-001", "2330", "台積電", 1, 850.0, db_path)
        realized = close_position("exec-001", "2330", sell_price=900.0, db_path=db_path)
        # (900-850) * 1 * 1000 = 50000
        assert realized == pytest.approx(50_000.0)

    def test_position_removed_after_close(self, db_path):
        open_position("exec-001", "2330", "台積電", 1, 850.0, db_path)
        close_position("exec-001", "2330", sell_price=900.0, db_path=db_path)
        port = get_portfolio("exec-001", db_path)
        assert len(port.open_positions) == 0

    def test_realized_pnl_accumulated(self, db_path):
        open_position("exec-001", "2330", "台積電", 1, 850.0, db_path)
        close_position("exec-001", "2330", 900.0, db_path)
        open_position("exec-001", "2454", "聯發科", 1, 1000.0, db_path)
        close_position("exec-001", "2454", 1100.0, db_path)
        port = get_portfolio("exec-001", db_path)
        # 50000 + 100000
        assert port.realized_pnl == pytest.approx(150_000.0)

    def test_close_nonexistent_returns_zero(self, db_path):
        result = close_position("exec-001", "9999", 100.0, db_path)
        assert result == 0.0


# ── update_unrealized ─────────────────────────────────────────────────────────

class TestUpdateUnrealized:
    def test_updates_current_price(self, db_path):
        open_position("exec-001", "2330", "台積電", 1, 850.0, db_path)
        update_unrealized("exec-001", {"2330": 880.0}, db_path)
        port = get_portfolio("exec-001", db_path)
        pos = port.open_positions[0]
        assert pos.current_price == pytest.approx(880.0)
        assert pos.unrealized_pnl == pytest.approx(30_000.0)


# ── SimReport ─────────────────────────────────────────────────────────────────

class TestSimReport:
    def test_has_required_fields(self):
        r = SimReport(
            execution_id="exec-001",
            plan_type="balanced",
            report_date=date.today(),
            total_pnl=50_000.0,
            realized_pnl=30_000.0,
            unrealized_pnl=20_000.0,
            return_pct=5.0,
            total_trades=3,
            win_trades=2,
            open_positions_count=1,
            narrative="今日表現良好",
        )
        assert r.total_pnl == pytest.approx(50_000.0)
        assert r.return_pct == pytest.approx(5.0)

    def test_win_rate(self):
        r = SimReport("e", "b", date.today(), 0, 0, 0, 0, 4, 3, 0, "")
        assert r.win_rate == pytest.approx(75.0)

    def test_win_rate_zero_when_no_trades(self):
        r = SimReport("e", "b", date.today(), 0, 0, 0, 0, 0, 0, 0, "")
        assert r.win_rate == 0.0


# ── generate_sim_report ───────────────────────────────────────────────────────

class TestGenerateSimReport:
    def test_returns_report(self, db_path):
        open_position("exec-001", "2330", "台積電", 1, 850.0, db_path)
        update_unrealized("exec-001", {"2330": 880.0}, db_path)
        report = generate_sim_report("exec-001", "balanced", 30_000.0, db_path)
        assert isinstance(report, SimReport)
        assert report.plan_type == "balanced"

    def test_total_pnl_correct(self, db_path):
        open_position("exec-001", "2330", "台積電", 1, 850.0, db_path)
        close_position("exec-001", "2330", 900.0, db_path)
        report = generate_sim_report("exec-001", "balanced", 30_000.0, db_path)
        assert report.realized_pnl == pytest.approx(50_000.0)

    def test_return_pct_calculated(self, db_path):
        report = generate_sim_report("exec-001", "balanced", 100_000.0, db_path)
        assert isinstance(report.return_pct, float)


# ── compare_plans ─────────────────────────────────────────────────────────────

class TestComparePlans:
    def test_returns_list_of_reports(self, db_path):
        for i, plan_type in enumerate(("aggressive", "balanced", "conservative")):
            exec_id = f"exec-00{i+1}"
            open_position(exec_id, "2330", "台積電", 1, 850.0, db_path)
            close_position(exec_id, "2330", float(870 + i * 10), db_path)

        exec_map = {
            "aggressive":   "exec-001",
            "balanced":     "exec-002",
            "conservative": "exec-003",
        }
        reports = compare_plans(exec_map, initial_capital=30_000.0, db_path=db_path)
        assert len(reports) == 3

    def test_reports_sorted_by_pnl_desc(self, db_path):
        for i, (plan_type, pnl) in enumerate([
            ("aggressive", 900.0),
            ("balanced", 870.0),
            ("conservative", 860.0),
        ]):
            exec_id = f"exec-00{i+1}"
            open_position(exec_id, "2330", "台積電", 1, 850.0, db_path)
            close_position(exec_id, "2330", pnl, db_path)

        exec_map = {"aggressive": "exec-001", "balanced": "exec-002", "conservative": "exec-003"}
        reports = compare_plans(exec_map, 30_000.0, db_path)
        assert reports[0].total_pnl >= reports[1].total_pnl >= reports[2].total_pnl
