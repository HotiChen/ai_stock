"""Tests for weekly_report — AI weekly strategy review."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from learning_db import (
    LearningDB, DailyStrategyLog, StockPredictionLog, ChipSnapshot
)
from weekly_report import (
    collect_weekly_data,
    format_data_for_ai,
    parse_ai_report,
    WeeklyReportData,
)


@pytest.fixture
def db(tmp_path):
    return LearningDB(str(tmp_path / "test.db"))


@pytest.fixture
def db_with_data(db):
    """插入一週完整資料：5 個交易日，共 10 筆個股預測。"""
    days_data = [
        # (date, plan, pnl, pnl_pct, idx_chg, n_win, n_loss)
        (date(2026, 5, 4), "balanced",     3500.0,  1.2,  0.8,  2, 1),
        (date(2026, 5, 5), "balanced",    -1200.0, -0.4, -0.3,  1, 2),
        (date(2026, 5, 6), "aggressive",   5000.0,  1.8,  1.2,  3, 0),
        (date(2026, 5, 7), "balanced",     800.0,   0.3,  0.1,  1, 1),
        (date(2026, 5, 8), "conservative", 2000.0,  0.7,  0.5,  2, 0),
    ]
    for d, pt, pnl, pnl_pct, idx, nw, nl in days_data:
        db.upsert_daily_log(DailyStrategyLog(
            date=d, plan_type=pt, total_pnl=pnl, pnl_pct=pnl_pct,
            market_index_change=idx, n_win=nw, n_loss=nl,
        ))

    preds = [
        (date(2026, 5, 4), "2330", "台積電",   "open", 9, 3.0, 870.0, 895.0,  2.87, True),
        (date(2026, 5, 4), "2454", "聯發科",   "open", 6, 2.0, 1200.0,1170.0,-2.50, False),
        (date(2026, 5, 4), "6505", "台塑化",   "open", 7, 1.5, 100.0, 103.0,  3.00, True),
        (date(2026, 5, 5), "2330", "台積電",   "add",  8, 2.5, 895.0, 880.0, -1.68, False),
        (date(2026, 5, 5), "2317", "鴻海",     "open", 5, 1.0, 150.0, 147.0, -2.00, False),
        (date(2026, 5, 5), "2382", "廣達",     "open", 7, 2.0, 250.0, 258.0,  3.20, True),
        (date(2026, 5, 6), "2330", "台積電",   "open", 9, 4.0, 880.0, 920.0,  4.55, True),
        (date(2026, 5, 6), "3008", "大立光",   "open", 8, 3.0, 2400.0,2480.0, 3.33, True),
        (date(2026, 5, 6), "2603", "長榮",     "open", 6, 2.0, 180.0, 185.0,  2.78, True),
        (date(2026, 5, 7), "2454", "聯發科",   "open", 7, 2.0, 1170.0,1190.0, 1.71, True),
        (date(2026, 5, 8), "2330", "台積電",   "hold", 8, 0.0, 920.0, 935.0,  1.63, True),
        (date(2026, 5, 8), "2412", "中華電",   "open", 5, 1.0, 130.0, 128.0, -1.54, False),
    ]
    for d, code, name, action, conf, exp, entry, close, ret, correct in preds:
        db.insert_prediction(StockPredictionLog(
            date=d, code=code, name=name, action=action,
            confidence=conf, expected_return_pct=exp,
            entry_price=entry, closing_price=close,
            actual_return_pct=ret, was_correct=correct,
        ))
    return db


# ─────────────────────────────────────────────
# collect_weekly_data
# ─────────────────────────────────────────────

class TestCollectWeeklyData:
    def test_returns_weekly_report_data(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        assert isinstance(data, WeeklyReportData)

    def test_date_range_is_7_days(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        assert data.start_date == date(2026, 5, 2)
        assert data.end_date == date(2026, 5, 8)

    def test_trading_days_count(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        assert data.n_trading_days == 5

    def test_total_pnl_aggregated(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        # 3500 - 1200 + 5000 + 800 + 2000 = 10100
        assert data.total_pnl == pytest.approx(10100.0)

    def test_win_rate_calculated(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        # 12 筆：8 wins, 4 loss → 8/12 ≈ 0.667
        assert data.stock_win_rate == pytest.approx(8 / 12)

    def test_best_pick_identified(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        # 最高 actual_return_pct：2330@05/06 +4.55%
        assert data.best_pick["code"] == "2330"
        assert data.best_pick["actual_return_pct"] == pytest.approx(4.55)

    def test_worst_pick_identified(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        # 最低 actual_return_pct：2454@05/04 -2.50%
        assert data.worst_pick["code"] == "2454"
        assert data.worst_pick["actual_return_pct"] == pytest.approx(-2.50)

    def test_high_confidence_win_rate(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        # conf>=8: 2330/9(T), 2330/8(F), 2330/9(T), 3008/8(T), 2330/8(T) → 4/5 = 0.8
        assert data.high_conf_win_rate == pytest.approx(4 / 5)

    def test_empty_db_returns_zero_stats(self, db):
        data = collect_weekly_data(db, end_date=date(2026, 5, 8))
        assert data.n_trading_days == 0
        assert data.total_pnl == 0.0
        assert data.stock_win_rate == 0.0


# ─────────────────────────────────────────────
# format_data_for_ai
# ─────────────────────────────────────────────

class TestFormatDataForAi:
    def test_returns_string(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        prompt = format_data_for_ai(data)
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_contains_key_metrics(self, db_with_data):
        data = collect_weekly_data(db_with_data, end_date=date(2026, 5, 8))
        prompt = format_data_for_ai(data)
        assert "勝率" in prompt
        assert "2330" in prompt   # best pick 出現
        assert "損益" in prompt


# ─────────────────────────────────────────────
# parse_ai_report
# ─────────────────────────────────────────────

class TestParseAiReport:
    def test_returns_dict_with_required_keys(self):
        raw = """{
            "summary": "本週策略整體表現良好",
            "wins": ["台積電連續兩次進場均獲利"],
            "losses": ["聯發科判斷失準"],
            "patterns": ["高信心股票勝率較高"],
            "next_week": ["持續關注半導體題材"]
        }"""
        result = parse_ai_report(raw)
        assert "summary" in result
        assert "wins" in result
        assert "losses" in result
        assert "patterns" in result
        assert "next_week" in result

    def test_handles_json_in_markdown(self):
        raw = "```json\n{\"summary\": \"OK\", \"wins\": [], \"losses\": [], \"patterns\": [], \"next_week\": []}\n```"
        result = parse_ai_report(raw)
        assert result["summary"] == "OK"

    def test_returns_fallback_on_invalid_json(self):
        result = parse_ai_report("這不是 JSON")
        assert "summary" in result
        assert result["summary"] != ""   # fallback 不為空
