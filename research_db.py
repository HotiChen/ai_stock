from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_DDL = """
CREATE TABLE IF NOT EXISTS sim_positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL,
    code          TEXT NOT NULL,
    name          TEXT,
    quantity      INTEGER,
    avg_cost      REAL,
    current_price REAL,
    opened_at     TEXT,
    status        TEXT DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS sim_realized (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL,
    code          TEXT NOT NULL,
    name          TEXT,
    quantity      INTEGER,
    avg_cost      REAL,
    sell_price    REAL,
    realized_pnl  REAL,
    closed_at     TEXT
);

CREATE TABLE IF NOT EXISTS market_context (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at  TEXT NOT NULL,
    sp500_change REAL,
    nasdaq_change REAL,
    sox_change   REAL,
    vix          REAL,
    sentiment    TEXT,
    news_json    TEXT
);

CREATE TABLE IF NOT EXISTS stock_analysis (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    analyzed_at  TEXT NOT NULL,
    code         TEXT NOT NULL,
    name         TEXT,
    signal       TEXT,
    confidence   INTEGER,
    summary      TEXT,
    factors_json TEXT
);

CREATE TABLE IF NOT EXISTS plan_executions (
    execution_id TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    plan_type    TEXT NOT NULL,
    plan_json    TEXT NOT NULL,
    status       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    code          TEXT,
    name          TEXT,
    action        TEXT,
    quantity      INTEGER,
    price         REAL,
    simulated_pnl REAL,
    reason        TEXT
);

CREATE TABLE IF NOT EXISTS daily_summaries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL,
    date          TEXT NOT NULL,
    total_pnl     REAL,
    target_met    INTEGER,
    review        TEXT,
    next_day_plan TEXT
);

CREATE TABLE IF NOT EXISTS daily_plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date  TEXT NOT NULL UNIQUE,
    picks_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT,
    action      TEXT NOT NULL,
    quantity    INTEGER,
    price       REAL,
    amount      REAL,
    pnl         REAL,
    lot_type    TEXT DEFAULT 'common',
    sector      TEXT DEFAULT '未知',
    note        TEXT,
    executed_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT,
    name        TEXT,
    alert_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'normal',
    created_at  TEXT NOT NULL,
    sent        INTEGER NOT NULL DEFAULT 0
);
"""


# ── Row types ─────────────────────────────────────────────────────────────────

@dataclass
class MarketContextRow:
    recorded_at:    datetime
    sp500_change:   float
    nasdaq_change:  float
    sox_change:     float
    vix:            float
    sentiment:      str
    news_headlines: list[str]


@dataclass
class StockAnalysisRow:
    analyzed_at: datetime
    code:        str
    name:        str
    signal:      str
    confidence:  int
    summary:     str
    factors:     dict


@dataclass
class PlanExecutionRow:
    execution_id: str
    created_at:   datetime
    plan_type:    str
    plan_json:    dict
    status:       str


@dataclass
class ExecutionRecordRow:
    execution_id:  str
    recorded_at:   datetime
    code:          str
    name:          str
    action:        str
    quantity:      int
    price:         float
    simulated_pnl: float
    reason:        str


@dataclass
class DailySummaryRow:
    execution_id:  str
    date:          date
    total_pnl:     float
    target_met:    bool
    review:        str
    next_day_plan: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _conn(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def _upgrade_daily_trades(con: sqlite3.Connection) -> None:
    """Idempotent migration: add lot_type / sector / executed_at columns to daily_trades if absent.

    Needed for databases created before this schema version.
    SQLite does not support ADD COLUMN IF NOT EXISTS, so we check PRAGMA first.
    """
    existing = {row[1] for row in con.execute("PRAGMA table_info(daily_trades)").fetchall()}
    if "lot_type" not in existing:
        con.execute("ALTER TABLE daily_trades ADD COLUMN lot_type TEXT DEFAULT 'common'")
    if "sector" not in existing:
        con.execute("ALTER TABLE daily_trades ADD COLUMN sector TEXT DEFAULT '未知'")
    if "executed_at" not in existing:
        con.execute("ALTER TABLE daily_trades ADD COLUMN executed_at TEXT")


def init_db(path: str) -> None:
    with _conn(path) as con:
        con.executescript(_DDL)
        _upgrade_daily_trades(con)


# ── MarketContext ─────────────────────────────────────────────────────────────

def save_market_context(ctx: MarketContextRow, path: str) -> None:
    with _conn(path) as con:
        con.execute(
            "INSERT INTO market_context (recorded_at,sp500_change,nasdaq_change,sox_change,vix,sentiment,news_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (ctx.recorded_at.isoformat(), ctx.sp500_change, ctx.nasdaq_change,
             ctx.sox_change, ctx.vix, ctx.sentiment, json.dumps(ctx.news_headlines, ensure_ascii=False)),
        )


