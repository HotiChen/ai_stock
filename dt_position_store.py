"""
dt_position_store.py — 當沖持倉 SQLite 單一真相來源（single source of truth）

背景
----
當沖持倉先前存於 data/daytrading_positions.json，由 main.py（排程器）與
telegram_bot.py（Bot）兩個 process 各自「load → 改 → save 全部」同步，即使改用
原子寫入仍有 read-modify-write race：兩邊同時載入→各改一部分→各自存全部，
後存者會蓋掉先存者對其他股票的變更。

本模組把持倉狀態收斂到 SQLite（data/daytrading_positions.db），提供：
  * 對齊既有介面的 load_positions / save_positions（UPSERT，只寫傳入的股票）
  * replace_today（8:30 新的一天清舊寫新）
  * 原子單筆操作 mark_entered / mark_skipped / mark_closed / update_peak /
    append_alert / record_sell_attempt —— 全部單條 SQL 完成，不經 load-all-save-all，
    因此跨 process 併發安全（SQLite WAL + busy_timeout 序列化寫入）
  * 一次性 JSON 遷移 + 每次寫入的 JSON 鏡像輸出（供 dashboard/app.py 既有讀取相容）
  * reconcile_with_broker —— 與券商實際持倉對帳

save_positions / 原子操作 **只 UPSERT 傳入或指定的 code**，不刪除其他列，這是避開
read-modify-write race 的關鍵：每個 writer 只寫自己真正改過的持倉。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from atomic_json import atomic_write_json
from daytrading_monitor import DaytradingPosition

log = logging.getLogger(__name__)

_DB_PATH = "data/daytrading_positions.db"
_JSON_MIRROR = "data/daytrading_positions.json"

# 每個 (db_path, trade_date) 只嘗試一次 JSON→DB 遷移，避免重複匯入
_migrated: set[tuple[str, str]] = set()


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _conn(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        _init(conn)
        yield conn
    finally:
        conn.close()


def _init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dt_positions (
            trade_date      TEXT    NOT NULL,
            code            TEXT    NOT NULL,
            name            TEXT,
            entry_low       REAL,
            entry_high      REAL,
            target_price    REAL,
            stop_loss       REAL,
            dt_score        INTEGER,
            status          TEXT,
            alerts_sent     TEXT,
            ai_summary      TEXT,
            entry_price     REAL,
            peak_price      REAL,
            quantity        INTEGER,
            lot_type        TEXT,
            sell_attempts   INTEGER DEFAULT 0,
            last_sell_error TEXT    DEFAULT '',
            updated_at      TEXT,
            PRIMARY KEY (trade_date, code)
        )
        """
    )
    conn.commit()


# ── row ↔ dataclass ──────────────────────────────────────────────────────────

def _row_to_pos(r: sqlite3.Row) -> DaytradingPosition:
    try:
        alerts = json.loads(r["alerts_sent"]) if r["alerts_sent"] else []
    except (TypeError, json.JSONDecodeError):
        alerts = []
    return DaytradingPosition(
        code=r["code"],
        name=r["name"] or "",
        entry_low=r["entry_low"],
        entry_high=r["entry_high"],
        target_price=r["target_price"],
        stop_loss=r["stop_loss"],
        dt_score=r["dt_score"] if r["dt_score"] is not None else 0,
        status=r["status"] or "watching",
        alerts_sent=alerts,
        ai_summary=r["ai_summary"] or "",
        entry_price=r["entry_price"],
        peak_price=r["peak_price"],
        quantity=r["quantity"] if r["quantity"] is not None else 0,
        lot_type=r["lot_type"] or "common",
        sell_attempts=r["sell_attempts"] if r["sell_attempts"] is not None else 0,
        last_sell_error=r["last_sell_error"] or "",
    )


