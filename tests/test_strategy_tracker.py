from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from strategy_tracker import (
    DailyEntry,
    StrategyGoal,
    StrategyProgress,
    calc_daily_target,
    calc_progress,
    check_daily_target,
    generate_daily_review,
    load_daily_entries,
    load_goal,
    save_daily_entry,
    save_goal,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

START = date(2026, 4, 1)
END   = date(2026, 4, 30)

def make_goal(**kwargs) -> StrategyGoal:
    defaults = dict(
        target_multiplier=2.0,
        start_date=START,
        end_date=END,
        initial_capital=30_000.0,
        approach="短線技術分析 + AI 輔助決策",
    )
    defaults.update(kwargs)
    return StrategyGoal(**defaults)

def make_entry(entry_date=None, pnl=500.0, trades=1, review="ok", plan="明天繼續", target_met=True) -> DailyEntry:
    return DailyEntry(
        date=entry_date or START,
        pnl=pnl,
        trades_count=trades,
        review=review,
        tomorrow_plan=plan,
        target_met=target_met,
    )


# ── StrategyGoal ──────────────────────────────────────────────────────────────

class TestStrategyGoal:
    def test_has_required_fields(self):
        g = make_goal()
        assert g.target_multiplier == 2.0
        assert g.initial_capital == 30_000.0
        assert g.approach != ""

    def test_start_before_end(self):
        g = make_goal()
        assert g.start_date < g.end_date

    def test_target_multiplier_above_one(self):
        g = make_goal(target_multiplier=1.5)
        assert g.target_multiplier > 1.0

    def test_total_days_property(self):
        g = make_goal(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30))
        assert g.total_days == 29

    def test_target_value_property(self):
        g = make_goal(initial_capital=30_000, target_multiplier=2.0)
        assert g.target_value == pytest.approx(60_000.0)


# ── DailyEntry ────────────────────────────────────────────────────────────────

class TestDailyEntry:
    def test_has_required_fields(self):
        e = make_entry()
        assert hasattr(e, "date")
        assert hasattr(e, "pnl")
        assert hasattr(e, "trades_count")
        assert hasattr(e, "review")
        assert hasattr(e, "tomorrow_plan")
        assert hasattr(e, "target_met")

    def test_pnl_can_be_negative(self):
        e = make_entry(pnl=-1000.0)
        assert e.pnl < 0


# ── save / load goal ──────────────────────────────────────────────────────────

class TestSaveLoadGoal:
    def test_roundtrip(self):
        g = make_goal(target_multiplier=1.5, initial_capital=50_000.0)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        save_goal(g, path)
        loaded = load_goal(path)
        assert loaded.target_multiplier == pytest.approx(1.5)
        assert loaded.initial_capital == pytest.approx(50_000.0)
        assert loaded.start_date == g.start_date
        assert loaded.approach == g.approach

    def test_load_nonexistent_returns_none(self):
        result = load_goal("/tmp/no_goal_xyz.json")
        assert result is None

    def test_saved_has_all_fields(self):
        g = make_goal()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        save_goal(g, path)
        with open(path) as f:
            data = json.load(f)
        for field in ("target_multiplier", "initial_capital", "approach", "start_date", "end_date"):
            assert field in data


# ── save / load daily entries ─────────────────────────────────────────────────