def load_latest_market_context(path: str) -> Optional[MarketContextRow]:
    with _conn(path) as con:
        row = con.execute(
            "SELECT recorded_at,sp500_change,nasdaq_change,sox_change,vix,sentiment,news_json "
            "FROM market_context ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return MarketContextRow(
        recorded_at=datetime.fromisoformat(row[0]),
        sp500_change=row[1], nasdaq_change=row[2],
        sox_change=row[3], vix=row[4], sentiment=row[5],
        news_headlines=json.loads(row[6]),
    )


# ── StockAnalysis ─────────────────────────────────────────────────────────────

def save_stock_analysis(row: StockAnalysisRow, path: str) -> None:
    with _conn(path) as con:
        con.execute(
            "INSERT INTO stock_analysis (analyzed_at,code,name,signal,confidence,summary,factors_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (row.analyzed_at.isoformat(), row.code, row.name, row.signal,
             row.confidence, row.summary, json.dumps(row.factors, ensure_ascii=False)),
        )


def load_stock_analysis(code: str, path: str) -> Optional[StockAnalysisRow]:
    with _conn(path) as con:
        r = con.execute(
            "SELECT analyzed_at,code,name,signal,confidence,summary,factors_json "
            "FROM stock_analysis WHERE code=? ORDER BY analyzed_at DESC LIMIT 1",
            (code,),
        ).fetchone()
    if not r:
        return None
    return StockAnalysisRow(
        analyzed_at=datetime.fromisoformat(r[0]),
        code=r[1], name=r[2], signal=r[3],
        confidence=r[4], summary=r[5],
        factors=json.loads(r[6]),
    )


# ── PlanExecution ─────────────────────────────────────────────────────────────

def save_plan_execution(row: PlanExecutionRow, path: str) -> None:
    with _conn(path) as con:
        con.execute(
            "INSERT OR REPLACE INTO plan_executions (execution_id,created_at,plan_type,plan_json,status) "
            "VALUES (?,?,?,?,?)",
            (row.execution_id, row.created_at.isoformat(), row.plan_type,
             json.dumps(row.plan_json, ensure_ascii=False), row.status),
        )


def load_active_execution(path: str) -> Optional[PlanExecutionRow]:
    with _conn(path) as con:
        r = con.execute(
            "SELECT execution_id,created_at,plan_type,plan_json,status "
            "FROM plan_executions WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not r:
        return None
    return PlanExecutionRow(
        execution_id=r[0], created_at=datetime.fromisoformat(r[1]),
        plan_type=r[2], plan_json=json.loads(r[3]), status=r[4],
    )


def stop_execution(execution_id: str, path: str) -> None:
    with _conn(path) as con:
        con.execute(
            "UPDATE plan_executions SET status='stopped' WHERE execution_id=?",
            (execution_id,),
        )


# ── ExecutionRecord ───────────────────────────────────────────────────────────

def save_execution_record(row: ExecutionRecordRow, path: str) -> None:
    with _conn(path) as con:
        con.execute(
            "INSERT INTO execution_records "
            "(execution_id,recorded_at,code,name,action,quantity,price,simulated_pnl,reason) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (row.execution_id, row.recorded_at.isoformat(), row.code, row.name,
             row.action, row.quantity, row.price, row.simulated_pnl, row.reason),
        )


def load_execution_records(execution_id: str, path: str) -> list[ExecutionRecordRow]:
    with _conn(path) as con:
        rows = con.execute(
            "SELECT execution_id,recorded_at,code,name,action,quantity,price,simulated_pnl,reason "
            "FROM execution_records WHERE execution_id=? ORDER BY recorded_at",
            (execution_id,),
        ).fetchall()
    return [
        ExecutionRecordRow(
            execution_id=r[0], recorded_at=datetime.fromisoformat(r[1]),
            code=r[2], name=r[3], action=r[4], quantity=r[5],
            price=r[6], simulated_pnl=r[7], reason=r[8],
        )
        for r in rows
    ]


# ── DailySummary ──────────────────────────────────────────────────────────────

def save_daily_summary(row: DailySummaryRow, path: str) -> None:
    with _conn(path) as con:
        con.execute(
            "INSERT INTO daily_summaries (execution_id,date,total_pnl,target_met,review,next_day_plan) "
            "VALUES (?,?,?,?,?,?)",
            (row.execution_id, row.date.isoformat(), row.total_pnl,
             1 if row.target_met else 0, row.review, row.next_day_plan),
        )


def load_daily_summaries(execution_id: str, path: str) -> list[DailySummaryRow]:
    with _conn(path) as con:
        rows = con.execute(
            "SELECT execution_id,date,total_pnl,target_met,review,next_day_plan "
            "FROM daily_summaries WHERE execution_id=? ORDER BY date",
            (execution_id,),
        ).fetchall()
    return [
        DailySummaryRow(
            execution_id=r[0], date=date.fromisoformat(r[1]),
            total_pnl=r[2], target_met=bool(r[3]),
            review=r[4], next_day_plan=r[5],
        )
        for r in rows
    ]


# ── DailyPlan ─────────────────────────────────────────────────────────────────