def _upsert(conn: sqlite3.Connection, trade_date: str, p: DaytradingPosition) -> None:
    conn.execute(
        """
        INSERT INTO dt_positions (
            trade_date, code, name, entry_low, entry_high, target_price, stop_loss,
            dt_score, status, alerts_sent, ai_summary, entry_price, peak_price,
            quantity, lot_type, sell_attempts, last_sell_error, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(trade_date, code) DO UPDATE SET
            name=excluded.name,
            entry_low=excluded.entry_low,
            entry_high=excluded.entry_high,
            target_price=excluded.target_price,
            stop_loss=excluded.stop_loss,
            dt_score=excluded.dt_score,
            status=excluded.status,
            alerts_sent=excluded.alerts_sent,
            ai_summary=excluded.ai_summary,
            entry_price=excluded.entry_price,
            peak_price=excluded.peak_price,
            quantity=excluded.quantity,
            lot_type=excluded.lot_type,
            sell_attempts=excluded.sell_attempts,
            last_sell_error=excluded.last_sell_error,
            updated_at=excluded.updated_at
        """,
        (
            trade_date, p.code, p.name, p.entry_low, p.entry_high, p.target_price,
            p.stop_loss, p.dt_score, p.status, json.dumps(p.alerts_sent or []),
            p.ai_summary, p.entry_price, p.peak_price, p.quantity, p.lot_type,
            getattr(p, "sell_attempts", 0), getattr(p, "last_sell_error", ""), _now(),
        ),
    )


def _pos_dict(p: DaytradingPosition) -> dict:
    return {
        "code":            p.code,
        "name":            p.name,
        "entry_low":       p.entry_low,
        "entry_high":      p.entry_high,
        "target_price":    p.target_price,
        "stop_loss":       p.stop_loss,
        "dt_score":        p.dt_score,
        "status":          p.status,
        "alerts_sent":     p.alerts_sent,
        "ai_summary":      p.ai_summary,
        "entry_price":     p.entry_price,
        "peak_price":      p.peak_price,
        "quantity":        p.quantity,
        "lot_type":        p.lot_type,
        "sell_attempts":   getattr(p, "sell_attempts", 0),
        "last_sell_error": getattr(p, "last_sell_error", ""),
    }


# ── JSON migration + mirror ──────────────────────────────────────────────────

def _parse_json_file(path: str) -> list[DaytradingPosition]:
    """直接解析舊 JSON 檔（不經 daytrading_monitor，避免委派回本模組的遞迴）。"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("dt_position_store: parse JSON %s failed: %s", path, e)
        return []
    out: list[DaytradingPosition] = []
    for d in data:
        out.append(DaytradingPosition(
            code=d["code"], name=d.get("name", ""),
            entry_low=d.get("entry_low"), entry_high=d.get("entry_high"),
            target_price=d.get("target_price"), stop_loss=d.get("stop_loss"),
            dt_score=d.get("dt_score", 0), status=d.get("status", "watching"),
            alerts_sent=d.get("alerts_sent", []), ai_summary=d.get("ai_summary", ""),
            entry_price=d.get("entry_price"), peak_price=d.get("peak_price"),
            quantity=d.get("quantity", 0), lot_type=d.get("lot_type", "common"),
            sell_attempts=d.get("sell_attempts", 0),
            last_sell_error=d.get("last_sell_error", ""),
        ))
    return out


def _maybe_migrate(conn: sqlite3.Connection, trade_date: str, db_path: str,
                   json_path: str) -> None:
    """首次讀某日且 DB 該日無資料時，一次性把舊 JSON 匯入為當日持倉。"""
    key = (db_path, trade_date)
    if key in _migrated:
        return
    _migrated.add(key)
    row = conn.execute(
        "SELECT COUNT(*) FROM dt_positions WHERE trade_date=?", (trade_date,)
    ).fetchone()
    if row and row[0] > 0:
        return
    legacy = _parse_json_file(json_path)
    if not legacy:
        return
    for p in legacy:
        _upsert(conn, trade_date, p)
    conn.commit()
    log.info("dt_position_store: 由 %s 一次性遷移 %d 筆持倉至 DB（trade_date=%s）",
             json_path, len(legacy), trade_date)


def _write_mirror(conn: sqlite3.Connection, json_path: str, trade_date: str) -> None:
    """把當日持倉鏡像輸出成 JSON（供 dashboard/app.py 既有讀取相容）。"""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM dt_positions WHERE trade_date=? ORDER BY code", (trade_date,)
    ).fetchall()
    data = [_pos_dict(_row_to_pos(r)) for r in rows]
    try:
        atomic_write_json(json_path, data, indent=2)
    except OSError as e:
        log.warning("dt_position_store: 寫 JSON 鏡像 %s 失敗: %s", json_path, e)


# ── Bulk API（對齊既有介面）───────────────────────────────────────────────────

def load_positions(trade_date: Optional[str] = None, db_path: str = _DB_PATH,
                   json_path: str = _JSON_MIRROR) -> list[DaytradingPosition]:
    td = trade_date or _today()
    with _conn(db_path) as conn:
        _maybe_migrate(conn, td, db_path, json_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM dt_positions WHERE trade_date=? ORDER BY code", (td,)
        ).fetchall()
        return [_row_to_pos(r) for r in rows]


def save_positions(positions: list[DaytradingPosition],
                   trade_date: Optional[str] = None, db_path: str = _DB_PATH,
                   json_path: str = _JSON_MIRROR) -> None:
    """UPSERT 傳入的持倉（不刪除其他 code）。只寫你真正改過的股票以避開 race。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        for p in positions:
            _upsert(conn, td, p)
        conn.commit()
        _write_mirror(conn, json_path, td)


