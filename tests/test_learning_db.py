"""Tests for learning_db — SQLite-backed learning data store."""
from __future__ import annotations

import os
import tempfile
from datetime import date

import pytest

from learning_db import (
    LearningDB,
    DailyStrategyLog,
    StockPredictionLog,
    ChipSnapshot,
)


@pytest.fixture
def db(tmp_path):
    """每個測試用獨立的臨時資料庫。"""
    path = str(tmp_path / "test_learning.db")
    return LearningDB(path)


# ─────────────────────────────────────────────
# Schema / init
# ─────────────────────────────────────────────

class TestInit:
    def test_tables_created(self, db):
        """初始化後三張表都存在。"""
        import sqlite3
        conn = sqlite3.connect(db.path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "daily_strategy_log" in tables
        assert "stock_prediction_log" in tables
        assert "chip_snapshot" in tables

    def test_idempotent_init(self, tmp_path):
        """重複建立 LearningDB 不會 crash（IF NOT EXISTS）。"""
        path = str(tmp_path / "test.db")
        LearningDB(path)
        LearningDB(path)   # 不應丟例外


# ─────────────────────────────────────────────
# DailyStrategyLog
# ─────────────────────────────────────────────

class TestDailyStrategyLog:
    def test_upsert_and_get(self, db):
        """寫入後可以讀回。"""
        log = DailyStrategyLog(
            date=date(2026, 5, 5),
            plan_type="balanced",
            total_pnl=3500.0,
            pnl_pct=1.2,
            market_index_change=-0.5,
            n_win=2,
            n_loss=1,
        )
        db.upsert_daily_log(log)
        result = db.get_daily_log(date(2026, 5, 5))
        assert result is not None
        assert result.plan_type == "balanced"
        assert result.total_pnl == 3500.0
        assert result.n_win == 2

    def test_upsert_overwrites(self, db):
        """同一天再次 upsert 會覆蓋。"""
        db.upsert_daily_log(DailyStrategyLog(
            date=date(2026, 5, 5), plan_type="aggressive",
            total_pnl=1000.0, pnl_pct=0.5,
            market_index_change=0.3, n_win=1, n_loss=0,
        ))
        db.upsert_daily_log(DailyStrategyLog(
            date=date(2026, 5, 5), plan_type="balanced",
            total_pnl=2000.0, pnl_pct=0.8,
            market_index_change=0.3, n_win=2, n_loss=0,
        ))
        result = db.get_daily_log(date(2026, 5, 5))
        assert result.plan_type == "balanced"
        assert result.total_pnl == 2000.0

    def test_get_nonexistent_returns_none(self, db):
        """查不到的日期回傳 None。"""
        assert db.get_daily_log(date(2000, 1, 1)) is None

    def test_get_logs_range(self, db):
        """可以查某段日期範圍。"""
        for d, pnl in [(date(2026, 5, 1), 100.0), (date(2026, 5, 2), -50.0),
                       (date(2026, 5, 3), 200.0), (date(2026, 5, 4), 80.0)]:
            db.upsert_daily_log(DailyStrategyLog(
                date=d, plan_type="balanced", total_pnl=pnl, pnl_pct=0.0,
                market_index_change=0.0, n_win=1, n_loss=0,
            ))
        logs = db.get_logs_range(date(2026, 5, 2), date(2026, 5, 3))
        assert len(logs) == 2
        assert logs[0].date == date(2026, 5, 2)


# ─────────────────────────────────────────────
# StockPredictionLog
# ─────────────────────────────────────────────

class TestStockPredictionLog:
    def test_insert_and_get_by_date(self, db):
        """寫入個股預測後可以讀回當天所有記錄。"""
        preds = [
            StockPredictionLog(
                date=date(2026, 5, 5), code="2330", name="台積電",
                action="open", confidence=8, expected_return_pct=3.5,
                entry_price=870.0, closing_price=895.0,
                actual_return_pct=2.87, was_correct=True,
            ),
            StockPredictionLog(
                date=date(2026, 5, 5), code="2454", name="聯發科",
                action="open", confidence=6, expected_return_pct=2.0,
                entry_price=1200.0, closing_price=1170.0,
                actual_return_pct=-2.5, was_correct=False,
            ),
        ]
        for p in preds:
            db.insert_prediction(p)

        results = db.get_predictions_by_date(date(2026, 5, 5))
        assert len(results) == 2
        codes = {r.code for r in results}
        assert codes == {"2330", "2454"}

    def test_closing_price_can_be_null(self, db):
        """盤中還沒收盤時，closing_price 可以是 None。"""
        pred = StockPredictionLog(
            date=date(2026, 5, 5), code="2330", name="台積電",
            action="open", confidence=8, expected_return_pct=3.5,
            entry_price=870.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
        )
        db.insert_prediction(pred)
        results = db.get_predictions_by_date(date(2026, 5, 5))
        assert results[0].closing_price is None

    def test_update_closing_prices(self, db):
        """盤後可以批次更新收盤價與實際損益。"""
        db.insert_prediction(StockPredictionLog(
            date=date(2026, 5, 5), code="2330", name="台積電",
            action="open", confidence=8, expected_return_pct=3.5,
            entry_price=870.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
        ))
        db.update_closing_price(
            date=date(2026, 5, 5), code="2330",
            closing_price=895.0, actual_return_pct=2.87, was_correct=True,
        )
        result = db.get_predictions_by_date(date(2026, 5, 5))[0]
        assert result.closing_price == 895.0
        assert result.actual_return_pct == pytest.approx(2.87)
        assert result.was_correct is True

    def test_win_rate_over_days(self, db):
        """計算最近 N 天的整體勝率。"""
        rows = [
            (date(2026, 4, 30), "2330", True),
            (date(2026, 5, 1),  "2454", True),
            (date(2026, 5, 2),  "2330", False),
            (date(2026, 5, 3),  "2454", True),
            (date(2026, 5, 5),  "2330", True),
        ]
        for d, code, correct in rows:
            db.insert_prediction(StockPredictionLog(
                date=d, code=code, name=code,
                action="open", confidence=7, expected_return_pct=2.0,
                entry_price=100.0, closing_price=102.0,
                actual_return_pct=2.0, was_correct=correct,
            ))
        stats = db.win_rate_stats(end_date=date(2026, 5, 5), days=7)
        assert stats["total"] == 5
        assert stats["wins"] == 4
        assert stats["win_rate"] == pytest.approx(0.8)

    def test_confidence_accuracy(self, db):
        """高信心（>=8）的勝率分析。"""
        rows = [
            (date(2026, 5, 1), "A", 9, True),
            (date(2026, 5, 2), "B", 8, True),
            (date(2026, 5, 3), "C", 5, False),
            (date(2026, 5, 4), "D", 9, False),
        ]
        for d, code, conf, correct in rows:
            db.insert_prediction(StockPredictionLog(
                date=d, code=code, name=code,
                action="open", confidence=conf, expected_return_pct=2.0,
                entry_price=100.0, closing_price=102.0,
                actual_return_pct=2.0, was_correct=correct,
            ))
        stats = db.confidence_accuracy(end_date=date(2026, 5, 5), days=7)
        # 高信心（>=8）：A(9,✓) B(8,✓) D(9,✗) → 2/3
        assert stats["high_confidence_total"] == 3
        assert stats["high_confidence_wins"] == 2
        assert stats["high_confidence_win_rate"] == pytest.approx(2 / 3)


# ─────────────────────────────────────────────
# ChipSnapshot
# ─────────────────────────────────────────────

class TestChipSnapshot:
    def test_upsert_and_get(self, db):
        """寫入籌碼快照後可以讀回。"""
        snap = ChipSnapshot(
            date=date(2026, 5, 5), code="2330",
            foreign_net=5000.0, trust_net=200.0,
            margin_change_pct=2.5,
        )
        db.upsert_chip_snapshot(snap)
        result = db.get_chip_snapshot(date(2026, 5, 5), "2330")
        assert result is not None
        assert result.foreign_net == 5000.0
        assert result.margin_change_pct == pytest.approx(2.5)

    def test_upsert_overwrites(self, db):
        """同一天同一股再次 upsert 會覆蓋。"""
        db.upsert_chip_snapshot(ChipSnapshot(
            date=date(2026, 5, 5), code="2330",
            foreign_net=5000.0, trust_net=200.0, margin_change_pct=2.5,
        ))
        db.upsert_chip_snapshot(ChipSnapshot(
            date=date(2026, 5, 5), code="2330",
            foreign_net=6000.0, trust_net=300.0, margin_change_pct=1.0,
        ))
        result = db.get_chip_snapshot(date(2026, 5, 5), "2330")
        assert result.foreign_net == 6000.0

    def test_get_nonexistent_returns_none(self, db):
        assert db.get_chip_snapshot(date(2000, 1, 1), "9999") is None


# ─────────────────────────────────────────────
# 綜合：盤後寫入流程
# ─────────────────────────────────────────────

class TestPostMarketFlow:
    def test_full_post_market_write(self, db):
        """模擬一次完整的盤後寫入流程。"""
        today = date(2026, 5, 5)

        # 1. 盤前：寫入個股預測（無收盤價）
        db.insert_prediction(StockPredictionLog(
            date=today, code="2330", name="台積電",
            action="open", confidence=8, expected_return_pct=3.0,
            entry_price=870.0, closing_price=None,
            actual_return_pct=None, was_correct=None,
        ))
        db.upsert_chip_snapshot(ChipSnapshot(
            date=today, code="2330",
            foreign_net=5000.0, trust_net=200.0, margin_change_pct=1.5,
        ))

        # 2. 盤後：更新收盤價、寫入日策略彙總
        db.update_closing_price(
            date=today, code="2330",
            closing_price=895.0, actual_return_pct=2.87, was_correct=True,
        )
        db.upsert_daily_log(DailyStrategyLog(
            date=today, plan_type="balanced",
            total_pnl=3500.0, pnl_pct=1.2,
            market_index_change=0.8, n_win=1, n_loss=0,
        ))

        # 3. 驗證
        log = db.get_daily_log(today)
        assert log.total_pnl == 3500.0
        preds = db.get_predictions_by_date(today)
        assert preds[0].was_correct is True
        snap = db.get_chip_snapshot(today, "2330")
        assert snap.foreign_net == 5000.0
