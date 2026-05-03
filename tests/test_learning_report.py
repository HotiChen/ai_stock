from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from learning_report import (
    collect_report_data,
    compute_plan_stats,
    compute_actual_path_stats,
    build_report_prompt,
    generate_learning_report,
    PlanPerformanceSummary,
    LearningReport,
)
from daily_tracker import DailyTrackRecord, PlanResult

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_result(plan_type: str, pnl: float | None, pnl_pct: float | None,
                 wins: int = 1, losses: int = 0, is_actual: bool = False) -> PlanResult:
    closing = (1_000.0 + pnl) if pnl is not None else None
    return PlanResult(
        plan_type=plan_type,
        pnl=pnl,
        pnl_pct=pnl_pct,
        closing_capital=closing,
        win_trades=wins,
        loss_trades=losses,
        is_actual=is_actual,
    )


def _make_record(day_offset: int, chosen: str,
                 agg_pnl=500.0, bal_pnl=300.0, con_pnl=150.0) -> DailyTrackRecord:
    d = date(2026, 4, 1) + timedelta(days=day_offset)
    results = [
        _make_result("aggressive",   agg_pnl, agg_pnl / 30_000 * 100,
                     wins=1, losses=0, is_actual=(chosen == "aggressive")),
        _make_result("balanced",     bal_pnl, bal_pnl / 30_000 * 100,
                     wins=1, losses=0, is_actual=(chosen == "balanced")),
        _make_result("conservative", con_pnl, con_pnl / 30_000 * 100,
                     wins=1, losses=0, is_actual=(chosen == "conservative")),
    ]
    return DailyTrackRecord(
        date=d, starting_capital=30_000.0,
        chosen_plan_type=chosen, results=results,
    )


def _seven_records() -> list[DailyTrackRecord]:
    choices = ["aggressive", "balanced", "balanced", "conservative",
               "aggressive", "balanced", "conservative"]
    return [_make_record(i, choices[i]) for i in range(7)]


# ── collect_report_data ───────────────────────────────────────────────────────

class TestCollectReportData:
    def test_returns_list(self, tmp_path):
        from daily_tracker import save_day_record
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = collect_report_data(7, track_dir=str(tmp_path))
        assert isinstance(result, list)

    def test_returns_last_n_days(self, tmp_path):
        from daily_tracker import save_day_record
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = collect_report_data(7, track_dir=str(tmp_path))
        assert len(result) == 7

    def test_sorted_oldest_first(self, tmp_path):
        from daily_tracker import save_day_record
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = collect_report_data(7, track_dir=str(tmp_path))
        dates = [r.date for r in result]
        assert dates == sorted(dates)

    def test_returns_empty_if_no_data(self, tmp_path):
        result = collect_report_data(7, track_dir=str(tmp_path))
        assert result == []

    def test_caps_at_n_days(self, tmp_path):
        """有 10 筆資料，只要求 7 天 → 回傳最近 7 筆"""
        from daily_tracker import save_day_record
        for i in range(10):
            save_day_record(_make_record(i, "balanced"), track_dir=str(tmp_path))
        result = collect_report_data(7, track_dir=str(tmp_path))
        assert len(result) == 7

    def test_partial_data_returns_what_exists(self, tmp_path):
        """只有 3 天資料，要求 7 天 → 回傳 3 筆（不足不報錯）"""
        from daily_tracker import save_day_record
        for i in range(3):
            save_day_record(_make_record(i, "balanced"), track_dir=str(tmp_path))
        result = collect_report_data(7, track_dir=str(tmp_path))
        assert len(result) == 3


# ── compute_plan_stats ────────────────────────────────────────────────────────