def replace_today(positions: list[DaytradingPosition],
                  trade_date: Optional[str] = None, db_path: str = _DB_PATH,
                  json_path: str = _JSON_MIRROR) -> None:
    """8:30 新的一天：清掉當日舊持倉，寫入新的一批。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM dt_positions WHERE trade_date=?", (td,))
        for p in positions:
            _upsert(conn, td, p)
        conn.commit()
        _write_mirror(conn, json_path, td)


# ── 原子單筆操作（單條 SQL；跨 process 併發安全）─────────────────────────────

def mark_entered(code: str, entry_price: float, quantity: int,
                 lot_type: str = "common", trade_date: Optional[str] = None,
                 db_path: str = _DB_PATH, json_path: str = _JSON_MIRROR) -> None:
    """買單成交：status→active，記錄進場價/數量，初始 peak=entry_price。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        conn.execute(
            """UPDATE dt_positions
               SET status='active', entry_price=?, peak_price=?, quantity=?,
                   lot_type=?, updated_at=?
               WHERE trade_date=? AND code=?""",
            (entry_price, entry_price, quantity, lot_type, _now(), td, code),
        )
        conn.commit()
        _write_mirror(conn, json_path, td)


def mark_skipped(code: str, trade_date: Optional[str] = None,
                 db_path: str = _DB_PATH, json_path: str = _JSON_MIRROR) -> None:
    td = trade_date or _today()
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE dt_positions SET status='skipped', updated_at=? "
            "WHERE trade_date=? AND code=?",
            (_now(), td, code),
        )
        conn.commit()
        _write_mirror(conn, json_path, td)


def mark_closed(code: str, trade_date: Optional[str] = None,
                db_path: str = _DB_PATH, json_path: str = _JSON_MIRROR) -> None:
    td = trade_date or _today()
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE dt_positions SET status='closed', updated_at=? "
            "WHERE trade_date=? AND code=?",
            (_now(), td, code),
        )
        conn.commit()
        _write_mirror(conn, json_path, td)


def update_peak(code: str, price: float, trade_date: Optional[str] = None,
                db_path: str = _DB_PATH, json_path: str = _JSON_MIRROR) -> bool:
    """峰值只升不降：條件式 UPDATE，回傳是否有更新。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            """UPDATE dt_positions SET peak_price=?, updated_at=?
               WHERE trade_date=? AND code=?
                 AND (peak_price IS NULL OR peak_price < ?)""",
            (price, _now(), td, code, price),
        )
        conn.commit()
        changed = cur.rowcount > 0
        if changed:
            _write_mirror(conn, json_path, td)
        return changed


def append_alert(code: str, alert_type: str, trade_date: Optional[str] = None,
                 db_path: str = _DB_PATH, json_path: str = _JSON_MIRROR) -> None:
    """把 alert_type 追加到 alerts_sent JSON 陣列（若尚未存在），單條 SQL 原子完成。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        conn.execute(
            """UPDATE dt_positions
               SET alerts_sent = json_insert(COALESCE(alerts_sent,'[]'), '$[#]', ?),
                   updated_at = ?
               WHERE trade_date=? AND code=?
                 AND NOT EXISTS (
                    SELECT 1 FROM json_each(COALESCE(dt_positions.alerts_sent,'[]'))
                    WHERE value = ?
                 )""",
            (alert_type, _now(), td, code, alert_type),
        )
        conn.commit()
        _write_mirror(conn, json_path, td)


