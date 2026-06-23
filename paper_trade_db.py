"""
paper_trade_db.py — 紙上模擬交易資料庫（與真實交易完全隔離）

一張表 paper_trades，每筆 = 一次完整的紙上進出場（含 entry/exit/pnl/出場原因）。
支援跨日彙總查詢：當日損益、近 N 日勝率與累計損益、近 N 日歷史。

刻意獨立成 data/paper_trades.db，不寫 research.db（真實成交）也不寫
daytrading_review.db（AI 預測準確率），因此紙上模擬的損益與勝率不會污染
任何真實統計。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_PATH = "data/paper_trades.db"


@dataclass
class PaperTrade:
    date:        str          # 交易日 YYYY-MM-DD
    code:        str
    name:        str
    entry_price: float
    exit_price:  float
    quantity:    int
    lot_type:    str          # 'common' | 'intraday_odd'
    pnl:         float        # 紙上損益（元）
    reason:      str          # stop_loss | take_profit_ceiling | trailing_stop | force_close
    entry_time:  str = ""     # ISO 8601
    exit_time:   str = ""     # ISO 8601


class PaperTradeDB:
    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    date        TEXT    NOT NULL,
                    code        TEXT    NOT NULL,
                    name        TEXT    NOT NULL,
                    entry_price REAL    NOT NULL,
                    exit_price  REAL    NOT NULL,
                    quantity    INTEGER NOT NULL DEFAULT 1,
                    lot_type    TEXT    NOT NULL DEFAULT 'common',
                    pnl         REAL    NOT NULL DEFAULT 0,
                    reason      TEXT    NOT NULL DEFAULT '',
                    entry_time  TEXT    NOT NULL DEFAULT '',
                    exit_time   TEXT    NOT NULL DEFAULT '',
                    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_trades_date "
                "ON paper_trades(date)"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Write ─────────────────────────────────────────────────────────────────

    def save_trade(self, t: PaperTrade) -> int:
        """INSERT 一筆紙上交易，回傳新增筆數（1）。"""
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO paper_trades
                    (date, code, name, entry_price, exit_price, quantity,
                     lot_type, pnl, reason, entry_time, exit_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (t.date, t.code, t.name, t.entry_price, t.exit_price,
                  t.quantity, t.lot_type, t.pnl, t.reason,
                  t.entry_time, t.exit_time))
            return cur.rowcount

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_trades(self, target_date: str) -> list[dict]:
        """回傳某日所有紙上交易（dict list，依 id 排序）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE date = ? ORDER BY id",
                (target_date,),
            ).fetchall()
        return [dict(r) for r in rows]

    def day_summary(self, target_date: str) -> dict:
        """某日彙總：total_pnl / wins / n / win_rate。"""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)                                   AS n,
                    COALESCE(SUM(pnl), 0)                      AS total_pnl,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)   AS wins
                FROM paper_trades WHERE date = ?
            """, (target_date,)).fetchone()
        n     = row["n"] or 0
        wins  = row["wins"] or 0
        return {
            "n":         n,
            "total_pnl": float(row["total_pnl"] or 0.0),
            "wins":      wins,
            "win_rate":  round(wins / n, 3) if n else None,
        }

    def win_rate_summary(self, days: int = 30) -> dict:
        """近 N 日彙總：total / wins / losses / win_rate / total_pnl。"""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*)                                  AS total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)  AS wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) AS losses,
                    COALESCE(SUM(pnl), 0)                     AS total_pnl
                FROM paper_trades
                WHERE date >= date('now', ?, 'localtime')
            """, (f"-{days} days",)).fetchone()
        total = row["total"] or 0
        wins  = row["wins"]  or 0
        return {
            "total":     total,
            "wins":      wins,
            "losses":    row["losses"] or 0,
            "win_rate":  round(wins / total, 3) if total else None,
            "total_pnl": float(row["total_pnl"] or 0.0),
        }

    def recent_history(self, days: int = 14) -> list[dict]:
        """近 N 日所有紙上交易，最新在前。"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM paper_trades
                WHERE date >= date('now', ?, 'localtime')
                ORDER BY date DESC, id DESC
            """, (f"-{days} days",)).fetchall()
        return [dict(r) for r in rows]
