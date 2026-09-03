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


#: 「確定持有」的狀態。強平、對帳、損益結算都必須涵蓋這些。
#:
#: sell_submitted 也算持有——賣單送出但未成交時部位仍在手上。漏掉它會讓
#: 系統以為已平倉，13:00 強平也不再處理，隔天就是交割義務。
HELD_STATUSES = ("active", "sell_submitted")

#: 「不確定」的狀態：買單已送出但未確認成交，可能持有也可能沒有。
#: 不能當成持有（對它下賣單會變成放空），但必須另行向券商對帳確認。
UNCERTAIN_STATUSES = ("buy_submitted",)


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _conn(db_path: Optional[str]):
    db_path = db_path or _DB_PATH
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
            buy_order_id    TEXT,
            sell_order_id   TEXT,
            updated_at      TEXT,
            PRIMARY KEY (trade_date, code)
        )
        """
    )
    # 後加的欄位：CREATE TABLE IF NOT EXISTS 對既有資料表是 no-op，
    # 光靠建表語句永遠補不上（見 LESSONS.md 錯誤 9）。
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dt_positions)")}
    for col in ("buy_order_id", "sell_order_id"):
        if col not in cols:
            conn.execute(f"ALTER TABLE dt_positions ADD COLUMN {col} TEXT")
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
    """首次讀某日且 DB 該日無資料時，一次性把舊 JSON 匯入為當日持倉。

    JSON 檔的 mtime 日期必須等於 trade_date 才遷移：舊鏡像（例如昨日收盤後
    留下的檔案）不得被復活成今日持倉。
    """
    key = (db_path, trade_date)
    if key in _migrated:
        return
    _migrated.add(key)
    row = conn.execute(
        "SELECT COUNT(*) FROM dt_positions WHERE trade_date=?", (trade_date,)
    ).fetchone()
    if row and row[0] > 0:
        return
    try:
        mtime_date = date.fromtimestamp(Path(json_path).stat().st_mtime).isoformat()
        if mtime_date != trade_date:
            log.info(
                "dt_position_store: %s 的 mtime 日期 %s != %s，跳過遷移（過期鏡像）",
                json_path, mtime_date, trade_date,
            )
            return
    except OSError:
        return  # 檔案不存在等 → 無可遷移
    legacy = _parse_json_file(json_path)
    if not legacy:
        return
    for p in legacy:
        _upsert(conn, trade_date, p)
    conn.commit()
    log.info("dt_position_store: 由 %s 一次性遷移 %d 筆持倉至 DB（trade_date=%s）",
             json_path, len(legacy), trade_date)


def _write_mirror(conn: sqlite3.Connection, json_path: Optional[str], trade_date: str) -> None:
    json_path = json_path or _JSON_MIRROR
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

def load_positions(trade_date: Optional[str] = None, db_path: Optional[str] = None,
                   json_path: Optional[str] = None) -> list[DaytradingPosition]:
    td = trade_date or _today()
    db_path = db_path or _DB_PATH
    json_path = json_path or _JSON_MIRROR
    with _conn(db_path) as conn:
        _maybe_migrate(conn, td, db_path, json_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM dt_positions WHERE trade_date=? ORDER BY code", (td,)
        ).fetchall()
        return [_row_to_pos(r) for r in rows]


def save_positions(positions: list[DaytradingPosition],
                   trade_date: Optional[str] = None, db_path: Optional[str] = None,
                   json_path: Optional[str] = None) -> None:
    """UPSERT 傳入的持倉（不刪除其他 code）。只寫你真正改過的股票以避開 race。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        for p in positions:
            _upsert(conn, td, p)
        conn.commit()
        _write_mirror(conn, json_path, td)


def replace_today(positions: list[DaytradingPosition],
                  trade_date: Optional[str] = None, db_path: Optional[str] = None,
                  json_path: Optional[str] = None) -> None:
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
                 db_path: Optional[str] = None, json_path: Optional[str] = None) -> bool:
    """買單成交：status→active，記錄進場價/數量，初始 peak=entry_price。

    回傳是否有更新到列（False = 該 (trade_date, code) 不存在，呼叫端必須告警：
    券商已成交但持倉狀態機沒有這筆，之後的監控/強平都看不到）。
    """
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            """UPDATE dt_positions
               SET status='active', entry_price=?, peak_price=?, quantity=?,
                   lot_type=?, updated_at=?
               WHERE trade_date=? AND code=?""",
            (entry_price, entry_price, quantity, lot_type, _now(), td, code),
        )
        conn.commit()
        _write_mirror(conn, json_path, td)
        return cur.rowcount > 0


