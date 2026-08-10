"""
daytrading_db.py — 當沖預測記錄 + 收盤檢討資料庫

兩張表：
  dt_prediction_log ：
    盤前：存 AI 預測欄位（entry_low / target_price / stop_loss …）
    收盤：補 OHLC + outcome + was_correct
  ai_decision_log ：
    LLM 決策全落庫（llm_mode="decider"/"advisor" 皆落庫），供 dt_counterfactual.py
    之類的反事實分析工具重建「LLM 決策 vs 規則決策」的完整歷史。落庫失敗不得
    影響交易流程（log_ai_decision 內部 try/except，絕不對外拋出）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_PATH = "data/daytrading_review.db"


def _hash_prompt(prompt: Optional[str]) -> Optional[str]:
    """sha256 前 16 碼。prompt 為 None/空字串時回傳 None（代表當次沒有實際呼叫 LLM
    或未取得 prompt，例如 advisor 模式省略 9:05 呼叫）。"""
    if not prompt:
        return None
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


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


#: 後來才加進 schema 的欄位。``CREATE TABLE IF NOT EXISTS`` 對既有資料表是
#: no-op，所以光靠建表語句永遠補不上這些欄位——正式環境的
#: data/daytrading_review.db 就因此缺了 was_correct，讓 adaptive_scorer 每天
#: 靜默失敗（no such column）長達數月。新增欄位時一併登記在這裡。
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (資料表, 欄位, 型別)
    ("dt_prediction_log", "was_correct",   "INTEGER"),
    ("dt_prediction_log", "ai_commentary", "TEXT NOT NULL DEFAULT ''"),
    ("dt_prediction_log", "reviewed_at",   "TEXT"),
    ("dt_prediction_log", "entry_low",     "REAL"),
    ("dt_prediction_log", "entry_high",    "REAL"),
)


def _apply_migrations(conn) -> None:
    """替既有資料表補上缺少的欄位。

    只做 ADD COLUMN，既有列的新欄位一律留成 NULL——不臆測歷史資料的值。
    以 PRAGMA 檢查而非 try/except duplicate column，因此可重複執行。
    """
    for table, column, coltype in _MIGRATIONS:
        exists = conn.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
        ).fetchone()
        if not exists:
            continue  # 表還沒建，CREATE TABLE 會帶齊欄位
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


class DaytradingDB:
    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as conn:
            _apply_migrations(conn)
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_decision_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    date          TEXT    NOT NULL,
                    time          TEXT    NOT NULL,
                    code          TEXT    NOT NULL,
                    stage         TEXT    NOT NULL,        -- 'premarket' | 'reconfirm'
                    llm_mode      TEXT    NOT NULL,
                    dt_score      INTEGER NOT NULL DEFAULT 0,
                    prompt_hash   TEXT,
                    raw_response  TEXT,
                    parsed_action TEXT,
                    rule_action   TEXT,
                    final_action  TEXT,
                    features_json TEXT    NOT NULL DEFAULT '{}',
                    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
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

    # ── AI 決策全落庫（反事實分析用）───────────────────────────────────

    def log_ai_decision(
        self,
        *,
        date: str,
        time: str,
        code: str,
        stage: str,                    # 'premarket' | 'reconfirm'
        llm_mode: str,
        dt_score: int,
        prompt: Optional[str],
        raw_response: Optional[str],
        parsed_action: Optional[str],
        rule_action: Optional[str],
        final_action: Optional[str],
        features: Optional[dict] = None,
    ) -> None:
        """落庫一筆 AI 決策記錄。不論 llm_mode 為何都應呼叫（decider 模式的
        rule_action 也順便算出來存，供「規則 vs LLM」反事實比較）。

        失敗（含 DB I/O 錯誤）只記 log.warning，絕不對外拋出——落庫是輔助分析
        用途，不得影響交易主流程。
        """
        try:
            features_json = json.dumps(features or {}, ensure_ascii=False, default=str)
            prompt_hash = _hash_prompt(prompt)
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO ai_decision_log
                        (date, time, code, stage, llm_mode, dt_score, prompt_hash,
                         raw_response, parsed_action, rule_action, final_action, features_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (date, time, code, stage, llm_mode, dt_score, prompt_hash,
                      raw_response, parsed_action, rule_action, final_action, features_json))
        except Exception as e:
            log.warning("log_ai_decision(%s, stage=%s) failed: %s", code, stage, e)

    def get_ai_decisions(self, target_date: str, stage: Optional[str] = None) -> list[sqlite3.Row]:
        """查詢某日的 AI 決策記錄，stage 可篩選 'premarket' / 'reconfirm'。"""
        with self._conn() as conn:
            if stage is not None:
                return conn.execute("""
                    SELECT * FROM ai_decision_log
                    WHERE date = ? AND stage = ? ORDER BY id
                """, (target_date, stage)).fetchall()
            return conn.execute("""
                SELECT * FROM ai_decision_log
                WHERE date = ? ORDER BY id
            """, (target_date,)).fetchall()

    def recent_history(self, days: int = 14) -> list[sqlite3.Row]:
        """近 N 日所有已複盤記錄，供 UI 顯示。"""
        with self._conn() as conn:
            return conn.execute("""
                SELECT * FROM dt_prediction_log
                WHERE outcome IS NOT NULL
                  AND date >= date('now', ?, 'localtime')
                ORDER BY date DESC, dt_score DESC
            """, (f"-{days} days",)).fetchall()
