"""
learning_db.py — SQLite-backed learning data store

三張表：
  daily_strategy_log   每日策略彙總（總損益、大盤漲跌、勝負計數）
  stock_prediction_log 個股 AI 預測 + 盤後實際結果
  chip_snapshot        選股當下的籌碼快照

盤前寫入預測（closing_price=None），盤後更新收盤價與結果。
7/14/28 天報告從這裡計算。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

_DEFAULT_PATH = "data/learning.db"


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class DailyStrategyLog:
    date: date
    plan_type: str                  # aggressive / balanced / conservative
    total_pnl: float                # 當日總損益（元）
    pnl_pct: float                  # 當日損益率（%）
    market_index_change: float      # 加權指數當日漲跌幅（%）
    n_win: int                      # 獲利個股數
    n_loss: int                     # 虧損個股數


@dataclass
class StockPredictionLog:
    date: date
    code: str
    name: str
    action: str                         # open / add / reduce / close / switch / hold
    confidence: int                     # AI 信心分數 1–10
    expected_return_pct: float          # AI 預期報酬率（%）
    entry_price: float                  # 進場價
    closing_price: Optional[float]      # 收盤價（盤後填入）
    actual_return_pct: Optional[float]  # 實際報酬率（盤後計算）
    was_correct: Optional[bool]         # 預測方向是否正確


@dataclass
class ChipSnapshot:
    date: date
    code: str
    foreign_net: float          # 外資買賣超（張）
    trust_net: float            # 投信買賣超（張）
    margin_change_pct: float    # 融資增減%


# ── LearningDB ─────────────────────────────────────────────────────────────────

class LearningDB:
    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── internal ────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        # H4: WAL mode 讓讀寫不互相 block；busy_timeout 避免 "database is locked"
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS daily_strategy_log (
                    date                TEXT PRIMARY KEY,
                    plan_type           TEXT NOT NULL,
                    total_pnl           REAL NOT NULL,
                    pnl_pct             REAL NOT NULL,
                    market_index_change REAL NOT NULL,
                    n_win               INTEGER NOT NULL DEFAULT 0,
                    n_loss              INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS stock_prediction_log (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    date                TEXT NOT NULL,
                    code                TEXT NOT NULL,
                    name                TEXT NOT NULL,
                    action              TEXT NOT NULL,
                    confidence          INTEGER NOT NULL,
                    expected_return_pct REAL NOT NULL,
                    entry_price         REAL NOT NULL,
                    closing_price       REAL,
                    actual_return_pct   REAL,
                    was_correct         INTEGER,
                    UNIQUE(date, code)
                );

                CREATE TABLE IF NOT EXISTS chip_snapshot (
                    date               TEXT NOT NULL,
                    code               TEXT NOT NULL,
                    foreign_net        REAL NOT NULL DEFAULT 0,
                    trust_net          REAL NOT NULL DEFAULT 0,
                    margin_change_pct  REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (date, code)
                );
            """)

    @staticmethod
    def _date_str(d: date) -> str:
        return d.isoformat()

    @staticmethod
    def _parse_date(s: str) -> date:
        return date.fromisoformat(s)

    # ── DailyStrategyLog ────────────────────────────────────────────────────

    def upsert_daily_log(self, log: DailyStrategyLog) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO daily_strategy_log
                    (date, plan_type, total_pnl, pnl_pct, market_index_change, n_win, n_loss)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    plan_type           = excluded.plan_type,
                    total_pnl           = excluded.total_pnl,
                    pnl_pct             = excluded.pnl_pct,
                    market_index_change = excluded.market_index_change,
                    n_win               = excluded.n_win,
                    n_loss              = excluded.n_loss
            """, (
                self._date_str(log.date), log.plan_type, log.total_pnl,
                log.pnl_pct, log.market_index_change, log.n_win, log.n_loss,
            ))

    def get_daily_log(self, d: date) -> Optional[DailyStrategyLog]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_strategy_log WHERE date = ?",
                (self._date_str(d),)
            ).fetchone()
        if row is None:
            return None
        return DailyStrategyLog(
            date=self._parse_date(row["date"]),
            plan_type=row["plan_type"],
            total_pnl=row["total_pnl"],
            pnl_pct=row["pnl_pct"],
            market_index_change=row["market_index_change"],
            n_win=row["n_win"],
            n_loss=row["n_loss"],
        )

    def get_logs_range(self, start: date, end: date) -> list[DailyStrategyLog]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_strategy_log WHERE date BETWEEN ? AND ? ORDER BY date",
                (self._date_str(start), self._date_str(end)),
            ).fetchall()
        return [
            DailyStrategyLog(
                date=self._parse_date(r["date"]),
                plan_type=r["plan_type"],
                total_pnl=r["total_pnl"],
                pnl_pct=r["pnl_pct"],
                market_index_change=r["market_index_change"],
                n_win=r["n_win"],
                n_loss=r["n_loss"],
            )
            for r in rows
        ]

    # ── StockPredictionLog ──────────────────────────────────────────────────

    def insert_prediction(self, pred: StockPredictionLog) -> None:
        """盤前寫入預測（closing_price 可為 None）。重複插入同一天同一股會忽略。"""
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO stock_prediction_log
                    (date, code, name, action, confidence, expected_return_pct,
                     entry_price, closing_price, actual_return_pct, was_correct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self._date_str(pred.date), pred.code, pred.name,
                pred.action, pred.confidence, pred.expected_return_pct,
                pred.entry_price, pred.closing_price,
                pred.actual_return_pct,
                None if pred.was_correct is None else int(pred.was_correct),
            ))

    def update_closing_price(
        self,
        date: date,
        code: str,
        closing_price: float,
        actual_return_pct: float,
        was_correct: bool,
    ) -> None:
        """盤後更新收盤價與結果。"""
        with self._conn() as conn:
            conn.execute("""
                UPDATE stock_prediction_log
                SET closing_price     = ?,
                    actual_return_pct = ?,
                    was_correct       = ?
                WHERE date = ? AND code = ?
            """, (
                closing_price, actual_return_pct, int(was_correct),
                self._date_str(date), code,
            ))

    def get_predictions_by_date(self, d: date) -> list[StockPredictionLog]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM stock_prediction_log WHERE date = ? ORDER BY code",
                (self._date_str(d),),
            ).fetchall()
        return [self._row_to_pred(r) for r in rows]

    def get_predictions_by_date_range(
        self,
        start: date,
        end: date,
        only_completed: bool = False,
    ) -> list[StockPredictionLog]:
        """H6: 公開 API — 取得指定日期區間的所有個股預測紀錄。

        Args:
            start: 起始日期（含）。
            end:   結束日期（含）。
            only_completed: True 時只回傳已有 was_correct 的紀錄（盤後結果已更新）。
        """
        sql = "SELECT * FROM stock_prediction_log WHERE date BETWEEN ? AND ?"
        params: list = [self._date_str(start), self._date_str(end)]
        if only_completed:
            sql += " AND was_correct IS NOT NULL"
        sql += " ORDER BY date, code"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_pred(r) for r in rows]

    def _row_to_pred(self, row) -> StockPredictionLog:
        wc = row["was_correct"]
        return StockPredictionLog(
            date=self._parse_date(row["date"]),
            code=row["code"],
            name=row["name"],
            action=row["action"],
            confidence=row["confidence"],
            expected_return_pct=row["expected_return_pct"],
            entry_price=row["entry_price"],
            closing_price=row["closing_price"],
            actual_return_pct=row["actual_return_pct"],
            was_correct=None if wc is None else bool(wc),
        )

    # ── Analytics ────────────────────────────────────────────────────────────

    def win_rate_stats(self, end_date: date, days: int = 7) -> dict:
        """計算最近 N 天（含今天）的整體勝率（只計已有結果的紀錄）。"""
        from datetime import timedelta
        start = end_date - timedelta(days=days - 1)
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT was_correct FROM stock_prediction_log
                WHERE date BETWEEN ? AND ?
                  AND was_correct IS NOT NULL
            """, (self._date_str(start), self._date_str(end_date))).fetchall()

        total = len(rows)
        wins  = sum(1 for r in rows if r["was_correct"])
        return {
            "total":    total,
            "wins":     wins,
            "win_rate": wins / total if total > 0 else 0.0,
            "days":     days,
        }

    def confidence_accuracy(self, end_date: date, days: int = 7) -> dict:
        """信心分數 >=8 的高信心預測，勝率如何？"""
        from datetime import timedelta
        start = end_date - timedelta(days=days - 1)
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT confidence, was_correct FROM stock_prediction_log
                WHERE date BETWEEN ? AND ?
                  AND was_correct IS NOT NULL
            """, (self._date_str(start), self._date_str(end_date))).fetchall()

        all_rows  = list(rows)
        high      = [r for r in all_rows if r["confidence"] >= 8]
        high_wins = sum(1 for r in high if r["was_correct"])

        return {
            "high_confidence_total":    len(high),
            "high_confidence_wins":     high_wins,
            "high_confidence_win_rate": high_wins / len(high) if high else 0.0,
        }

    def sector_stats(self, end_date: date, days: int = 14) -> list[dict]:
        """各 action 類型的勝率排行（為 sector 分析預留，目前用 action 分組）。"""
        from datetime import timedelta
        start = end_date - timedelta(days=days - 1)
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT action,
                       COUNT(*) AS total,
                       SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) AS wins
                FROM stock_prediction_log
                WHERE date BETWEEN ? AND ?
                  AND was_correct IS NOT NULL
                GROUP BY action
                ORDER BY wins * 1.0 / COUNT(*) DESC
            """, (self._date_str(start), self._date_str(end_date))).fetchall()
        return [
            {
                "action":   r["action"],
                "total":    r["total"],
                "wins":     r["wins"],
                "win_rate": r["wins"] / r["total"] if r["total"] > 0 else 0.0,
            }
            for r in rows
        ]

    def summary_report(self, end_date: date, days: int = 7) -> dict:
        """一次取得所有學習指標，供 Telegram 或 Streamlit 顯示。"""
        logs     = self.get_logs_range(
            end_date - __import__("datetime").timedelta(days=days - 1),
            end_date,
        )
        win_stat = self.win_rate_stats(end_date, days)
        conf_acc = self.confidence_accuracy(end_date, days)

        total_pnl = sum(l.total_pnl for l in logs)
        n_days    = len(logs)
        win_days  = sum(1 for l in logs if l.total_pnl > 0)

        return {
            "days":                     days,
            "n_trading_days":           n_days,
            "total_pnl":                total_pnl,
            "win_days":                 win_days,
            "stock_win_rate":           win_stat["win_rate"],
            "stock_total":              win_stat["total"],
            "high_conf_win_rate":       conf_acc["high_confidence_win_rate"],
            "high_conf_total":          conf_acc["high_confidence_total"],
        }

    # ── ChipSnapshot ────────────────────────────────────────────────────────

    def upsert_chip_snapshot(self, snap: ChipSnapshot) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO chip_snapshot
                    (date, code, foreign_net, trust_net, margin_change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date, code) DO UPDATE SET
                    foreign_net       = excluded.foreign_net,
                    trust_net         = excluded.trust_net,
                    margin_change_pct = excluded.margin_change_pct
            """, (
                self._date_str(snap.date), snap.code,
                snap.foreign_net, snap.trust_net, snap.margin_change_pct,
            ))

    def get_chip_snapshot(self, d: date, code: str) -> Optional[ChipSnapshot]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM chip_snapshot WHERE date = ? AND code = ?",
                (self._date_str(d), code),
            ).fetchone()
        if row is None:
            return None
        return ChipSnapshot(
            date=self._parse_date(row["date"]),
            code=row["code"],
            foreign_net=row["foreign_net"],
            trust_net=row["trust_net"],
            margin_change_pct=row["margin_change_pct"],
        )