def save_daily_plan(plan_date: date, picks: list[dict], path: str) -> None:
    """Upsert today's approved picks list (replaces if same date exists)."""
    with _conn(path) as con:
        con.execute(
            "INSERT OR REPLACE INTO daily_plans (plan_date, picks_json) VALUES (?, ?)",
            (plan_date.isoformat(), json.dumps(picks, ensure_ascii=False)),
        )


def load_daily_plan(plan_date: date, path: str) -> list[dict]:
    """Return approved picks for the given date, or [] if none saved."""
    with _conn(path) as con:
        row = con.execute(
            "SELECT picks_json FROM daily_plans WHERE plan_date=?",
            (plan_date.isoformat(),),
        ).fetchone()
    if not row:
        return []
    return json.loads(row[0])


# ── DailyTrade ────────────────────────────────────────────────────────────────

def reject_pick_from_plan(plan_date: date, code: str, path: str) -> None:
    """Remove a single stock code from today's daily plan (user rejected it)."""
    picks = load_daily_plan(plan_date, path)
    updated = [p for p in picks if p.get("code") != code]
    if updated != picks:  # something changed — write back
        save_daily_plan(plan_date, updated, path)


def reject_all_picks_from_plan(plan_date: date, path: str) -> None:
    """Clear all picks for the given date (user rejected all).
    No-op if no plan exists for that date."""
    with _conn(path) as con:
        con.execute(
            "UPDATE daily_plans SET picks_json=? WHERE plan_date=?",
            ("[]", plan_date.isoformat()),
        )


def save_daily_trade(trade: dict, path: str) -> None:
    """Append a trade record.

    Required keys: trade_date, code, action.
    Optional keys: name, quantity, price, amount, pnl (nullable),
                   lot_type (default 'common'), sector (default '未知'), note,
                   executed_at (default: datetime.now() ISO 8601 to seconds).

    ``lot_type`` and ``sector`` are stored as proper columns so that
    load_current_positions() can build a risk_guard-compatible position record
    without parsing free-text strings.
    ``executed_at`` records the wall-clock time of execution for audit purposes.
    """
    td = trade.get("trade_date")
    td_str = td.isoformat() if isinstance(td, date) else str(td)
    executed_at = trade.get("executed_at")
    if executed_at is None:
        executed_at = datetime.now().isoformat(timespec="seconds")
    with _conn(path) as con:
        con.execute(
            "INSERT INTO daily_trades "
            "(trade_date, code, name, action, quantity, price, amount, pnl, "
            " lot_type, sector, note, executed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                td_str,
                trade.get("code"),
                trade.get("name"),
                trade.get("action"),
                trade.get("quantity"),
                trade.get("price"),
                trade.get("amount"),
                trade.get("pnl"),
                trade.get("lot_type", "common"),
                trade.get("sector", "未知"),
                trade.get("note"),
                executed_at,
            ),
        )


def load_daily_trades(trade_date: date, path: str) -> list[dict]:
    """Return all trades for the given date.

    Each dict contains: code, name, action, quantity, price, amount, pnl,
                        lot_type, sector, note, executed_at.
    """
    with _conn(path) as con:
        rows = con.execute(
            "SELECT code, name, action, quantity, price, amount, pnl, "
            "       lot_type, sector, note, executed_at "
            "FROM daily_trades WHERE trade_date=? ORDER BY id",
            (trade_date.isoformat(),),
        ).fetchall()
    return [
        {
            "code":        r[0], "name":     r[1], "action":      r[2],
            "quantity":    r[3], "price":    r[4], "amount":      r[5],
            "pnl":         r[6], "lot_type": r[7], "sector":      r[8],
            "note":        r[9], "executed_at": r[10],
        }
        for r in rows
    ]


# ── Alert ─────────────────────────────────────────────────────────────────────

def save_alert(alert: dict, path: str) -> int:
    """Insert a new alert. Returns the new row id.
    alert dict keys: code, name, alert_type, message, severity, created_at (datetime or str)."""
    ca = alert.get("created_at", datetime.now())
    ca_str = ca.isoformat() if isinstance(ca, datetime) else str(ca)
    with _conn(path) as con:
        cur = con.execute(
            "INSERT INTO alerts (code, name, alert_type, message, severity, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                alert.get("code"),
                alert.get("name"),
                alert.get("alert_type"),
                alert.get("message"),
                alert.get("severity", "normal"),
                ca_str,
            ),
        )
        return cur.lastrowid


def load_pending_alerts(path: str) -> list[dict]:
    """Return all unsent alerts ordered by created_at."""
    with _conn(path) as con:
        rows = con.execute(
            "SELECT id, code, name, alert_type, message, severity, created_at "
            "FROM alerts WHERE sent=0 ORDER BY created_at",
        ).fetchall()
    return [
        {
            "id": r[0], "code": r[1], "name": r[2],
            "alert_type": r[3], "message": r[4],
            "severity": r[5], "created_at": r[6],
        }
        for r in rows
    ]


def mark_alert_sent(alert_id: int, path: str) -> None:
    """Mark an alert as sent so it won't appear in load_pending_alerts."""
    with _conn(path) as con:
        con.execute("UPDATE alerts SET sent=1 WHERE id=?", (alert_id,))
