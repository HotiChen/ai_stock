"""tests/test_paper_trade_logging.py — Task 5: run_daily_paper_trade 非法 %-format log."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path


def _make_review_db(path: str, today: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE dt_prediction_log (
            date TEXT, code TEXT, name TEXT, action TEXT, dt_score INTEGER,
            outcome TEXT, daily_open REAL, daily_close REAL,
            target_price REAL, stop_loss REAL, ai_summary TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO dt_prediction_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (today, "2330", "台積電", "long", 90, "hit_target",
         100.0, 105.0, 106.0, 97.0, "測試摘要"),
    )
    conn.commit()
    conn.close()


class TestPaperTradeLoggingFormat:
    def test_success_log_has_no_format_error(self, tmp_path, caplog):
        from dt_paper_trade import run_daily_paper_trade

        review_db = str(tmp_path / "review.db")
        db_path = str(tmp_path / "paper.db")
        today = "2026-07-03"
        _make_review_db(review_db, today)

        with caplog.at_level(logging.INFO):
            trade = run_daily_paper_trade(today=today, db_path=db_path, review_db=review_db)

        assert trade is not None
        assert trade["outcome"] == "hit_target"
        # 逐筆 render log record；非法 %-format 會在 getMessage() 拋出。
        for r in caplog.records:
            r.getMessage()   # must not raise
        # 確認確實走到成功 log（含 capital 字樣）
        assert any("capital" in r.getMessage() for r in caplog.records)
