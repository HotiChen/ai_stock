"""Tests for delete functions in sim_plan_store and daily_tracker."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from strategy_planner import PlanSet, StrategyPlan, StockPick

TODAY = date(2026, 5, 3)

# ── helpers ───────────────────────────────────────────────────────────────────

def _planset() -> PlanSet:
    pick = StockPick(
        code="2330", name="台積電", action="open",
        quantity=1, hold_days=3, expected_return_pct=8.0,
        reason="test", confidence=7,
    )
    plan = StrategyPlan(
        plan_type="aggressive", description="test",
        picks=[pick], capital_deployed_pct=0.5,
        expected_return_pct=8.0, risk_note="",
    )
    return PlanSet(
        aggressive=plan, balanced=plan, conservative=plan,
        generated_at=TODAY,
    )


# ── imports under test ────────────────────────────────────────────────────────

from sim_plan_store import save_sim_plan, list_sim_plans, delete_sim_plan
from daily_tracker import DailyTrackRecord, PlanResult, save_day_record, delete_day_record


# ── delete_sim_plan ───────────────────────────────────────────────────────────

class TestDeleteSimPlan:
    def test_delete_removes_file(self, tmp_path):
        fpath = save_sim_plan(_planset(), plan_dir=str(tmp_path))
        filename = fpath.name
        delete_sim_plan(filename, plan_dir=str(tmp_path))
        assert not (tmp_path / filename).exists()

    def test_deleted_plan_not_in_list(self, tmp_path):
        fpath = save_sim_plan(_planset(), plan_dir=str(tmp_path))
        filename = fpath.name
        delete_sim_plan(filename, plan_dir=str(tmp_path))
        remaining = [p["filename"] for p in list_sim_plans(str(tmp_path))]
        assert filename not in remaining

    def test_delete_nonexistent_raises_or_ignores(self, tmp_path):
        """刪除不存在的檔案應 raise FileNotFoundError 或靜默忽略，不 crash。"""
        try:
            delete_sim_plan("not_exist.json", plan_dir=str(tmp_path))
        except FileNotFoundError:
            pass  # acceptable

    def test_delete_leaves_other_plans_intact(self, tmp_path):
        fpath1 = save_sim_plan(_planset(), plan_dir=str(tmp_path))
        fpath2 = save_sim_plan(_planset(), plan_dir=str(tmp_path))
        delete_sim_plan(fpath1.name, plan_dir=str(tmp_path))
        remaining = [p["filename"] for p in list_sim_plans(str(tmp_path))]
        assert fpath2.name in remaining
        assert fpath1.name not in remaining

    def test_returns_none_or_bool(self, tmp_path):
        fpath = save_sim_plan(_planset(), plan_dir=str(tmp_path))
        result = delete_sim_plan(fpath.name, plan_dir=str(tmp_path))
        # return value can be None or bool, just shouldn't raise
        assert result is None or isinstance(result, bool)


# ── delete_day_record ─────────────────────────────────────────────────────────

def _make_record(d: date) -> DailyTrackRecord:
    result = PlanResult(
        plan_type="balanced", pnl=500.0, pnl_pct=1.5,
        closing_capital=30_500.0, win_trades=1, loss_trades=0, is_actual=True,
    )
    return DailyTrackRecord(
        date=d, starting_capital=30_000.0,
        chosen_plan_type="balanced", results=[result],
    )


class TestDeleteDayRecord:
    def test_delete_removes_file(self, tmp_path):
        rec = _make_record(TODAY)
        save_day_record(rec, track_dir=str(tmp_path))
        delete_day_record(TODAY, track_dir=str(tmp_path))
        json_path = tmp_path / f"{TODAY.isoformat()}.json"
        assert not json_path.exists()

    def test_deleted_record_not_in_list(self, tmp_path):
        from daily_tracker import list_day_records
        rec = _make_record(TODAY)
        save_day_record(rec, track_dir=str(tmp_path))
        delete_day_record(TODAY, track_dir=str(tmp_path))
        remaining = list_day_records(str(tmp_path))
        assert not any(r.date == TODAY for r in remaining)

    def test_delete_nonexistent_raises_or_ignores(self, tmp_path):
        try:
            delete_day_record(date(2000, 1, 1), track_dir=str(tmp_path))
        except FileNotFoundError:
            pass  # acceptable

    def test_delete_leaves_other_records_intact(self, tmp_path):
        from daily_tracker import list_day_records, load_day_record
        d1 = date(2026, 5, 1)
        d2 = date(2026, 5, 2)
        save_day_record(_make_record(d1), track_dir=str(tmp_path))
        save_day_record(_make_record(d2), track_dir=str(tmp_path))
        delete_day_record(d1, track_dir=str(tmp_path))
        remaining = list_day_records(str(tmp_path))
        dates = [r.date for r in remaining]
        assert d2 in dates
        assert d1 not in dates

    def test_returns_none_or_bool(self, tmp_path):
        rec = _make_record(TODAY)
        save_day_record(rec, track_dir=str(tmp_path))
        result = delete_day_record(TODAY, track_dir=str(tmp_path))
        assert result is None or isinstance(result, bool)