def record_sell_attempt(code: str, error: str, trade_date: Optional[str] = None,
                        db_path: str = _DB_PATH, json_path: str = _JSON_MIRROR) -> int:
    """賣單失敗：sell_attempts+1、記錄 last_sell_error，回傳累計失敗次數。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        conn.execute(
            """UPDATE dt_positions
               SET sell_attempts = sell_attempts + 1, last_sell_error=?, updated_at=?
               WHERE trade_date=? AND code=?""",
            (error or "", _now(), td, code),
        )
        conn.commit()
        _write_mirror(conn, json_path, td)
        row = conn.execute(
            "SELECT sell_attempts FROM dt_positions WHERE trade_date=? AND code=?",
            (td, code),
        ).fetchone()
        return int(row[0]) if row else 0


# ── 券商對帳 ──────────────────────────────────────────────────────────────────

def _broker_positions(api) -> Optional[dict[str, int]]:
    """用 Shioaji 取券商實際股票持倉，回傳 {code: quantity}；失敗回 None。"""
    try:
        raw = api.list_positions(api.stock_account)
    except Exception as e:  # noqa: BLE001 — 對帳失敗不告警，只 log
        log.warning("reconcile: 取券商持倉失敗: %s", e)
        return None
    out: dict[str, int] = {}
    for p in raw or []:
        code = getattr(p, "code", None)
        qty = getattr(p, "quantity", 0)
        if code is None:
            continue
        try:
            out[str(code)] = int(qty)
        except (TypeError, ValueError):
            out[str(code)] = 0
    return out


def reconcile_with_broker(api, trade_date: Optional[str] = None,
                          db_path: str = _DB_PATH, json_path: str = _JSON_MIRROR,
                          chat_id: Optional[str] = None) -> Optional[dict]:
    """比對 DB 中 status=active 的當沖持倉與券商實際持倉。

    回傳差異報告 dict（broker 取得失敗回 None，不告警）：
      db_only       : DB active 但券商查無（可能已出場或買單未成交）
      broker_only   : 券商有但 DB 無 active（本系統外部位，只 log 不動作）
      qty_mismatch  : 兩邊都有但數量不符 [(code, db_qty, broker_qty), ...]
      matched       : 數量一致的 code 清單

    有 actionable 差異（db_only / qty_mismatch）且提供 chat_id 時發 Telegram 告警。
    """
    td = trade_date or _today()
    broker = _broker_positions(api)
    if broker is None:
        return None

    positions = [p for p in load_positions(td, db_path, json_path)
                 if p.status == "active"]
    db_map = {p.code: p for p in positions}

    db_only: list[str] = []
    qty_mismatch: list[tuple[str, int, int]] = []
    matched: list[str] = []
    for code, pos in db_map.items():
        db_qty = _expected_shares(pos)
        if code not in broker or broker[code] == 0:
            db_only.append(code)
        elif broker[code] != db_qty:
            qty_mismatch.append((code, db_qty, broker[code]))
        else:
            matched.append(code)

    broker_only = [c for c in broker if c not in db_map and broker[c] != 0]

    report = {
        "db_only": db_only,
        "broker_only": broker_only,
        "qty_mismatch": qty_mismatch,
        "matched": matched,
    }

    for c in broker_only:
        log.info("reconcile: 券商持有 %s（本系統無 active 紀錄）數量=%d，不動作",
                 c, broker[c])

    actionable = bool(db_only or qty_mismatch)
    if actionable:
        log.warning("reconcile 差異：db_only=%s qty_mismatch=%s", db_only, qty_mismatch)
        if chat_id:
            try:
                from telegram_bot import send_text
                send_text(chat_id, _format_reconcile(report, db_map))
            except Exception as e:  # noqa: BLE001
                log.warning("reconcile 告警發送失敗: %s", e)
    return report


def _expected_shares(pos: DaytradingPosition) -> int:
    """DB 持倉的預期券商股數。common=張×1000，零股=股數。"""
    if pos.lot_type == "common":
        return pos.quantity * 1000
    return pos.quantity


def _format_reconcile(report: dict, db_map: dict) -> str:
    lines = ["🔎 <b>當沖持倉對帳差異</b>", "━━━━━━━━━━━━━━━━"]
    for code in report["db_only"]:
        name = db_map[code].name if code in db_map else code
        lines.append(f"⚠️ <b>{code} {name}</b>　DB active 但券商查無（請確認是否已出場）")
    for code, db_qty, br_qty in report["qty_mismatch"]:
        name = db_map[code].name if code in db_map else code
        lines.append(f"⚠️ <b>{code} {name}</b>　數量不符 DB={db_qty} 券商={br_qty}")
    for code in report["broker_only"]:
        lines.append(f"ℹ️ 券商持有 {code}（本系統外部位，不動作）")
    lines.append("<i>請人工檢查持倉一致性。</i>")
    return "\n".join(lines)
