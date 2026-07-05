from __future__ import annotations

"""
tests/test_exit_recording.py — 出場記錄測試（GAP 1 + GAP 3）

驗證兩件事：

GAP 3（schema 基礎）：
  - daily_trades 新增可為 NULL 的 exit_reason 欄位
  - save_daily_trade 接受可選的 "exit_reason"，未提供時存 None
  - load_daily_trades 回傳含 exit_reason 的 dict
  - 既有資料庫（無 exit_reason 欄位）經 init_db 後可冪等升級

GAP 1（自動出場可見性）：
  - AlertWorker 在 auto_execute=True 且 force_stop_loss 成功時，
    必須寫入「一筆」action=sell 的 daily_trades，且 exit_reason 等於 alert_type
  - force_stop_loss 失敗時不可寫入賣出記錄
  - DB 寫入失敗不可中斷 alert thread（包在 try/except）
"""

import queue
import sqlite3
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest


# ── GAP 3：exit_reason schema ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_dt_store(tmp_path, monkeypatch):
    """隔離 dt_position_store 的預設路徑：AlertWorker 出場前的 CAS claim 會讀
    持倉狀態機，不得受 repo data/ 下殘留的執行期檔案影響。"""
    import dt_position_store as dps
    monkeypatch.setattr(dps, "_DB_PATH", str(tmp_path / "dtpos.db"))
    monkeypatch.setattr(dps, "_JSON_MIRROR", str(tmp_path / "dtpos.json"))
    dps._migrated.clear()

class TestExitReasonSchema:
    def test_save_and_load_round_trips_exit_reason(self, tmp_path):
        """save_daily_trade 帶 exit_reason → load_daily_trades 取回同值。"""
        from research_db import init_db, save_daily_trade, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        save_daily_trade({
            "trade_date": date(2026, 6, 13),
            "code": "2330", "name": "台積電", "action": "sell",
            "quantity": 1000, "price": 850.0, "exit_reason": "trailing_stop",
        }, db_path)

        rows = load_daily_trades(date(2026, 6, 13), db_path)
        assert len(rows) == 1
        assert rows[0]["exit_reason"] == "trailing_stop"

    def test_exit_reason_defaults_to_none(self, tmp_path):
        """未提供 exit_reason → 存 None。"""
        from research_db import init_db, save_daily_trade, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        save_daily_trade({
            "trade_date": date(2026, 6, 13),
            "code": "2330", "name": "台積電", "action": "buy",
            "quantity": 1000, "price": 850.0,
        }, db_path)

        rows = load_daily_trades(date(2026, 6, 13), db_path)
        assert rows[0]["exit_reason"] is None

    def test_migration_adds_exit_reason_to_legacy_db(self, tmp_path):
        """既有資料庫（無 exit_reason 欄位）→ init_db 冪等升級後可使用。"""
        from research_db import init_db, save_daily_trade, load_daily_trades
        db_path = str(tmp_path / "legacy.db")

        # 建立一個沒有 exit_reason 欄位的舊版 daily_trades
        with sqlite3.connect(db_path) as con:
            con.execute(
                "CREATE TABLE daily_trades ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " trade_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT,"
                " action TEXT NOT NULL, quantity INTEGER, price REAL,"
                " amount REAL, pnl REAL, note TEXT)"
            )

        # 升級
        init_db(db_path)

        cols = {r[1] for r in sqlite3.connect(db_path).execute(
            "PRAGMA table_info(daily_trades)").fetchall()}
        assert "exit_reason" in cols

        # 升級後可正常存取
        save_daily_trade({
            "trade_date": date(2026, 6, 13), "code": "2330", "action": "sell",
            "exit_reason": "stop_loss",
        }, db_path)
        rows = load_daily_trades(date(2026, 6, 13), db_path)
        assert rows[0]["exit_reason"] == "stop_loss"

    def test_init_db_twice_is_idempotent(self, tmp_path):
        """init_db 連續呼叫兩次不應 raise（ALTER TABLE 不重複加欄位）。"""
        from research_db import init_db
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        init_db(db_path)  # second call must not raise


# ── GAP 1：AlertWorker 自動出場寫入 daily_trades ─────────────────────────────