class TestSaveLoadDailyEntries:
    def test_save_and_load_single(self):
        e = make_entry(entry_date=date(2026, 4, 5), pnl=800.0)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        save_daily_entry(e, path)
        entries = load_daily_entries(path)
        assert len(entries) == 1
        assert entries[0].pnl == pytest.approx(800.0)

    def test_append_multiple(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        save_daily_entry(make_entry(entry_date=date(2026, 4, 1), pnl=100), path)
        save_daily_entry(make_entry(entry_date=date(2026, 4, 2), pnl=200), path)
        entries = load_daily_entries(path)
        assert len(entries) == 2

    def test_load_nonexistent_returns_empty(self):
        entries = load_daily_entries("/tmp/no_entries_xyz.jsonl")
        assert entries == []

    def test_date_preserved(self):
        e = make_entry(entry_date=date(2026, 4, 15))
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        save_daily_entry(e, path)
        loaded = load_daily_entries(path)
        assert loaded[0].date == date(2026, 4, 15)

    def test_target_met_preserved(self):
        e = make_entry(target_met=False)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        save_daily_entry(e, path)
        loaded = load_daily_entries(path)
        assert loaded[0].target_met is False


# ── calc_daily_target ─────────────────────────────────────────────────────────

class TestCalcDailyTarget:
    def test_basic_2x_in_30_days(self):
        g = make_goal(initial_capital=30_000, target_multiplier=2.0,
                      start_date=date(2026, 4, 1), end_date=date(2026, 4, 30))
        # target_pnl=30000, over 29 days ≈ 1034/day
        daily = calc_daily_target(g)
        assert daily == pytest.approx(30_000 / 29, rel=0.01)

    def test_1_5x_goal(self):
        g = make_goal(initial_capital=10_000, target_multiplier=1.5,
                      start_date=date(2026, 4, 1), end_date=date(2026, 4, 11))
        # target_pnl=5000, over 10 days = 500/day
        daily = calc_daily_target(g)
        assert daily == pytest.approx(500.0, rel=0.01)

    def test_returns_positive_for_growth_goal(self):
        assert calc_daily_target(make_goal()) > 0

    def test_zero_days_returns_zero(self):
        g = make_goal(start_date=date(2026, 4, 1), end_date=date(2026, 4, 1))
        assert calc_daily_target(g) == 0.0


# ── calc_progress ─────────────────────────────────────────────────────────────

class TestCalcProgress:
    def test_returns_strategy_progress(self):
        g = make_goal()
        result = calc_progress(g, current_value=32_000.0, today=date(2026, 4, 10))
        assert isinstance(result, StrategyProgress)

    def test_days_elapsed(self):
        g = make_goal(start_date=date(2026, 4, 1))
        result = calc_progress(g, current_value=30_000.0, today=date(2026, 4, 11))
        assert result.days_elapsed == 10

    def test_days_remaining(self):
        g = make_goal(end_date=date(2026, 4, 30))
        result = calc_progress(g, current_value=30_000.0, today=date(2026, 4, 20))
        assert result.days_remaining == 10

    def test_total_pnl(self):
        g = make_goal(initial_capital=30_000)
        result = calc_progress(g, current_value=35_000.0, today=START)
        assert result.total_pnl == pytest.approx(5_000.0)

    def test_total_pnl_pct(self):
        g = make_goal(initial_capital=30_000)
        result = calc_progress(g, current_value=33_000.0, today=START)
        assert result.total_pnl_pct == pytest.approx(10.0)

    def test_on_track_when_ahead(self):
        g = make_goal(initial_capital=30_000, target_multiplier=2.0,
                      start_date=date(2026, 4, 1), end_date=date(2026, 4, 30))
        # 15 days in, halfway → need 45000, currently 50000 → on track
        result = calc_progress(g, current_value=50_000.0, today=date(2026, 4, 16))
        assert result.on_track is True

    def test_not_on_track_when_behind(self):
        g = make_goal(initial_capital=30_000, target_multiplier=2.0,
                      start_date=date(2026, 4, 1), end_date=date(2026, 4, 30))
        # 15 days in, halfway → expected ~45000, actually 30001 → not on track
        result = calc_progress(g, current_value=30_001.0, today=date(2026, 4, 16))
        assert result.on_track is False

    def test_required_daily_pnl_decreases_when_ahead(self):
        g = make_goal(initial_capital=30_000, target_multiplier=2.0,
                      start_date=date(2026, 4, 1), end_date=date(2026, 4, 30))
        original_daily = calc_daily_target(g)
        result = calc_progress(g, current_value=50_000.0, today=date(2026, 4, 16))
        assert result.required_daily_pnl < original_daily

    def test_completion_pct_at_start(self):
        g = make_goal(initial_capital=30_000, target_multiplier=2.0)
        result = calc_progress(g, current_value=30_000.0, today=START)
        assert result.completion_pct == pytest.approx(0.0)

    def test_completion_pct_at_goal(self):
        g = make_goal(initial_capital=30_000, target_multiplier=2.0)
        result = calc_progress(g, current_value=60_000.0, today=START)
        assert result.completion_pct == pytest.approx(100.0)


# ── check_daily_target ────────────────────────────────────────────────────────

class TestCheckDailyTarget:
    def test_met_when_pnl_equals_target(self):
        assert check_daily_target(daily_pnl=1000.0, daily_target=1000.0) is True

    def test_met_when_pnl_exceeds_target(self):
        assert check_daily_target(daily_pnl=1500.0, daily_target=1000.0) is True

    def test_not_met_when_pnl_below_target(self):
        assert check_daily_target(daily_pnl=800.0, daily_target=1000.0) is False

    def test_not_met_when_pnl_negative(self):
        assert check_daily_target(daily_pnl=-200.0, daily_target=1000.0) is False

    def test_negative_target_always_met_with_any_profit(self):
        assert check_daily_target(daily_pnl=1.0, daily_target=-100.0) is True


# ── generate_daily_review ─────────────────────────────────────────────────────

class TestGenerateDailyReview:
    @patch("strategy_tracker.call_ollama")
    def test_returns_dict_with_review_and_plan(self, mock_ollama):
        mock_ollama.return_value = '{"review": "今天 RSI 低點進場成功", "tomorrow_plan": "明天繼續觀察均線"}'
        result = generate_daily_review(
            goal=make_goal(),
            today_pnl=500.0,
            daily_target=1034.0,
            trades=["買入台積電 1 張"],
            yesterday_plan="嘗試均線策略",
        )
        assert "review" in result
        assert "tomorrow_plan" in result
        assert len(result["review"]) > 0
        assert len(result["tomorrow_plan"]) > 0

    @patch("strategy_tracker.call_ollama")
    def test_includes_goal_in_prompt(self, mock_ollama):
        mock_ollama.return_value = '{"review": "ok", "tomorrow_plan": "繼續"}'
        make_goal(approach="短線技術分析")
        generate_daily_review(make_goal(approach="短線技術分析"), 500.0, 1000.0, [], "")
        prompt = mock_ollama.call_args[0][1]
        assert "短線技術分析" in prompt or "2.0" in prompt or "30,000" in prompt

    @patch("strategy_tracker.call_ollama", side_effect=Exception("timeout"))
    def test_ollama_failure_returns_defaults(self, mock_ollama):
        result = generate_daily_review(make_goal(), 0.0, 1000.0, [], "")
        assert "review" in result
        assert "tomorrow_plan" in result