def mark_buy_submitted(code: str, order_id: Optional[str] = None,
                       trade_date: Optional[str] = None,
                       db_path: Optional[str] = None,
                       json_path: Optional[str] = None) -> bool:
    """watching → buy_submitted（CAS）。回 True 表示本呼叫者送出了委託。

    「送出委託」不是「已成交」。提前標成 active 會讓監控對不存在的部位算
    損益、讓 13:00 強平對沒成交的部位下賣單（變成放空）。
    """
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='buy_submitted', buy_order_id=?,"
            " updated_at=? WHERE trade_date=? AND code=? AND status='watching'",
            (order_id, _now(), td, code),
        )
        conn.commit()
        ok = cur.rowcount == 1
        if ok:
            _write_mirror(conn, json_path, td)
    return ok


def confirm_buy_filled(code: str, entry_price: float, quantity: int,
                       lot_type: str = "common",
                       trade_date: Optional[str] = None,
                       db_path: Optional[str] = None,
                       json_path: Optional[str] = None) -> bool:
    """buy_submitted → active（僅在**券商回報成交**後呼叫）。

    來源必須是券商的成交回報，不是 place_order 的回傳值。沒送過委託就回報
    成交代表狀態機被繞過，一律拒絕。
    """
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='active', entry_price=?,"
            " peak_price=?, quantity=?, lot_type=?, updated_at=?"
            " WHERE trade_date=? AND code=? AND status='buy_submitted'",
            (entry_price, entry_price, quantity, lot_type, _now(), td, code),
        )
        conn.commit()
        ok = cur.rowcount == 1
        if ok:
            _write_mirror(conn, json_path, td)
    return ok


def revert_buy_submitted(code: str, trade_date: Optional[str] = None,
                         db_path: Optional[str] = None,
                         json_path: Optional[str] = None) -> bool:
    """buy_submitted → watching（委託被退、逾時取消）。

    不回滾的話這一檔今天就卡在 buy_submitted，既不會成交也不會重試。
    """
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='watching', buy_order_id=NULL,"
            " updated_at=? WHERE trade_date=? AND code=? AND status='buy_submitted'",
            (_now(), td, code),
        )
        conn.commit()
        ok = cur.rowcount == 1
        if ok:
            _write_mirror(conn, json_path, td)
    return ok


def mark_sell_submitted(code: str, order_id: Optional[str] = None,
                        trade_date: Optional[str] = None,
                        db_path: Optional[str] = None,
                        json_path: Optional[str] = None) -> bool:
    """active → sell_submitted（CAS）。回 True 表示本呼叫者取得出場權。

    取代 claim_for_close 直接跳到 closed 的做法：賣單送出後、成交確認前，
    部位仍在手上（見 HELD_STATUSES）。
    """
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='sell_submitted', sell_order_id=?,"
            " updated_at=? WHERE trade_date=? AND code=? AND status='active'",
            (order_id, _now(), td, code),
        )
        conn.commit()
        ok = cur.rowcount == 1
        if ok:
            _write_mirror(conn, json_path, td)
    return ok


