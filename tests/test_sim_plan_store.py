from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from strategy_planner import PlanSet, StrategyPlan, StockPick
from sim_plan_store import (
    list_sim_plans,
    load_sim_plan,
    planset_from_dict,
    planset_to_dict,
    save_sim_plan,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _pick(**kw) -> StockPick:
    return StockPick(
        code=kw.get("code", "2330"),
        name=kw.get("name", "台積電"),
        action="buy",
        quantity=1,
        hold_days=3,
        expected_return_pct=2.5,
        reason="強勢突破",
        confidence=8,
        key_catalysts=["AI 需求"],
        entry_logic="站上 MA5",
        exit_logic="跌破 MA5 停損",
    )


def _plan(plan_type: str = "aggressive") -> StrategyPlan:
    return StrategyPlan(
        plan_type=plan_type,
        description="衝刺策略",
        picks=[_pick()],
        capital_deployed_pct=0.8,
        expected_return_pct=3.0,
        risk_note="高風險",
        thesis="AI 題材持續強勢",
        macro_context="美股偏多",
    )


def _plan_set(d: date = date(2026, 4, 29)) -> PlanSet:
    return PlanSet(
        aggressive=_plan("aggressive"),
        balanced=_plan("balanced"),
        conservative=_plan("conservative"),
        generated_at=d,
    )


# ── Serialization roundtrip ───────────────────────────────────────────────────

class TestPlanSetSerialization:
    def test_to_dict_has_three_plan_keys(self):
        d = planset_to_dict(_plan_set())
        assert {"aggressive", "balanced", "conservative"} <= d.keys()

    def test_to_dict_has_generated_at(self):
        d = planset_to_dict(_plan_set())
        assert d["generated_at"] == "2026-04-29"

    def test_roundtrip_preserves_date(self):
        ps = _plan_set()
        assert planset_from_dict(planset_to_dict(ps)).generated_at == date(2026, 4, 29)

    def test_roundtrip_preserves_plan_type(self):
        ps = _plan_set()
        restored = planset_from_dict(planset_to_dict(ps))
        assert restored.aggressive.plan_type == "aggressive"
        assert restored.balanced.plan_type == "balanced"
        assert restored.conservative.plan_type == "conservative"

    def test_roundtrip_preserves_pick_code(self):
        ps = _plan_set()
        restored = planset_from_dict(planset_to_dict(ps))
        assert restored.aggressive.picks[0].code == "2330"

    def test_roundtrip_preserves_optional_fields(self):
        ps = _plan_set()
        restored = planset_from_dict(planset_to_dict(ps))
        assert restored.aggressive.thesis == "AI 題材持續強勢"
        assert restored.aggressive.macro_context == "美股偏多"
        assert restored.aggressive.picks[0].key_catalysts == ["AI 需求"]
        assert restored.aggressive.picks[0].entry_logic == "站上 MA5"

    def test_roundtrip_preserves_fractional_pick(self):
        ps = _plan_set()
        ps.balanced.picks[0].is_fractional = True
        ps.balanced.picks[0].shares = 500
        restored = planset_from_dict(planset_to_dict(ps))
        assert restored.balanced.picks[0].is_fractional is True
        assert restored.balanced.picks[0].shares == 500

    def test_from_dict_missing_optional_fields_uses_defaults(self):
        d = planset_to_dict(_plan_set())
        del d["aggressive"]["thesis"]
        del d["aggressive"]["macro_context"]
        restored = planset_from_dict(d)
        assert restored.aggressive.thesis == ""
        assert restored.aggressive.macro_context == ""


# ── save_sim_plan ─────────────────────────────────────────────────────────────

class TestSaveSimPlan:
    def test_creates_file(self, tmp_path):
        fpath = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        assert fpath.exists()

    def test_filename_contains_date(self, tmp_path):
        fpath = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        assert "2026-04-29" in fpath.name

    def test_filename_starts_with_day_label(self, tmp_path):
        fpath = save_sim_plan(_plan_set(), plan_dir=str(tmp_path), day_label="day3")
        assert fpath.name.startswith("day3_")

    def test_auto_label_first_file_is_day1(self, tmp_path):
        fpath = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        assert fpath.name.startswith("day1_")

    def test_auto_label_increments(self, tmp_path):
        f1 = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        f2 = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        assert f1.name.startswith("day1_")
        assert f2.name.startswith("day2_")

    def test_creates_dir_if_missing(self, tmp_path):
        nested = tmp_path / "a" / "b"
        fpath = save_sim_plan(_plan_set(), plan_dir=str(nested))
        assert fpath.exists()

    def test_content_is_valid_json(self, tmp_path):
        fpath = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        data = json.loads(fpath.read_text(encoding="utf-8"))
        assert "aggressive" in data

    def test_explicit_day_label_does_not_affect_counter(self, tmp_path):
        save_sim_plan(_plan_set(), plan_dir=str(tmp_path), day_label="day5")
        f2 = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        assert f2.name.startswith("day6_")


# ── load_sim_plan ─────────────────────────────────────────────────────────────

class TestLoadSimPlan:
    def test_returns_plan_set(self, tmp_path):
        fpath = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        loaded = load_sim_plan(fpath.name, plan_dir=str(tmp_path))
        assert isinstance(loaded, PlanSet)

    def test_roundtrip_preserves_date(self, tmp_path):
        fpath = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        loaded = load_sim_plan(fpath.name, plan_dir=str(tmp_path))
        assert loaded.generated_at == date(2026, 4, 29)

    def test_roundtrip_preserves_pick(self, tmp_path):
        fpath = save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        loaded = load_sim_plan(fpath.name, plan_dir=str(tmp_path))
        assert loaded.aggressive.picks[0].code == "2330"

    def test_missing_file_returns_none(self, tmp_path):
        assert load_sim_plan("ghost.json", plan_dir=str(tmp_path)) is None

    def test_corrupt_file_returns_none(self, tmp_path):
        (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
        assert load_sim_plan("bad.json", plan_dir=str(tmp_path)) is None


# ── list_sim_plans ────────────────────────────────────────────────────────────

class TestListSimPlans:
    def test_empty_dir_returns_empty(self, tmp_path):
        assert list_sim_plans(plan_dir=str(tmp_path)) == []

    def test_missing_dir_returns_empty(self, tmp_path):
        assert list_sim_plans(plan_dir=str(tmp_path / "no_such")) == []

    def test_one_file_returns_one_entry(self, tmp_path):
        save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        assert len(list_sim_plans(plan_dir=str(tmp_path))) == 1

    def test_two_files_returns_two_entries(self, tmp_path):
        save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        assert len(list_sim_plans(plan_dir=str(tmp_path))) == 2

    def test_entry_has_required_keys(self, tmp_path):
        save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        entry = list_sim_plans(plan_dir=str(tmp_path))[0]
        assert "filename" in entry
        assert "day_label" in entry
        assert "plan_date" in entry

    def test_sorted_newest_first(self, tmp_path):
        save_sim_plan(_plan_set(), plan_dir=str(tmp_path))   # day1
        save_sim_plan(_plan_set(), plan_dir=str(tmp_path))   # day2
        result = list_sim_plans(plan_dir=str(tmp_path))
        assert result[0]["day_label"] == "day2"
        assert result[1]["day_label"] == "day1"

    def test_entry_day_label_matches_filename(self, tmp_path):
        save_sim_plan(_plan_set(), plan_dir=str(tmp_path), day_label="day7")
        entry = list_sim_plans(plan_dir=str(tmp_path))[0]
        assert entry["day_label"] == "day7"

    def test_entry_plan_date_matches_generated_at(self, tmp_path):
        save_sim_plan(_plan_set(), plan_dir=str(tmp_path))
        entry = list_sim_plans(plan_dir=str(tmp_path))[0]
        assert entry["plan_date"] == "2026-04-29"
