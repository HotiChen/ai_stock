from __future__ import annotations

"""
tests/test_trade_timestamp.py — TDD 測試 executed_at 欄位新增至 daily_trades

涵蓋：
  - 遷移冪等性：全新 DB 與既有舊 DB（無 executed_at 欄位）皆可正常初始化
  - save_daily_trade() 帶 executed_at：原值保留
  - save_daily_trade() 不帶 executed_at：自動填入可解析的 ISO 8601 字串
  - load_daily_trades() 回傳的每筆 dict 包含 executed_at key
"""

import sqlite3
import tempfile
from datetime import date, datetime

import pytest

from research_db import init_db, save_daily_trade, load_daily_trades


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """全新初始化的資料庫（包含 executed_at 欄位）。"""
    path = str(tmp_path / "test_ts.db")
    init_db(path)
    return path


@pytest.fixture
def legacy_db(tmp_path):
    """模擬舊版資料庫（daily_trades 沒有 executed_at 欄位）。"""
    path = str(tmp_path / "legacy.db")
    # 以舊版 DDL 建立表（不含 executed_at）
    with sqlite3.connect(path) as con:
        con.execute("""
            CREATE TABLE daily_trades (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                code       TEXT NOT NULL,
                name       TEXT,
                action     TEXT NOT NULL,
                quantity   INTEGER,
                price      REAL,
                amount     REAL,
                pnl        REAL,
                lot_type   TEXT DEFAULT 'common',
                sector     TEXT DEFAULT '未知',
                note       TEXT
            )
        """)
    return path


def _make_trade(**kwargs):
    defaults = dict(
        trade_date=date(2026, 6, 11),
        code="2330",
        name="台積電",
        action="buy",
        quantity=1,
        price=850.0,
        amount=850_000.0,
        pnl=None,
        lot_type="common",
        sector="半導體",
        note="測試",
    )
    defaults.update(kwargs)
    return defaults


# ── 遷移冪等性 ────────────────────────────────────────────────────────────────

class TestMigrationIdempotent:
    """executed_at 欄位的遷移必須是冪等的。"""

    def test_init_db_twice_does_not_raise(self, tmp_path):
        """init_db() 呼叫兩次不應拋出任何例外。"""
        path = str(tmp_path / "double.db")
        init_db(path)
        init_db(path)  # 第二次必須安全

    def test_legacy_db_upgraded_on_init(self, legacy_db):
        """舊版 DB（無 executed_at）執行 init_db() 後欄位存在。"""
        init_db(legacy_db)
        with sqlite3.connect(legacy_db) as con:
            cols = {row[1] for row in con.execute("PRAGMA table_info(daily_trades)").fetchall()}
        assert "executed_at" in cols

    def test_legacy_db_init_does_not_raise(self, legacy_db):
        """對舊版 DB 執行 init_db() 不應拋出任何例外。"""
        init_db(legacy_db)  # 不應 raise

    def test_new_db_has_executed_at_column(self, db):
        """全新 DB 的 daily_trades 表應有 executed_at 欄位。"""
        with sqlite3.connect(db) as con:
            cols = {row[1] for row in con.execute("PRAGMA table_info(daily_trades)").fetchall()}
        assert "executed_at" in cols


# ── save 帶 executed_at ────────────────────────────────────────────────────────

class TestSaveWithExplicitExecutedAt:
    """傳入 executed_at 時，原值應原封不動存入。"""

    def test_explicit_executed_at_preserved(self, db):
        """save_daily_trade() 帶 executed_at：讀回值與傳入值相同。"""
        ts = "2026-06-11T09:30:00"
        save_daily_trade(_make_trade(executed_at=ts), db)
        result = load_daily_trades(date(2026, 6, 11), db)
        assert result[0]["executed_at"] == ts

    def test_explicit_datetime_object_preserved(self, db):
        """executed_at 為 datetime 物件時自動轉為 ISO 字串存入。"""
        dt = datetime(2026, 6, 11, 9, 30, 0)
        save_daily_trade(_make_trade(executed_at=dt.isoformat(timespec="seconds")), db)
        result = load_daily_trades(date(2026, 6, 11), db)
        assert result[0]["executed_at"] == "2026-06-11T09:30:00"

    def test_legacy_db_save_with_executed_at_after_migration(self, legacy_db):
        """舊版 DB 遷移後可正確儲存 executed_at。"""
        init_db(legacy_db)
        ts = "2026-06-11T10:00:00"
        save_daily_trade(_make_trade(executed_at=ts), legacy_db)
        result = load_daily_trades(date(2026, 6, 11), legacy_db)
        assert result[0]["executed_at"] == ts