class TestAlertWorkerRecordsExit:
    def _alert(self, alert_type="trailing_stop", **kwargs):
        a = dict(
            code="2330", name="台積電", alert_type=alert_type,
            message="觸發", severity="high",
            created_at=datetime(2026, 6, 13, 11, 0),
            current_price=842.0,
        )
        a.update(kwargs)
        return a

    def _watchlist(self, **kwargs):
        p = dict(code="2330", name="台積電", quantity=1000,
                 lot_type="common", entry_price=820.0)
        p.update(kwargs)
        return [p]

    def test_auto_execute_writes_one_sell_row_with_exit_reason(self, tmp_path):
        """auto_execute 成功 → 恰好一筆 sell daily_trade，exit_reason=alert_type。"""
        from monitor_agent import AlertWorker
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        q = queue.Queue()
        worker = AlertWorker(
            q, db_path=db_path, telegram_chat_id=None,
            auto_execute=True, api=MagicMock(),
            watchlist=self._watchlist(),
        )
        q.put(self._alert(alert_type="trailing_stop"))
        q.put(None)

        with patch("monitor_agent.force_stop_loss", return_value=True), \
                patch("notifier.notify_price_alert"):
            worker.run()

        rows = load_daily_trades(date.today(), db_path)
        sells = [r for r in rows if r["action"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["code"] == "2330"
        assert sells[0]["exit_reason"] == "trailing_stop"
        assert sells[0]["price"] == pytest.approx(842.0)
        assert sells[0]["quantity"] == 1000

    def test_stop_loss_exit_reason_recorded(self, tmp_path):
        """alert_type=stop_loss → exit_reason=stop_loss。"""
        from monitor_agent import AlertWorker
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        q = queue.Queue()
        worker = AlertWorker(
            q, db_path=db_path, telegram_chat_id=None,
            auto_execute=True, api=MagicMock(),
            watchlist=self._watchlist(),
        )
        q.put(self._alert(alert_type="stop_loss"))
        q.put(None)

        with patch("monitor_agent.force_stop_loss", return_value=True), \
                patch("notifier.notify_price_alert"):
            worker.run()

        rows = load_daily_trades(date.today(), db_path)
        sells = [r for r in rows if r["action"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["exit_reason"] == "stop_loss"

    def test_pnl_computed_from_entry_price(self, tmp_path):
        """有 entry_price → pnl = (current - entry) × quantity × lot multiplier。

        common 的 quantity 是「張」（1 張 = 1000 股）：pnl 必須乘 1000。
        這裡持倉 1 張 @820 → 842 出場，pnl = 22 × 1 × 1000 = 22000。
        """
        from monitor_agent import AlertWorker
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        q = queue.Queue()
        worker = AlertWorker(
            q, db_path=db_path, telegram_chat_id=None,
            auto_execute=True, api=MagicMock(),
            watchlist=self._watchlist(entry_price=820.0, quantity=1),
        )
        q.put(self._alert(current_price=842.0))
        q.put(None)

        with patch("monitor_agent.force_stop_loss", return_value=True), \
                patch("notifier.notify_price_alert"):
            worker.run()

        rows = load_daily_trades(date.today(), db_path)
        sells = [r for r in rows if r["action"] == "sell"]
        # (842 - 820) * 1(張) * 1000(股/張) = 22000
        assert sells[0]["pnl"] == pytest.approx(22000.0)

    def test_pnl_none_when_no_entry_price(self, tmp_path):
        """無 entry_price → pnl=None。"""
        from monitor_agent import AlertWorker
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        wl = [dict(code="2330", name="台積電", quantity=1000, lot_type="common")]
        q = queue.Queue()
        worker = AlertWorker(
            q, db_path=db_path, telegram_chat_id=None,
            auto_execute=True, api=MagicMock(), watchlist=wl,
        )
        q.put(self._alert())
        q.put(None)

        with patch("monitor_agent.force_stop_loss", return_value=True), \
                patch("notifier.notify_price_alert"):
            worker.run()

        rows = load_daily_trades(date.today(), db_path)
        sells = [r for r in rows if r["action"] == "sell"]
        assert sells[0]["pnl"] is None

    def test_force_stop_loss_failure_writes_no_sell(self, tmp_path):
        """force_stop_loss 回 False → 不寫賣出記錄。"""
        from monitor_agent import AlertWorker
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        q = queue.Queue()
        worker = AlertWorker(
            q, db_path=db_path, telegram_chat_id=None,
            auto_execute=True, api=MagicMock(),
            watchlist=self._watchlist(),
        )
        q.put(self._alert())
        q.put(None)

        with patch("monitor_agent.force_stop_loss", return_value=False), \
                patch("notifier.notify_price_alert"):
            worker.run()

        rows = load_daily_trades(date.today(), db_path)
        sells = [r for r in rows if r["action"] == "sell"]
        assert len(sells) == 0

    def test_no_auto_execute_writes_no_sell(self, tmp_path):
        """auto_execute=False → 不下單也不寫賣出記錄。"""
        from monitor_agent import AlertWorker
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        q = queue.Queue()
        worker = AlertWorker(
            q, db_path=db_path, telegram_chat_id=None,
            auto_execute=False, api=MagicMock(),
            watchlist=self._watchlist(),
        )
        q.put(self._alert())
        q.put(None)

        with patch("monitor_agent.force_stop_loss", return_value=True) as fsl, \
                patch("notifier.notify_price_alert"):
            worker.run()

        fsl.assert_not_called()
        rows = load_daily_trades(date.today(), db_path)
        assert [r for r in rows if r["action"] == "sell"] == []

    def test_db_failure_does_not_break_thread(self, tmp_path):
        """save_daily_trade 拋例外 → worker 不 raise，仍處理完 poison pill。"""
        from monitor_agent import AlertWorker
        from research_db import init_db
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        q = queue.Queue()
        worker = AlertWorker(
            q, db_path=db_path, telegram_chat_id=None,
            auto_execute=True, api=MagicMock(),
            watchlist=self._watchlist(),
        )
        q.put(self._alert())
        q.put(None)

        with patch("monitor_agent.force_stop_loss", return_value=True), \
                patch("notifier.notify_price_alert"), \
                patch("monitor_agent.save_daily_trade",
                      side_effect=Exception("db down")):
            worker.run()  # must return without raising