class TestComputePlanStats:
    def test_returns_plan_performance_summary(self):
        records = _seven_records()
        result = compute_plan_stats(records, "aggressive")
        assert isinstance(result, PlanPerformanceSummary)

    def test_correct_plan_type(self):
        records = _seven_records()
        result = compute_plan_stats(records, "balanced")
        assert result.plan_type == "balanced"

    def test_correct_total_pnl(self):
        # aggressive: 500 TWD * 7 days
        records = _seven_records()
        result = compute_plan_stats(records, "aggressive")
        assert result.total_pnl == pytest.approx(500.0 * 7)

    def test_correct_win_rate(self):
        # all wins=1, losses=0 → 100%
        records = _seven_records()
        result = compute_plan_stats(records, "balanced")
        assert result.win_rate == pytest.approx(1.0)

    def test_win_rate_with_mixed_results(self):
        record = _make_record(0, "aggressive", agg_pnl=100.0, bal_pnl=-50.0, con_pnl=30.0)
        # Override to have 1 win 1 loss for aggressive
        record.results[0].win_trades = 1
        record.results[0].loss_trades = 1
        result = compute_plan_stats([record], "aggressive")
        assert result.win_rate == pytest.approx(0.5)

    def test_days_tracked_count(self):
        records = _seven_records()
        result = compute_plan_stats(records, "conservative")
        assert result.days_tracked == 7

    def test_skips_unsettled_records(self):
        """pnl=None 的記錄不計入統計"""
        records = [_make_record(0, "balanced")]
        records[0].results[0].pnl = None       # aggressive unsettled
        records[0].results[0].pnl_pct = None
        result = compute_plan_stats(records, "aggressive")
        assert result.days_tracked == 0
        assert result.total_pnl == 0.0

    def test_avg_pnl_pct_calculated(self):
        records = _seven_records()
        result = compute_plan_stats(records, "aggressive")
        # 500 / 30000 * 100 = 1.667%
        assert result.avg_pnl_pct == pytest.approx(500.0 / 30_000 * 100, abs=0.01)


# ── compute_actual_path_stats ─────────────────────────────────────────────────

class TestComputeActualPathStats:
    def test_returns_plan_performance_summary(self):
        records = _seven_records()
        result = compute_actual_path_stats(records)
        assert isinstance(result, PlanPerformanceSummary)

    def test_plan_type_is_actual(self):
        records = _seven_records()
        result = compute_actual_path_stats(records)
        assert result.plan_type == "actual"

    def test_only_counts_is_actual_results(self):
        """只加總 is_actual=True 的 PlanResult"""
        records = _seven_records()
        # day 0: chosen=aggressive → pnl=500
        # day 1: chosen=balanced   → pnl=300
        # day 2: chosen=balanced   → pnl=300
        # day 3: chosen=conservative→ pnl=150
        # day 4: chosen=aggressive → pnl=500
        # day 5: chosen=balanced   → pnl=300
        # day 6: chosen=conservative→ pnl=150
        expected_pnl = 500 + 300 + 300 + 150 + 500 + 300 + 150
        result = compute_actual_path_stats(records)
        assert result.total_pnl == pytest.approx(expected_pnl)

    def test_days_tracked_equals_settled_count(self):
        records = _seven_records()
        result = compute_actual_path_stats(records)
        assert result.days_tracked == 7

    def test_empty_records_returns_zeros(self):
        result = compute_actual_path_stats([])
        assert result.total_pnl == 0.0
        assert result.days_tracked == 0


# ── build_report_prompt ───────────────────────────────────────────────────────

class TestBuildReportPrompt:
    def test_returns_string(self):
        records = _seven_records()
        result = build_report_prompt(records, 7)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_includes_period_days(self):
        records = _seven_records()
        prompt = build_report_prompt(records, 7)
        assert "7" in prompt

    def test_includes_all_three_plan_types(self):
        records = _seven_records()
        prompt = build_report_prompt(records, 7)
        assert "aggressive" in prompt
        assert "balanced" in prompt
        assert "conservative" in prompt

    def test_includes_actual_path_section(self):
        records = _seven_records()
        prompt = build_report_prompt(records, 7)
        assert "實際" in prompt or "actual" in prompt.lower()

    def test_includes_shadow_tracking_label(self):
        records = _seven_records()
        prompt = build_report_prompt(records, 7)
        assert "影子" in prompt or "shadow" in prompt.lower() or "反事實" in prompt

    def test_includes_pnl_numbers(self):
        records = _seven_records()
        prompt = build_report_prompt(records, 7)
        assert "TWD" in prompt or "損益" in prompt

    def test_asks_for_system_recommendation_json(self):
        records = _seven_records()
        prompt = build_report_prompt(records, 7)
        assert "system_recommendation" in prompt

    def test_asks_for_key_insights(self):
        records = _seven_records()
        prompt = build_report_prompt(records, 7)
        assert "key_insights" in prompt

    def test_empty_records_returns_empty_string(self):
        result = build_report_prompt([], 7)
        assert result == ""

    def test_includes_date_range(self):
        records = _seven_records()
        prompt = build_report_prompt(records, 7)
        assert "2026-04-01" in prompt or "04-01" in prompt