def confirm_sell_filled(code: str, trade_date: Optional[str] = None,
                        db_path: Optional[str] = None,
                        json_path: Optional[str] = None) -> bool:
    """sell_submitted → closed（僅在券商回報成交後呼叫）。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='closed', updated_at=?"
            " WHERE trade_date=? AND code=? AND status='sell_submitted'",
            (_now(), td, code),
        )
        conn.commit()
        ok = cur.rowcount == 1
        if ok:
            _write_mirror(conn, json_path, td)
    return ok


def revert_sell_submitted(code: str, trade_date: Optional[str] = None,
                          db_path: Optional[str] = None,
                          json_path: Optional[str] = None) -> bool:
    """sell_submitted → active（賣單被退）。

    不回滾的話系統以為已平倉、實際上還抱著，13:00 強平也不會再處理它，
    隔天就是交割義務。
    """
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='active', sell_order_id=NULL,"
            " updated_at=? WHERE trade_date=? AND code=? AND status='sell_submitted'",
            (_now(), td, code),
        )
        conn.commit()
        ok = cur.rowcount == 1
        if ok:
            _write_mirror(conn, json_path, td)
    return ok


def mark_skipped(code: str, trade_date: Optional[str] = None,
                 db_path: Optional[str] = None, json_path: Optional[str] = None) -> bool:
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='skipped', updated_at=? "
            "WHERE trade_date=? AND code=?",
            (_now(), td, code),
        )
        conn.commit()
        _write_mirror(conn, json_path, td)
        return cur.rowcount > 0


def mark_closed(code: str, trade_date: Optional[str] = None,
                db_path: Optional[str] = None, json_path: Optional[str] = None) -> bool:
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='closed', updated_at=? "
            "WHERE trade_date=? AND code=?",
            (_now(), td, code),
        )
        conn.commit()
        _write_mirror(conn, json_path, td)
        return cur.rowcount > 0


def claim_for_close(code: str, trade_date: Optional[str] = None,
                    db_path: Optional[str] = None, json_path: Optional[str] = None) -> bool:
    """CAS 搶佔出場權：active → closed，單條 SQL 原子完成。

    回傳 True = 本呼叫者搶到、負責下賣單；False = 該持倉非 active（已被
    另一條出場路徑處理、或列不存在）→ 呼叫者不得下賣單，避免重複出場。
    賣單失敗時用 revert_to_active() 回滾，讓下一輪重試。
    """
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='closed', updated_at=? "
            "WHERE trade_date=? AND code=? AND status='active'",
            (_now(), td, code),
        )
        conn.commit()
        if cur.rowcount > 0:
            _write_mirror(conn, json_path, td)
            return True
        return False


def revert_to_active(code: str, trade_date: Optional[str] = None,
                     db_path: Optional[str] = None, json_path: Optional[str] = None) -> bool:
    """claim 之後賣單失敗：closed → active 回滾（僅回滾 closed 狀態）。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET status='active', updated_at=? "
            "WHERE trade_date=? AND code=? AND status='closed'",
            (_now(), td, code),
        )
        conn.commit()
        if cur.rowcount > 0:
            _write_mirror(conn, json_path, td)
            return True
        return False


