from __future__ import annotations

import json
import tempfile
from datetime import date, datetime

import pytest

from research_db import (
    init_db,
    save_market_context,
    load_latest_market_context,
    save_stock_analysis,
    load_stock_analysis,
    save_plan_execution,
    load_active_execution,
    stop_execution,
    save_execution_record,
    load_execution_records,
    save_daily_summary,
    load_daily_summaries,
    MarketContextRow,
    StockAnalysisRow,
    PlanExecutionRow,
    ExecutionRecordRow,
    DailySummaryRow,
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


# ── init_db ───────────────────────────────────────────────────────────────────

class TestInitDb:
    def test_creates_file(self, tmp_path):
        path = str(tmp_path / "new.db")
        init_db(path)
        import os
        assert os.path.exists(path)

    def test_idempotent(self, tmp_path):
        path = str(tmp_path / "new.db")
        init_db(path)
        init_db(path)  # should not raise


# ── MarketContext ─────────────────────────────────────────────────────────────

def make_market_ctx(**kw) -> MarketContextRow:
    defaults = dict(
        recorded_at=datetime(2026, 4, 27, 9, 0),
        sp500_change=0.5,
        nasdaq_change=1.2,
        sox_change=2.1,
        vix=18.0,
        sentiment="bullish",
        news_headlines=["Fed 維持利率不變", "台積電 ADR 上漲"],
    )
    defaults.update(kw)
    return MarketContextRow(**defaults)


class TestMarketContext:
    def test_save_and_load(self, db_path):
        ctx = make_market_ctx()
        save_market_context(ctx, db_path)
        loaded = load_latest_market_context(db_path)
        assert loaded is not None
        assert loaded.sp500_change == pytest.approx(0.5)
        assert loaded.sentiment == "bullish"

    def test_load_returns_latest(self, db_path):
        save_market_context(make_market_ctx(sp500_change=1.0, recorded_at=datetime(2026, 4, 27, 9, 0)), db_path)
        save_market_context(make_market_ctx(sp500_change=2.0, recorded_at=datetime(2026, 4, 27, 10, 0)), db_path)
        loaded = load_latest_market_context(db_path)
        assert loaded.sp500_change == pytest.approx(2.0)

    def test_load_empty_returns_none(self, db_path):
        assert load_latest_market_context(db_path) is None

    def test_news_headlines_preserved(self, db_path):
        ctx = make_market_ctx(news_headlines=["新聞A", "新聞B"])
        save_market_context(ctx, db_path)
        loaded = load_latest_market_context(db_path)
        assert "新聞A" in loaded.news_headlines


# ── StockAnalysis ─────────────────────────────────────────────────────────────

def make_stock_analysis(**kw) -> StockAnalysisRow:
    defaults = dict(
        analyzed_at=datetime(2026, 4, 27, 9, 10),
        code="2330",
        name="台積電",
        signal="buy",
        confidence=8,
        summary="RSI 低點，法人買超，短線偏多",
        factors={"trend": "up", "news": "positive", "us_market": "bullish"},
    )
    defaults.update(kw)
    return StockAnalysisRow(**defaults)


class TestStockAnalysis:
    def test_save_and_load(self, db_path):
        row = make_stock_analysis()
        save_stock_analysis(row, db_path)
        loaded = load_stock_analysis("2330", db_path)
        assert loaded is not None
        assert loaded.signal == "buy"
        assert loaded.confidence == 8

    def test_load_returns_latest_for_code(self, db_path):
        save_stock_analysis(make_stock_analysis(confidence=6, analyzed_at=datetime(2026, 4, 27, 9, 0)), db_path)
        save_stock_analysis(make_stock_analysis(confidence=9, analyzed_at=datetime(2026, 4, 27, 10, 0)), db_path)
        loaded = load_stock_analysis("2330", db_path)
        assert loaded.confidence == 9

    def test_load_missing_code_returns_none(self, db_path):
        assert load_stock_analysis("9999", db_path) is None

    def test_factors_dict_preserved(self, db_path):
        save_stock_analysis(make_stock_analysis(factors={"trend": "down"}), db_path)
        loaded = load_stock_analysis("2330", db_path)
        assert loaded.factors["trend"] == "down"


# ── PlanExecution ─────────────────────────────────────────────────────────────

def make_plan_execution(**kw) -> PlanExecutionRow:
    defaults = dict(
        execution_id="exec-001",
        created_at=datetime(2026, 4, 27, 9, 0),
        plan_type="balanced",
        plan_json={"description": "均衡策略"},
        status="active",
    )
    defaults.update(kw)
    return PlanExecutionRow(**defaults)


class TestPlanExecution:
    def test_save_and_load_active(self, db_path):
        row = make_plan_execution()
        save_plan_execution(row, db_path)
        loaded = load_active_execution(db_path)
        assert loaded is not None
        assert loaded.execution_id == "exec-001"
        assert loaded.plan_type == "balanced"

    def test_no_active_returns_none(self, db_path):
        assert load_active_execution(db_path) is None

    def test_stop_changes_status(self, db_path):
        save_plan_execution(make_plan_execution(), db_path)
        stop_execution("exec-001", db_path)
        loaded = load_active_execution(db_path)
        assert loaded is None  # no more active

    def test_plan_json_preserved(self, db_path):
        save_plan_execution(make_plan_execution(plan_json={"key": "value"}), db_path)
        loaded = load_active_execution(db_path)
        assert loaded.plan_json["key"] == "value"


# ── ExecutionRecord ───────────────────────────────────────────────────────────

def make_exec_record(**kw) -> ExecutionRecordRow:
    defaults = dict(
        execution_id="exec-001",
        recorded_at=datetime(2026, 4, 27, 9, 30),
        code="2330",
        name="台積電",
        action="buy",
        quantity=1,
        price=850.0,
        simulated_pnl=0.0,
        reason="RSI 低點",
    )
    defaults.update(kw)
    return ExecutionRecordRow(**defaults)


class TestExecutionRecord:
    def test_save_and_load(self, db_path):
        rec = make_exec_record()
        save_execution_record(rec, db_path)
        records = load_execution_records("exec-001", db_path)
        assert len(records) == 1
        assert records[0].code == "2330"
        assert records[0].price == pytest.approx(850.0)

    def test_multiple_records(self, db_path):
        save_execution_record(make_exec_record(code="2330"), db_path)
        save_execution_record(make_exec_record(code="2454"), db_path)
        records = load_execution_records("exec-001", db_path)
        assert len(records) == 2

    def test_load_by_execution_id(self, db_path):
        save_execution_record(make_exec_record(execution_id="exec-001"), db_path)
        save_execution_record(make_exec_record(execution_id="exec-002"), db_path)
        records = load_execution_records("exec-001", db_path)
        assert len(records) == 1

    def test_empty_returns_list(self, db_path):
        assert load_execution_records("exec-999", db_path) == []


# ── DailySummary ──────────────────────────────────────────────────────────────

def make_daily_summary(**kw) -> DailySummaryRow:
    defaults = dict(
        execution_id="exec-001",
        date=date(2026, 4, 27),
        total_pnl=1500.0,
        target_met=True,
        review="今日達標，RSI 策略奏效",
        next_day_plan="明日繼續觀察台積電",
    )
    defaults.update(kw)
    return DailySummaryRow(**defaults)


class TestDailySummary:
    def test_save_and_load(self, db_path):
        save_daily_summary(make_daily_summary(), db_path)
        summaries = load_daily_summaries("exec-001", db_path)
        assert len(summaries) == 1
        assert summaries[0].total_pnl == pytest.approx(1500.0)

    def test_target_met_preserved(self, db_path):
        save_daily_summary(make_daily_summary(target_met=False), db_path)
        summaries = load_daily_summaries("exec-001", db_path)
        assert summaries[0].target_met is False

    def test_multiple_days(self, db_path):
        save_daily_summary(make_daily_summary(date=date(2026, 4, 27)), db_path)
        save_daily_summary(make_daily_summary(date=date(2026, 4, 28)), db_path)
        summaries = load_daily_summaries("exec-001", db_path)
        assert len(summaries) == 2

    def test_ordered_by_date(self, db_path):
        save_daily_summary(make_daily_summary(date=date(2026, 4, 28)), db_path)
        save_daily_summary(make_daily_summary(date=date(2026, 4, 27)), db_path)
        summaries = load_daily_summaries("exec-001", db_path)
        assert summaries[0].date <= summaries[1].date
