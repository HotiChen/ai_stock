"""Tests for briefing accuracy validation added to learning_report."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from daily_tracker import DailyTrackRecord, PlanResult
from morning_briefing import MarketData, MorningBriefing
from learning_report import (
    BriefingDayAccuracy,
    BriefingAccuracyReport,
    compute_briefing_accuracy,
    load_briefings_for_period,
    build_briefing_section,
    LearningReport,
    generate_learning_report,
)

# ── helpers ───────────────────────────────────────────────────────────────────

BASE_DATE = date(2026, 4, 1)

def _date(offset: int) -> date:
    return BASE_DATE + timedelta(days=offset)


def _briefing(day_offset: int, outlook: str, vix: float = 18.0) -> MorningBriefing:
    d = _date(day_offset)
    md = MarketData(
        date=d, twse_index=38_000.0, twse_volume=None,
        foreign_net=-200.0, trust_net=50.0, margin_balance=None,
        sp500=7000.0, nasdaq=24000.0, sox=10000.0, vix=vix,
        news_items=[],
    )
    return MorningBriefing(
        date=d,
        generated_at=datetime(d.year, d.month, d.day, 8, 0, 0),
        market_data=md,
        ai_analysis="分析",
        market_outlook=outlook,
        key_risks=[], key_opportunities=[], sector_focus=[],
    )


def _result(plan_type: str, pnl: float, is_actual: bool = False) -> PlanResult:
    return PlanResult(
        plan_type=plan_type,
        pnl=pnl,
        pnl_pct=round(pnl / 30_000 * 100, 2),
        closing_capital=30_000.0 + pnl,
        win_trades=1 if pnl > 0 else 0,
        loss_trades=0 if pnl > 0 else 1,
        is_actual=is_actual,
    )


def _record(day_offset: int, chosen: str, actual_pnl: float) -> DailyTrackRecord:
    d = _date(day_offset)
    results = [
        _result("aggressive",   actual_pnl * 1.2, is_actual=(chosen == "aggressive")),
        _result("balanced",     actual_pnl,       is_actual=(chosen == "balanced")),
        _result("conservative", actual_pnl * 0.7, is_actual=(chosen == "conservative")),
    ]
    return DailyTrackRecord(
        date=d, starting_capital=30_000.0,
        chosen_plan_type=chosen, results=results,
    )


# ── BriefingDayAccuracy ───────────────────────────────────────────────────────

class TestBriefingDayAccuracy:
    def test_fields(self):
        acc = BriefingDayAccuracy(
            date=_date(0),
            predicted_outlook="bullish",
            actual_pnl_pct=2.0,
            correct=True,
            vix=16.5,
        )
        assert acc.date == _date(0)
        assert acc.predicted_outlook == "bullish"
        assert acc.correct is True

    def test_correct_bullish_positive_pnl(self):
        acc = BriefingDayAccuracy(
            date=_date(0),
            predicted_outlook="bullish",
            actual_pnl_pct=1.5,
            correct=True,
            vix=16.0,
        )
        assert acc.correct

    def test_optional_vix(self):
        acc = BriefingDayAccuracy(
            date=_date(0),
            predicted_outlook="neutral",
            actual_pnl_pct=0.0,
            correct=False,
            vix=None,
        )
        assert acc.vix is None


# ── BriefingAccuracyReport ────────────────────────────────────────────────────

class TestBriefingAccuracyReport:
    def _make_report(self) -> BriefingAccuracyReport:
        days = [
            BriefingDayAccuracy(_date(0), "bullish",  2.0,  True,  16.0),
            BriefingDayAccuracy(_date(1), "bearish",  -1.0, True,  22.0),
            BriefingDayAccuracy(_date(2), "bullish",  -0.5, False, 18.0),
            BriefingDayAccuracy(_date(3), "bearish",  1.0,  False, 20.0),
        ]
        return BriefingAccuracyReport(per_day=days)

    def test_total_days(self):
        r = self._make_report()
        assert r.total_days == 4

    def test_accuracy_rate(self):
        r = self._make_report()
        assert r.accuracy_rate == pytest.approx(0.5)   # 2/4

    def test_bullish_accuracy(self):
        r = self._make_report()
        # bullish: day0 correct, day2 wrong → 1/2 = 0.5
        assert r.bullish_accuracy == pytest.approx(0.5)

    def test_bearish_accuracy(self):
        r = self._make_report()
        # bearish: day1 correct, day3 wrong → 1/2 = 0.5
        assert r.bearish_accuracy == pytest.approx(0.5)

    def test_empty_per_day(self):
        r = BriefingAccuracyReport(per_day=[])
        assert r.total_days == 0
        assert r.accuracy_rate == 0.0
        assert r.bullish_accuracy == 0.0
        assert r.bearish_accuracy == 0.0

    def test_all_correct(self):
        days = [
            BriefingDayAccuracy(_date(i), "bullish", 1.0, True, 16.0)
            for i in range(5)
        ]
        r = BriefingAccuracyReport(per_day=days)
        assert r.accuracy_rate == pytest.approx(1.0)


# ── compute_briefing_accuracy ─────────────────────────────────────────────────

class TestComputeBriefingAccuracy:
    def _records(self):
        return [
            _record(0, "balanced",  500.0),   # pnl > 0 → bullish correct
            _record(1, "aggressive", -200.0),  # pnl < 0 → bearish correct
            _record(2, "balanced",  300.0),    # pnl > 0 → bearish wrong
        ]

    def _briefings(self):
        return [
            _briefing(0, "bullish"),
            _briefing(1, "bearish"),
            _briefing(2, "bearish"),
        ]

    def test_returns_report(self):
        r = compute_briefing_accuracy(self._records(), self._briefings())
        assert isinstance(r, BriefingAccuracyReport)

    def test_matches_days_with_both_record_and_briefing(self):
        r = compute_briefing_accuracy(self._records(), self._briefings())
        assert r.total_days == 3

    def test_bullish_correct_when_pnl_positive(self):
        r = compute_briefing_accuracy(self._records(), self._briefings())
        day0 = next(d for d in r.per_day if d.date == _date(0))
        assert day0.correct is True
        assert day0.predicted_outlook == "bullish"

    def test_bearish_correct_when_pnl_negative(self):
        r = compute_briefing_accuracy(self._records(), self._briefings())
        day1 = next(d for d in r.per_day if d.date == _date(1))
        assert day1.correct is True
        assert day1.predicted_outlook == "bearish"

    def test_bearish_wrong_when_pnl_positive(self):
        r = compute_briefing_accuracy(self._records(), self._briefings())
        day2 = next(d for d in r.per_day if d.date == _date(2))
        assert day2.correct is False

    def test_record_without_briefing_skipped(self):
        records = self._records() + [_record(10, "balanced", 100.0)]
        r = compute_briefing_accuracy(records, self._briefings())
        assert r.total_days == 3  # day10 has no briefing

    def test_neutral_outlook_not_counted_as_correct(self):
        records = [_record(0, "balanced", 500.0)]
        briefings = [_briefing(0, "neutral")]
        r = compute_briefing_accuracy(records, briefings)
        day0 = r.per_day[0]
        assert day0.correct is False  # neutral = 不預測

    def test_pnl_from_actual_result(self):
        records = [_record(0, "balanced", 1000.0)]
        briefings = [_briefing(0, "bullish")]
        r = compute_briefing_accuracy(records, briefings)
        day0 = r.per_day[0]
        # actual result (is_actual=True) pnl_pct should be positive
        assert day0.actual_pnl_pct > 0

    def test_vix_captured_from_briefing(self):
        records = [_record(0, "balanced", 200.0)]
        briefings = [_briefing(0, "bullish", vix=23.5)]
        r = compute_briefing_accuracy(records, briefings)
        assert r.per_day[0].vix == pytest.approx(23.5)

    def test_empty_records(self):
        r = compute_briefing_accuracy([], self._briefings())
        assert r.total_days == 0

    def test_empty_briefings(self):
        r = compute_briefing_accuracy(self._records(), [])
        assert r.total_days == 0


# ── load_briefings_for_period ─────────────────────────────────────────────────

class TestLoadBriefingsForPeriod:
    def test_returns_list(self, tmp_path):
        result = load_briefings_for_period(_date(0), _date(6), str(tmp_path))
        assert isinstance(result, list)

    def test_loads_briefings_in_range(self, tmp_path):
        import json
        # Save a briefing JSON manually
        b = _briefing(0, "bullish")
        bdir = tmp_path / "morning_briefings"
        bdir.mkdir()
        # Simulate what save_briefing would produce — use load_briefing compatible format
        from morning_briefing import save_briefing
        save_briefing(b, path=str(bdir / f"{b.date.isoformat()}.json"))
        result = load_briefings_for_period(b.date, b.date, str(bdir))
        assert len(result) == 1
        assert result[0].date == b.date

    def test_ignores_out_of_range(self, tmp_path):
        from morning_briefing import save_briefing
        bdir = tmp_path / "morning_briefings"
        bdir.mkdir()
        b_in  = _briefing(0, "bullish")
        b_out = _briefing(10, "bearish")
        save_briefing(b_in,  path=str(bdir / f"{b_in.date.isoformat()}.json"))
        save_briefing(b_out, path=str(bdir / f"{b_out.date.isoformat()}.json"))
        result = load_briefings_for_period(b_in.date, b_in.date, str(bdir))
        assert len(result) == 1

    def test_empty_dir_returns_empty(self, tmp_path):
        result = load_briefings_for_period(_date(0), _date(6), str(tmp_path))
        assert result == []


# ── build_briefing_section ────────────────────────────────────────────────────

class TestBuildBriefingSection:
    def _accuracy(self) -> BriefingAccuracyReport:
        days = [
            BriefingDayAccuracy(_date(0), "bullish",  2.0,  True,  16.0),
            BriefingDayAccuracy(_date(1), "bearish", -1.0,  True,  22.0),
            BriefingDayAccuracy(_date(2), "bullish", -0.5,  False, 18.0),
        ]
        return BriefingAccuracyReport(per_day=days)

    def test_returns_string(self):
        section = build_briefing_section(self._accuracy())
        assert isinstance(section, str)

    def test_includes_accuracy_rate(self):
        section = build_briefing_section(self._accuracy())
        # 2/3 ≈ 66.7%
        assert "66" in section or "67" in section or "2/3" in section

    def test_includes_bullish_accuracy(self):
        section = build_briefing_section(self._accuracy())
        # bullish: 1/2 = 50%
        assert "50" in section or "1/2" in section

    def test_includes_bearish_accuracy(self):
        section = build_briefing_section(self._accuracy())
        # bearish: 1/1 = 100%
        assert "100" in section

    def test_includes_section_header(self):
        section = build_briefing_section(self._accuracy())
        assert "簡報" in section or "Briefing" in section or "準確" in section

    def test_empty_accuracy_handled(self):
        section = build_briefing_section(BriefingAccuracyReport(per_day=[]))
        assert isinstance(section, str)


# ── LearningReport briefing_accuracy field ───────────────────────────────────

class TestLearningReportBriefingAccuracy:
    def test_has_briefing_accuracy_field(self):
        from learning_report import PlanPerformanceSummary
        dummy_summary = PlanPerformanceSummary("actual", 0.0, 0.0, 0.0, 0)
        report = LearningReport(
            generated_at=_date(0),
            period_days=7,
            start_date=_date(0),
            end_date=_date(6),
            actual_path=dummy_summary,
            shadow_paths=[],
            system_recommendation="balanced",
            ai_analysis="test",
            briefing_accuracy=None,
        )
        assert report.briefing_accuracy is None

    def test_briefing_accuracy_optional(self):
        from learning_report import PlanPerformanceSummary
        dummy_summary = PlanPerformanceSummary("actual", 0.0, 0.0, 0.0, 0)
        # Should work without briefing_accuracy kwarg (defaults to None)
        report = LearningReport(
            generated_at=_date(0),
            period_days=7,
            start_date=_date(0),
            end_date=_date(6),
            actual_path=dummy_summary,
            shadow_paths=[],
            system_recommendation="balanced",
            ai_analysis="test",
        )
        assert hasattr(report, "briefing_accuracy")


# ── generate_learning_report with briefing_dir ───────────────────────────────

class TestGenerateLearningReportWithBriefings:
    def _make_records_and_briefings(self, tmp_path, n=7):
        """Create n days of records + briefings, save briefings to tmp_path."""
        from morning_briefing import save_briefing
        bdir = tmp_path / "morning_briefings"
        bdir.mkdir()

        choices = ["balanced"] * n
        records = [_record(i, choices[i], 300.0) for i in range(n)]
        briefings = [_briefing(i, "bullish") for i in range(n)]

        for b in briefings:
            save_briefing(b, path=str(bdir / f"{b.date.isoformat()}.json"))

        return records, briefings, str(bdir)

    @patch("learning_report.collect_report_data")
    @patch("learning_report.call_gemini")
    def test_includes_briefing_accuracy_when_dir_given(
        self, mock_gemini, mock_collect, tmp_path
    ):
        import json
        records, _, bdir = self._make_records_and_briefings(tmp_path, n=7)
        mock_collect.return_value = records
        mock_gemini.return_value = (
            '分析內容\n{"system_recommendation": "balanced", "key_insights": ["a", "b", "c"]}'
        )
        report = generate_learning_report(days=7, briefing_dir=bdir)
        assert report is not None
        assert report.briefing_accuracy is not None
        assert report.briefing_accuracy.total_days > 0

    @patch("learning_report.collect_report_data")
    @patch("learning_report.call_gemini")
    def test_no_briefing_dir_still_works(self, mock_gemini, mock_collect, tmp_path):
        records = [_record(i, "balanced", 300.0) for i in range(7)]
        mock_collect.return_value = records
        mock_gemini.return_value = (
            '分析\n{"system_recommendation": "balanced", "key_insights": ["x"]}'
        )
        report = generate_learning_report(days=7)
        assert report is not None
        # briefing_accuracy may be None or empty when no dir given
        assert report.briefing_accuracy is None or report.briefing_accuracy.total_days == 0

    @patch("learning_report.collect_report_data")
    @patch("learning_report.call_gemini")
    def test_briefing_section_included_in_prompt(
        self, mock_gemini, mock_collect, tmp_path
    ):
        records, _, bdir = self._make_records_and_briefings(tmp_path, n=7)
        mock_collect.return_value = records
        mock_gemini.return_value = (
            '分析\n{"system_recommendation": "balanced", "key_insights": ["x"]}'
        )
        generate_learning_report(days=7, briefing_dir=bdir)
        prompt = mock_gemini.call_args[0][0]
        # prompt should contain briefing accuracy section
        assert "簡報" in prompt or "準確" in prompt or "Briefing" in prompt