def get_status(code: str, trade_date: Optional[str] = None,
               db_path: Optional[str] = None) -> Optional[str]:
    """回傳該持倉今日狀態；列不存在回 None。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM dt_positions WHERE trade_date=? AND code=?",
            (td, code),
        ).fetchone()
        return row[0] if row else None


def update_entry_range(code: str, entry_low: Optional[float],
                       entry_high: Optional[float],
                       trade_date: Optional[str] = None,
                       db_path: Optional[str] = None,
                       json_path: Optional[str] = None) -> bool:
    """9:05 再確認調整進場區間：只更新 entry_low/entry_high，不碰其他欄位
    （避免以過期快照 bulk 回存覆蓋並發的 status/sell_attempts 原子更新）。"""
    td = trade_date or _today()
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE dt_positions SET entry_low=?, entry_high=?, updated_at=? "
            "WHERE trade_date=? AND code=?",
            (entry_low, entry_high, _now(), td, code),
        )
        conn.commit()
        _write_mirror(conn, json_path, td)
        return cur.rowcount > 0


def update_peak(code: str, price: float, trade_date: Optional[str] = None,
                db_path: Optional[str] = None, json_path: Optional[str] = None) -> bool:
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
                 db_path: Optional[str] = None, json_path: Optional[str] = None) -> None:
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
                        db_path: Optional[str] = None, json_path: Optional[str] = None) -> int:
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
                          db_path: Optional[str] = None, json_path: Optional[str] = None,
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
                 if p.status in HELD_STATUSES]
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


# ══════════════════════════════════════════════════════════════════════════════
# 券商委託狀態同步
# ══════════════════════════════════════════════════════════════════════════════

#: 視為「已成交（手上有股票）」的券商狀態。
#: PartFilled 也算——部分成交代表確實持有，當成「還沒成交」會讓 13:00 強平
#: 漏掉這個部位，隔天變成交割義務。
_FILLED_STATUSES = ("Filled", "PartFilled")

#: 視為「已失效」的券商狀態 → 回滾狀態機，讓之後可以重試。
_DEAD_STATUSES = ("Cancelled", "Failed", "Inactive")


def _trade_field(obj, *names):
    """從巢狀物件取欄位，取不到回 None（不同 SDK 版本欄位位置略有差異）。"""
    cur = obj
    for n in names:
        cur = getattr(cur, n, None)
        if cur is None:
            return None
    return cur


def uncertain_positions(trade_date: Optional[str] = None,
                        db_path: Optional[str] = None) -> list:
    """回傳狀態為「不確定」（買單已送出、成交未確認）的持倉。

    這些部位可能已經成交但系統不知道：13:00 強平只處理 active，不會碰它們，
    隔天就變成交割義務。必須讓人看見。
    """
    return [p for p in load_positions(trade_date, db_path)
            if p.status in UNCERTAIN_STATUSES]


def sync_order_status(api, trade_date: Optional[str] = None,
                      db_path: Optional[str] = None,
                      json_path: Optional[str] = None) -> dict:
    """向券商查詢委託狀態，推進狀態機。回傳統計 dict。

    狀態機只有在「有人告訴它成交了」時才前進，而那個「有人」必須是**券商**，
    不是 place_order 的回傳值——後者只代表委託已送出。

    查詢失敗不拋出：這是週期性背景工作，中斷主流程的代價高於少同步一輪。
    """
    report = {"synced": 0, "filled": 0, "reverted": 0, "error": ""}
    if api is None:
        return report

    try:
        try:
            api.update_status()
        except Exception as e:      # 有些 SDK 版本不需要或不支援
            log.debug("update_status 失敗（忽略）: %s", e)
        trades = api.list_trades() or []
    except Exception as e:
        log.warning("sync_order_status: 取委託清單失敗: %s", e)
        report["error"] = str(e)
        return report

    td = trade_date or _today()
    # 本系統送出的委託：order_id → (code, 是買還是賣)
    known: dict = {}
    for p in load_positions(td, db_path):
        if p.status == "buy_submitted":
            oid = getattr(p, "buy_order_id", None) or _order_id_of(p, td, db_path, "buy")
            if oid:
                known[str(oid)] = (p.code, "buy")
        elif p.status == "sell_submitted":
            oid = getattr(p, "sell_order_id", None) or _order_id_of(p, td, db_path, "sell")
            if oid:
                known[str(oid)] = (p.code, "sell")

    for t in trades:
        oid = _trade_field(t, "order", "id")
        if oid is None or str(oid) not in known:
            continue        # 手動下單、其他程式送的委託——不屬於本狀態機
        code, side = known[str(oid)]
        status = str(_trade_field(t, "status", "status") or "")

        if status in _FILLED_STATUSES:
            if side == "buy":
                qty = _trade_field(t, "status", "deal_quantity") or 0
                price = _trade_field(t, "status", "deal_price") or 0.0
                if confirm_buy_filled(code, float(price), int(qty),
                                      trade_date=td, db_path=db_path,
                                      json_path=json_path):
                    report["filled"] += 1
                    report["synced"] += 1
            else:
                if confirm_sell_filled(code, trade_date=td, db_path=db_path,
                                       json_path=json_path):
                    report["filled"] += 1
                    report["synced"] += 1

        elif status in _DEAD_STATUSES:
            rollback = revert_buy_submitted if side == "buy" else revert_sell_submitted
            if rollback(code, trade_date=td, db_path=db_path, json_path=json_path):
                report["reverted"] += 1
                report["synced"] += 1
                log.warning("委託 %s（%s %s）狀態 %s，已回滾狀態機",
                            oid, code, side, status)

    if report["synced"]:
        log.info("委託狀態同步：成交 %d、回滾 %d",
                 report["filled"], report["reverted"])
    return report


def _order_id_of(pos, trade_date: str, db_path: Optional[str],
                 side: str) -> Optional[str]:
    """從 DB 直接取 order_id（DaytradingPosition 沒有這兩個欄位）。"""
    col = "buy_order_id" if side == "buy" else "sell_order_id"
    with _conn(db_path) as conn:
        row = conn.execute(
            f"SELECT {col} FROM dt_positions WHERE trade_date=? AND code=?",
            (trade_date, pos.code),
        ).fetchone()
    return row[0] if row else None
