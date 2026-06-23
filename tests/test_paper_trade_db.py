"""tests/test_paper_trade_db.py — TDD tests for paper_trade_db.py

紙上模擬交易資料庫：把每筆紙上進出場（含 pnl、出場原因）存進獨立 SQLite，
支援跨日彙總查詢（總損益、勝率、近 N 日歷史）。與真實交易 research.db 隔離。
"""
from __future__ import annotations

import pytest


def _trade(db_mod, date="2026-06-23", code="2337", name="旺宏",
           entry_price=100.0, exit_price=105.0, quantity=1,
           lot_type="common", pnl=5000.0, reason="take_profit_ceiling"):
    return db_mod.PaperTrade(
        date=date, code=code, name=name,
        entry_price=entry_price, exit_price=exit_price,
        quantity=quantity, lot_type=lot_type,
        pnl=pnl, reason=reason,
        entry_time="2026-06-23T09:10:00", exit_time="2026-06-23T11:30:00",
    )


@pytest.fixture
def db(tmp_path):
    import paper_trade_db
    return paper_trade_db.PaperTradeDB(path=str(tmp_path / "paper.db")), paper_trade_db


class TestSaveAndGet:
    def test_save_returns_rowcount(self, db):
        pdb, mod = db
        n = pdb.save_trade(_trade(mod))
        assert n == 1

    def test_get_trades_for_day(self, db):
        pdb, mod = db
        pdb.save_trade(_trade(mod, code="2337"))
        pdb.save_trade(_trade(mod, code="2449", pnl=-2000.0, reason="stop_loss"))
        rows = pdb.get_trades("2026-06-23")
        assert len(rows) == 2
        assert {r["code"] for r in rows} == {"2337", "2449"}

    def test_get_trades_filters_by_date(self, db):
        pdb, mod = db
        pdb.save_trade(_trade(mod, date="2026-06-22"))
        pdb.save_trade(_trade(mod, date="2026-06-23"))
        assert len(pdb.get_trades("2026-06-23")) == 1

    def test_fields_roundtrip(self, db):
        pdb, mod = db
        pdb.save_trade(_trade(mod, entry_price=100.0, exit_price=97.0,
                              pnl=-3000.0, reason="stop_loss"))
        r = pdb.get_trades("2026-06-23")[0]
        assert r["entry_price"] == 100.0
        assert r["exit_price"] == 97.0
        assert r["pnl"] == -3000.0
        assert r["reason"] == "stop_loss"


class TestDaySummary:
    def test_day_summary_totals(self, db):
        pdb, mod = db
        pdb.save_trade(_trade(mod, code="2337", pnl=5000.0))
        pdb.save_trade(_trade(mod, code="2449", pnl=-2000.0, reason="stop_loss"))
        s = pdb.day_summary("2026-06-23")
        assert s["total_pnl"] == 3000.0
        assert s["wins"] == 1
        assert s["n"] == 2

    def test_day_summary_empty(self, db):
        pdb, mod = db
        s = pdb.day_summary("2026-06-23")
        assert s["n"] == 0
        assert s["total_pnl"] == 0.0


class TestCrossDayStats:
    def test_win_rate_summary_aggregates_multiple_days(self, db):
        pdb, mod = db
        # 3 勝 1 敗
        pdb.save_trade(_trade(mod, date="2026-06-20", code="A", pnl=1000.0))
        pdb.save_trade(_trade(mod, date="2026-06-21", code="B", pnl=2000.0))
        pdb.save_trade(_trade(mod, date="2026-06-22", code="C", pnl=-500.0, reason="stop_loss"))
        pdb.save_trade(_trade(mod, date="2026-06-23", code="D", pnl=800.0))
        stats = pdb.win_rate_summary(days=3650)  # 夠長，全納入
        assert stats["total"] == 4
        assert stats["wins"] == 3
        assert stats["total_pnl"] == 3300.0
        assert stats["win_rate"] == 0.75

    def test_win_rate_summary_empty(self, db):
        pdb, mod = db
        stats = pdb.win_rate_summary(days=30)
        assert stats["total"] == 0
        assert stats["win_rate"] is None

    def test_recent_history_orders_newest_first(self, db):
        pdb, mod = db
        pdb.save_trade(_trade(mod, date="2026-06-20", code="A"))
        pdb.save_trade(_trade(mod, date="2026-06-23", code="B"))
        hist = pdb.recent_history(days=3650)
        assert hist[0]["date"] == "2026-06-23"


class TestIsolation:
    def test_separate_db_file(self, db):
        pdb, mod = db
        # 預設路徑必須與 research.db / daytrading_review.db 不同
        assert mod._DEFAULT_PATH != "data/research.db"
        assert mod._DEFAULT_PATH != "data/daytrading_review.db"
        assert "paper" in mod._DEFAULT_PATH
