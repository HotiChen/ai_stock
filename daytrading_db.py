"""
daytrading_db.py — 當沖預測記錄 + 收盤檢討資料庫

一張表 dt_prediction_log：
  盤前：存 AI 預測欄位（entry_low / target_price / stop_loss …）
  收盤：補 OHLC + outcome + was_correct
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_DEFAULT_PATH = "data/daytrading_review.db"


@dataclass
class DTPrediction:
    date:         str
    code:         str
    name:         str
    dt_score:     int
    action:       str            # 'long' | 'skip'
    entry_low:    Optional[float]
    entry_high:   Optional[float]
    target_price: Optional[float]
    stop_loss:    Optional[float]
    ai_summary:   str = ""


@dataclass
class DTReview:
    date:          str
    code:          str
    daily_open:    Optional[float]
    daily_high:    Optional[float]
    daily_low:     Optional[float]
    daily_close:   Optional[float]
    outcome:       str            # 'hit_target' | 'hit_stop' | 'neutral'
    was_correct:   Optional[int]  # 1 | 0 | None
    ai_commentary: str = ""


class DaytradingDB:
    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dt_prediction_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    date          TEXT    NOT NULL,
                    code          TEXT    NOT NULL,
                    name          TEXT    NOT NULL,
                    dt_score      INTEGER NOT NULL DEFAULT 0,
                    action        TEXT    NOT NULL DEFAULT 'skip',
                    entry_low     REAL,
                    entry_high    REAL,
                    target_price  REAL,
                    stop_loss     REAL,
                    ai_summary    TEXT    NOT NULL DEFAULT '',
                    daily_open    REAL,
                    daily_high    REAL,
                    daily_low     REAL,
                    daily_close   REAL,
                    outcome       TEXT,
                    was_correct   INTEGER,
                    ai_commentary TEXT    NOT NULL DEFAULT '',
                    reviewed_at   TEXT,
                    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                    UNIQUE(date, code)
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Phase 1: 存預測（盤前）──────────────────────────────────────

    def save_predictions(self, predictions: list[DTPrediction]) -> int:
        """INSERT OR IGNORE 多筆預測，回傳實際新增筆數。"""
        inserted = 0
        with self._conn() as conn:
            for p in predictions:
                cur = conn.execute("""
                    INSERT OR IGNORE INTO dt_prediction_log
                        (date, code, name, dt_score, action,
                         entry_low, entry_high, target_price, stop_loss, ai_summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (p.date, p.code, p.name, p.dt_score, p.action,
                      p.entry_low, p.entry_high, p.target_price, p.stop_loss,
                      p.ai_summary))
                inserted += cur.rowcount
        return inserted

    # ── Phase 2: 回填收盤結果（收盤後）─────────────────────────────

    def save_review(self, review: DTReview) -> None:
        """UPDATE 今日該股的 OHLC + outcome。"""
        with self._conn() as conn:
            conn.execute("""
                UPDATE dt_prediction_log
                SET daily_open    = ?,
                    daily_high    = ?,
                    daily_low     = ?,
                    daily_close   = ?,
                    outcome       = ?,
                    was_correct   = ?,
                    ai_commentary = ?,
                    reviewed_at   = datetime('now', 'localtime')
                WHERE date = ? AND code = ?
            """, (review.daily_open, review.daily_high,
                  review.daily_low, review.daily_close,
                  review.outcome, review.was_correct,
                  review.ai_commentary,
                  review.date, review.code))

    # ── 查詢 ──────────────────────────────────────────────────────────

    def get_predictions(self, target_date: str) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute("""
                SELECT * FROM dt_prediction_log
                WHERE date = ? ORDER BY dt_score DESC
            """, (target_date,)).fetchall()

    def get_unreviewed(self, target_date: str) -> list[sqlite3.Row]:
        """取今日尚未回填 OHLC 的預測（action='long'）。"""
        with self._conn() as conn:
            return conn.execute("""
                SELECT * FROM dt_prediction_log
                WHERE date = ? AND action = 'long' AND outcome IS NULL
                ORDER BY dt_score DESC
            """, (target_date,)).fetchall()

    def win_rate_summary(self, days: int = 30) -> dict:
        """近 N 日 long 預測勝率統計（只計 outcome 已填入者）。"""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)                                      AS total,
                    SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN was_correct = 0 THEN 1 ELSE 0 END) AS losses
                FROM dt_prediction_log
                WHERE action = 'long'
                  AND outcome IS NOT NULL
                  AND date >= date('now', ?, 'localtime')
            """, (f"-{days} days",)).fetchone()
        total = row["total"] or 0
        wins  = row["wins"]  or 0
        return {
            "total":    total,
            "wins":     wins,
            "losses":   row["losses"] or 0,
            "win_rate": round(wins / total, 3) if total else None,
        }

    def recent_history(self, days: int = 14) -> list[sqlite3.Row]:
        """近 N 日所有已複盤記錄，供 UI 顯示。"""
        with self._conn() as conn:
            return conn.execute("""
                SELECT * FROM dt_prediction_log
                WHERE outcome IS NOT NULL
                  AND date >= date('now', ?, 'localtime')
                ORDER BY date DESC, dt_score DESC
            """, (f"-{days} days",)).fetchall()
