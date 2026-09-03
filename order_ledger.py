"""
order_ledger.py — 跨行程的下單去重（原子宣告）

問題
----
executor.is_duplicate_order(code, action, prior_orders) 由呼叫端自己傳入
prior_orders。實際上只有 main.py 的波段路徑有傳；當沖自動買、Telegram
快速下單、FastAPI backend 都沒傳——守衛等於沒開。

而且它是記憶體內的判斷。main.py、telegram_bot.py、FastAPI backend 是三個
獨立行程，彼此看不到對方送出的委託：使用者在 Telegram 按「快速下單」的
同時，9:10 排程也在買同一檔，兩張單都會送出去。

機制
----
SQLite 的 ``INSERT OR IGNORE`` + ``UNIQUE(trade_date, code, action)``：
單一 SQL 敘述完成「檢查並宣告」，天然原子，跨行程有效。搶到才下單。

    claim()   下單前宣告；回 False 代表別人已經在處理這一筆
    release() 下單失敗時釋放，讓之後可以重試
    confirm() 下單成功後標記，之後不再釋放

只擋買進
--------
    重複買進 → 曝險加倍、花掉沒打算花的錢
    重複賣出 → 券商會擋（現股不能賣超過庫存）
    **被擋住的賣出 → 部位沒平掉 → 隔日交割義務 → 可能違約**

最後一項最嚴重。所以本模組只用於買進路徑，賣出一律放行——與 halt.py 的
禁買閘門同一個道理：緊急機制只能擋「進」，不能擋「出」。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date as _date
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/order_ledger.db"


def _normalize(action: str) -> str:
    """'Action.Buy' / 'BUY' / 'buy' → 'buy'。

    Shioaji 回傳 'Action.Buy'，內部用 'buy'。不正規化的話同一檔會被視為
    兩筆不同的宣告，去重形同虛設。
    """
    return str(action).lower().split(".")[-1]


def _conn(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")      # 跨行程並行讀寫
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_claims (
            trade_date TEXT NOT NULL,
            code       TEXT NOT NULL,
            action     TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'claimed',
            order_id   TEXT,
            claimed_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            note       TEXT,
            UNIQUE(trade_date, code, action)
        )
    """)
    return conn


def claim(code: str, action: str, trade_date: Optional[_date] = None,
          db_path: str = DEFAULT_DB_PATH, note: str = "") -> bool:
    """宣告一筆下單意圖。回 True 表示搶到，False 表示別人已經在處理。

    用單一 INSERT OR IGNORE 完成，rowcount 即結果——不可拆成「先查再寫」，
    那之間的空隙正是重複下單發生的地方。
    """
    d = (trade_date or _date.today()).isoformat()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO order_claims (trade_date, code, action, note)"
            " VALUES (?, ?, ?, ?)",
            (d, code, _normalize(action), note),
        )
        return cur.rowcount == 1


def release(code: str, action: str, trade_date: Optional[_date] = None,
            db_path: str = DEFAULT_DB_PATH) -> None:
    """釋放尚未送出的宣告（下單失敗時呼叫），讓之後可以重試。

    已 confirm 的宣告**不會**被釋放——委託已經在券商手上，釋放它等於
    允許重複下單。
    """
    d = (trade_date or _date.today()).isoformat()
    with _conn(db_path) as conn:
        conn.execute(
            "DELETE FROM order_claims"
            " WHERE trade_date=? AND code=? AND action=? AND status='claimed'",
            (d, code, _normalize(action)),
        )


def confirm(code: str, action: str, order_id: Optional[str] = None,
            trade_date: Optional[_date] = None,
            db_path: str = DEFAULT_DB_PATH) -> None:
    """標記委託已送出。之後 release 不再生效。

    註：這是「已送出」，**不是「已成交」**。成交狀態機是另一件事。
    """
    d = (trade_date or _date.today()).isoformat()
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE order_claims SET status='submitted', order_id=?"
            " WHERE trade_date=? AND code=? AND action=?",
            (order_id, d, code, _normalize(action)),
        )


def is_claimed(code: str, action: str, trade_date: Optional[_date] = None,
               db_path: str = DEFAULT_DB_PATH) -> bool:
    d = (trade_date or _date.today()).isoformat()
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM order_claims WHERE trade_date=? AND code=? AND action=?",
            (d, code, _normalize(action)),
        ).fetchone()
    return row is not None


def list_claims(trade_date: Optional[_date] = None,
                db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    d = (trade_date or _date.today()).isoformat()
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT trade_date, code, action, status, order_id, claimed_at, note"
            " FROM order_claims WHERE trade_date=? ORDER BY claimed_at",
            (d,),
        ).fetchall()
    return [dict(r) for r in rows]
