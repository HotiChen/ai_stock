from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from daily_tracker import (
    DailyTrackRecord,
    PlanResult,
    list_day_records,
    load_day_record,
    record_to_dict,
    record_from_dict,
    save_day_record,
    update_plan_result,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _result(plan_type: str, is_actual: bool, pnl: float = 1000.0) -> PlanResult:
    return PlanResult(
        plan_type=plan_type,
        pnl=pnl,
        pnl_pct=pnl / 100_000 * 100,
        closing_capital=100_000 + pnl,
        win_trades=2,
        loss_trades=1,
        is_actual=is_actual,
    )


def _record(d: date = date(2026, 4, 29)) -> DailyTrackRecord:
    return DailyTrackRecord(
        date=d,
        starting_capital=100_000.0,
        chosen_plan_type="aggressive",
        results=[
            _result("aggressive", is_actual=True,  pnl=2000.0),
            _result("balanced",   is_actual=False, pnl=1200.0),
            _result("conservative", is_actual=False, pnl=500.0),
        ],
    )


# ── Serialization ─────────────────────────────────────────────────────────────

class TestSerialization:
    def test_to_dict_has_required_keys(self):
        d = record_to_dict(_record())
        assert "date" in d
        assert "starting_capital" in d
        assert "chosen_plan_type" in d
        assert "results" in d

    def test_roundtrip_preserves_date(self):
        r = _record()
        assert record_from_dict(record_to_dict(r)).date == date(2026, 4, 29)

    def test_roundtrip_preserves_chosen_plan(self):
        r = _record()
        assert record_from_dict(record_to_dict(r)).chosen_plan_type == "aggressive"

    def test_roundtrip_preserves_starting_capital(self):
        r = _record()
        assert record_from_dict(record_to_dict(r)).starting_capital == 100_000.0

    def test_roundtrip_preserves_results_count(self):
        r = _record()
        assert len(record_from_dict(record_to_dict(r)).results) == 3

    def test_roundtrip_preserves_is_actual_flag(self):
        r = _record()
        restored = record_from_dict(record_to_dict(r))
        actual = [res for res in restored.results if res.is_actual]
        shadow = [res for res in restored.results if not res.is_actual]
        assert len(actual) == 1
        assert len(shadow) == 2

    def test_roundtrip_preserves_pnl(self):
        r = _record()
        restored = record_from_dict(record_to_dict(r))
        agg = next(res for res in restored.results if res.plan_type == "aggressive")
        assert agg.pnl == pytest.approx(2000.0)

    def test_result_none_pnl_allowed(self):
        """Results may be None before the day ends."""
        r = _record()
        r.results[1].pnl = None
        restored = record_from_dict(record_to_dict(r))
        bal = next(res for res in restored.results if res.plan_type == "balanced")
        assert bal.pnl is None


# ── save_day_record ───────────────────────────────────────────────────────────

class TestSaveDayRecord:
    def test_creates_file(self, tmp_path):
        fpath = save_day_record(_record(), track_dir=str(tmp_path))
        assert fpath.exists()

    def test_filename_contains_date(self, tmp_path):
        fpath = save_day_record(_record(), track_dir=str(tmp_path))
        assert "2026-04-29" in fpath.name

    def test_creates_dir_if_missing(self, tmp_path):
        nested = tmp_path / "a" / "b"
        fpath = save_day_record(_record(), track_dir=str(nested))
        assert fpath.exists()

    def test_content_is_valid_json(self, tmp_path):
        fpath = save_day_record(_record(), track_dir=str(tmp_path))
        data = json.loads(fpath.read_text(encoding="utf-8"))
        assert "chosen_plan_type" in data

    def test_overwrite_same_date(self, tmp_path):
        r = _record()
        save_day_record(r, track_dir=str(tmp_path))
        r.chosen_plan_type = "balanced"
        save_day_record(r, track_dir=str(tmp_path))
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["chosen_plan_type"] == "balanced"


# ── load_day_record ───────────────────────────────────────────────────────────

class TestLoadDayRecord:
    def test_returns_record(self, tmp_path):
        save_day_record(_record(), track_dir=str(tmp_path))
        loaded = load_day_record(date(2026, 4, 29), track_dir=str(tmp_path))
        assert isinstance(loaded, DailyTrackRecord)

    def test_roundtrip_preserves_chosen_plan(self, tmp_path):
        save_day_record(_record(), track_dir=str(tmp_path))
        loaded = load_day_record(date(2026, 4, 29), track_dir=str(tmp_path))
        assert loaded.chosen_plan_type == "aggressive"

    def test_missing_returns_none(self, tmp_path):
        assert load_day_record(date(2026, 1, 1), track_dir=str(tmp_path)) is None

    def test_corrupt_returns_none(self, tmp_path):
        (tmp_path / "2026-04-29.json").write_text("bad", encoding="utf-8")
        assert load_day_record(date(2026, 4, 29), track_dir=str(tmp_path)) is None


# ── list_day_records ──────────────────────────────────────────────────────────

class TestListDayRecords:
    def test_empty_dir_returns_empty(self, tmp_path):
        assert list_day_records(track_dir=str(tmp_path)) == []

    def test_missing_dir_returns_empty(self, tmp_path):
        assert list_day_records(track_dir=str(tmp_path / "no_such")) == []

    def test_one_file_returns_one_entry(self, tmp_path):
        save_day_record(_record(), track_dir=str(tmp_path))
        assert len(list_day_records(track_dir=str(tmp_path))) == 1

    def test_sorted_newest_first(self, tmp_path):
        save_day_record(_record(date(2026, 4, 28)), track_dir=str(tmp_path))
        save_day_record(_record(date(2026, 4, 29)), track_dir=str(tmp_path))
        records = list_day_records(track_dir=str(tmp_path))
        assert records[0].date == date(2026, 4, 29)
        assert records[1].date == date(2026, 4, 28)

    def test_returns_daily_track_record_objects(self, tmp_path):
        save_day_record(_record(), track_dir=str(tmp_path))
        records = list_day_records(track_dir=str(tmp_path))
        assert isinstance(records[0], DailyTrackRecord)


# ── update_plan_result ────────────────────────────────────────────────────────

class TestUpdatePlanResult:
    def test_updates_pnl(self, tmp_path):
        r = _record()
        r.results[0].pnl = None  # aggressive not yet settled
        save_day_record(r, track_dir=str(tmp_path))
        update_plan_result(
            date(2026, 4, 29), "aggressive",
            pnl=3000.0, pnl_pct=3.0, closing_capital=103_000.0,
            win_trades=3, loss_trades=0,
            track_dir=str(tmp_path),
        )
        loaded = load_day_record(date(2026, 4, 29), track_dir=str(tmp_path))
        agg = next(res for res in loaded.results if res.plan_type == "aggressive")
        assert agg.pnl == pytest.approx(3000.0)
        assert agg.closing_capital == pytest.approx(103_000.0)

    def test_updates_shadow_plan(self, tmp_path):
        r = _record()
        r.results[1].pnl = None  # balanced shadow not yet settled
        save_day_record(r, track_dir=str(tmp_path))
        update_plan_result(
            date(2026, 4, 29), "balanced",
            pnl=800.0, pnl_pct=0.8, closing_capital=100_800.0,
            win_trades=1, loss_trades=1,
            track_dir=str(tmp_path),
        )
        loaded = load_day_record(date(2026, 4, 29), track_dir=str(tmp_path))
        bal = next(res for res in loaded.results if res.plan_type == "balanced")
        assert bal.pnl == pytest.approx(800.0)

    def test_no_record_does_nothing(self, tmp_path):
        # should not raise
        update_plan_result(
            date(2026, 1, 1), "aggressive",
            pnl=0.0, pnl_pct=0.0, closing_capital=100_000.0,
            win_trades=0, loss_trades=0,
            track_dir=str(tmp_path),
        )

    def test_does_not_change_is_actual_flag(self, tmp_path):
        save_day_record(_record(), track_dir=str(tmp_path))
        update_plan_result(
            date(2026, 4, 29), "balanced",
            pnl=999.0, pnl_pct=1.0, closing_capital=100_999.0,
            win_trades=1, loss_trades=0,
            track_dir=str(tmp_path),
        )
        loaded = load_day_record(date(2026, 4, 29), track_dir=str(tmp_path))
        bal = next(res for res in loaded.results if res.plan_type == "balanced")
        assert bal.is_actual is False