# ── save 不帶 executed_at ──────────────────────────────────────────────────────

class TestSaveWithoutExecutedAt:
    """不傳 executed_at 時，應自動填入可解析的 ISO 8601 時間戳。"""

    def test_auto_filled_when_absent(self, db):
        """save_daily_trade() 不帶 executed_at → 自動填入時間戳。"""
        save_daily_trade(_make_trade(), db)
        result = load_daily_trades(date(2026, 6, 11), db)
        assert result[0]["executed_at"] is not None

    def test_auto_filled_is_parseable_iso(self, db):
        """自動填入的 executed_at 可用 datetime.fromisoformat() 解析。"""
        save_daily_trade(_make_trade(), db)
        result = load_daily_trades(date(2026, 6, 11), db)
        ts = result[0]["executed_at"]
        parsed = datetime.fromisoformat(ts)  # 不應 raise
        assert isinstance(parsed, datetime)

    def test_auto_filled_is_recent(self, db):
        """自動填入的時間戳應在測試執行前後 60 秒內（防止使用遠古預設值）。
        比對時截去毫秒，因為 isoformat(timespec='seconds') 不含毫秒。
        """
        before = datetime.now().replace(microsecond=0)
        save_daily_trade(_make_trade(), db)
        after = datetime.now()
        result = load_daily_trades(date(2026, 6, 11), db)
        ts = datetime.fromisoformat(result[0]["executed_at"])
        assert before <= ts <= after

    def test_auto_filled_has_seconds_precision(self, db):
        """自動填入格式精確到秒（timespec='seconds'）。"""
        save_daily_trade(_make_trade(), db)
        result = load_daily_trades(date(2026, 6, 11), db)
        ts = result[0]["executed_at"]
        # ISO 格式：YYYY-MM-DDTHH:MM:SS（不帶毫秒）
        assert len(ts) == len("2026-06-11T09:30:00")

    def test_none_executed_at_is_auto_filled(self, db):
        """executed_at=None 等同未傳，應自動填入。"""
        save_daily_trade(_make_trade(executed_at=None), db)
        result = load_daily_trades(date(2026, 6, 11), db)
        assert result[0]["executed_at"] is not None


# ── load_daily_trades 回傳結構 ────────────────────────────────────────────────

class TestLoadDailyTradesStructure:
    """load_daily_trades() 回傳的每筆 dict 必須包含 executed_at key。"""

    def test_returned_dict_has_executed_at_key(self, db):
        """load_daily_trades() 回傳的 dict 包含 executed_at 鍵。"""
        save_daily_trade(_make_trade(), db)
        result = load_daily_trades(date(2026, 6, 11), db)
        assert "executed_at" in result[0]

    def test_multiple_trades_all_have_executed_at(self, db):
        """多筆記錄每筆都有 executed_at。"""
        save_daily_trade(_make_trade(code="2330"), db)
        save_daily_trade(_make_trade(code="2454", name="聯發科"), db)
        result = load_daily_trades(date(2026, 6, 11), db)
        assert all("executed_at" in r for r in result)

    def test_legacy_db_load_after_migration_has_executed_at(self, legacy_db):
        """舊 DB 遷移後儲存的資料，load 出來也有 executed_at。"""
        # 先在舊 DB 插入一筆舊資料（沒有 executed_at）
        with sqlite3.connect(legacy_db) as con:
            con.execute(
                "INSERT INTO daily_trades (trade_date, code, action) VALUES (?, ?, ?)",
                ("2026-06-11", "2330", "buy"),
            )
        # 遷移
        init_db(legacy_db)
        result = load_daily_trades(date(2026, 6, 11), legacy_db)
        # 舊資料 executed_at 為 NULL，但 key 必須存在
        assert "executed_at" in result[0]
        # 舊資料的 executed_at 為 None（欄位是 nullable）
        assert result[0]["executed_at"] is None

    def test_existing_columns_still_present(self, db):
        """新增欄位後，既有欄位（code, price 等）仍正確回傳。"""
        save_daily_trade(_make_trade(price=900.0), db)
        result = load_daily_trades(date(2026, 6, 11), db)
        assert result[0]["code"] == "2330"
        assert result[0]["price"] == pytest.approx(900.0)
        assert result[0]["lot_type"] == "common"