# ── generate_learning_report ──────────────────────────────────────────────────

SAMPLE_AI_RESPONSE = """
## 7 天策略學習報告分析

在過去 7 天的操作中，aggressive 計劃表現最為突出，累積損益最高。
使用者在第 3、6 天選擇 conservative，錯過了較大的獲利機會。

### 主要發現
1. Aggressive 計劃在震盪市中反而表現穩定
2. 使用者傾向於在連跌後保守，但這往往是反彈時機
3. Balanced 計劃風險調整後報酬最佳

{"system_recommendation": "aggressive", "key_insights": ["aggressive 累積報酬最高", "使用者過度保守導致錯過機會", "下週建議提高部位至 aggressive"]}
"""

class TestGenerateLearningReport:
    @patch("learning_report.call_gemini")
    def test_returns_learning_report(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert isinstance(result, LearningReport)

    @patch("learning_report.call_gemini")
    def test_system_recommendation_is_valid(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert result.system_recommendation in ("aggressive", "balanced", "conservative")

    @patch("learning_report.call_gemini")
    def test_system_recommendation_parsed_from_json(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert result.system_recommendation == "aggressive"

    @patch("learning_report.call_gemini")
    def test_key_insights_parsed(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert isinstance(result.key_insights, list)
        assert len(result.key_insights) == 3

    @patch("learning_report.call_gemini")
    def test_period_days_in_report(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert result.period_days == 7

    @patch("learning_report.call_gemini")
    def test_date_range_in_report(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert result.start_date <= result.end_date

    @patch("learning_report.call_gemini")
    def test_gemini_called_with_prompt(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        generate_learning_report(days=7, track_dir=str(tmp_path))
        assert mock_gemini.called
        prompt = mock_gemini.call_args[0][0]
        assert "7" in prompt and "aggressive" in prompt

    @patch("learning_report.call_gemini")
    def test_returns_none_if_insufficient_data(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        # Only 3 days, need 7
        for i in range(3):
            save_day_record(_make_record(i, "balanced"), track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert result is None

    @patch("learning_report.call_gemini")
    def test_gemini_failure_returns_none(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = ""   # empty = failure
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert result is None

    def test_invalid_days_raises(self, tmp_path):
        with pytest.raises(ValueError):
            generate_learning_report(days=10, track_dir=str(tmp_path))

    @patch("learning_report.call_gemini")
    def test_supports_14_days(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for i in range(14):
            save_day_record(_make_record(i, "balanced"), track_dir=str(tmp_path))
        result = generate_learning_report(days=14, track_dir=str(tmp_path))
        assert result is not None
        assert result.period_days == 14

    @patch("learning_report.call_gemini")
    def test_supports_28_days(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for i in range(28):
            save_day_record(_make_record(i, "conservative"), track_dir=str(tmp_path))
        result = generate_learning_report(days=28, track_dir=str(tmp_path))
        assert result is not None
        assert result.period_days == 28

    @patch("learning_report.call_gemini")
    def test_actual_path_and_shadow_paths_present(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = SAMPLE_AI_RESPONSE
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert isinstance(result.actual_path, PlanPerformanceSummary)
        assert len(result.shadow_paths) == 3   # all 3 plan types
        plan_types = {s.plan_type for s in result.shadow_paths}
        assert plan_types == {"aggressive", "balanced", "conservative"}

    @patch("learning_report.call_gemini")
    def test_invalid_recommendation_falls_back_to_balanced(self, mock_gemini, tmp_path):
        from daily_tracker import save_day_record
        mock_gemini.return_value = '{"system_recommendation": "invalid_plan", "key_insights": []}'
        for rec in _seven_records():
            save_day_record(rec, track_dir=str(tmp_path))
        result = generate_learning_report(days=7, track_dir=str(tmp_path))
        assert result.system_recommendation == "balanced"
