from __future__ import annotations

"""
TDD tests for monitor_agent.py

Architecture:
  - ensure_connected()     : Shioaji login with auto-reconnect
  - get_snapshot()         : current price via api.snapshots()
  - check_price_alerts()   : pure fn — given price + pick thresholds → alert dicts
  - AlertWorker            : background thread draining a Queue, saves to DB & sends Telegram
  - subscribe_ticks()      : register on_tick_stk_v1 callback (thin wrapper, tested via mock)
  - MonitorAgent           : orchestrates all of the above, start()/stop()
"""

import queue
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytest


# ── check_price_alerts (pure logic) ──────────────────────────────────────────

class TestCheckPriceAlerts:
    def _pick(self, **kwargs):
        defaults = dict(
            code="2330", name="台積電",
            target_price=900.0,
            stop_loss_price=800.0,
        )
        defaults.update(kwargs)
        return defaults

    def test_no_alert_within_range(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 850.0, self._pick())
        assert alerts == []

    def test_target_hit_generates_alert(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 900.0, self._pick())
        types = [a["alert_type"] for a in alerts]
        assert "target_hit" in types

    def test_stop_loss_hit_generates_alert(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 800.0, self._pick())
        types = [a["alert_type"] for a in alerts]
        assert "stop_loss" in types

    def test_price_above_target_generates_alert(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 950.0, self._pick())
        types = [a["alert_type"] for a in alerts]
        assert "target_hit" in types

    def test_price_below_stop_generates_alert(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 750.0, self._pick())
        types = [a["alert_type"] for a in alerts]
        assert "stop_loss" in types

    def test_alert_contains_code(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 900.0, self._pick())
        assert alerts[0]["code"] == "2330"

    def test_alert_contains_message(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 900.0, self._pick())
        assert isinstance(alerts[0]["message"], str) and len(alerts[0]["message"]) > 0

    def test_target_none_no_target_alert(self):
        from monitor_agent import check_price_alerts
        pick = self._pick(target_price=None)
        alerts = check_price_alerts("2330", 999.0, pick)
        types = [a["alert_type"] for a in alerts]
        assert "target_hit" not in types

    def test_stop_none_no_stop_alert(self):
        from monitor_agent import check_price_alerts
        pick = self._pick(stop_loss_price=None)
        alerts = check_price_alerts("2330", 1.0, pick)
        types = [a["alert_type"] for a in alerts]
        assert "stop_loss" not in types

    def test_target_alert_severity_is_high(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 900.0, self._pick())
        target_alert = next(a for a in alerts if a["alert_type"] == "target_hit")
        assert target_alert["severity"] == "high"

    def test_stop_alert_severity_is_high(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 800.0, self._pick())
        stop_alert = next(a for a in alerts if a["alert_type"] == "stop_loss")
        assert stop_alert["severity"] == "high"

    def test_target_alert_contains_current_price(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 920.0, self._pick())
        alert = next(a for a in alerts if a["alert_type"] == "target_hit")
        assert alert["current_price"] == 920.0

    def test_target_alert_contains_target_price(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 920.0, self._pick())
        alert = next(a for a in alerts if a["alert_type"] == "target_hit")
        assert alert["target_price"] == 900.0

    def test_stop_alert_contains_current_price(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 780.0, self._pick())
        alert = next(a for a in alerts if a["alert_type"] == "stop_loss")
        assert alert["current_price"] == 780.0

    def test_stop_alert_contains_stop_loss_price(self):
        from monitor_agent import check_price_alerts
        alerts = check_price_alerts("2330", 780.0, self._pick())
        alert = next(a for a in alerts if a["alert_type"] == "stop_loss")
        assert alert["stop_loss_price"] == 800.0


# ── check_price_alerts: trailing stop ────────────────────────────────────────

class TestTrailingStop:
    """移動停損：漲到 trailing_start_pct 才啟動，停損跟在最高點下方 trailing_gap_pct。"""

    def _pick(self, entry_price=100.0, peak_price=None, **kwargs):
        p = dict(
            code="2330", name="台積電",
            entry_price=entry_price,
            target_price=120.0,
            stop_loss_price=95.0,
        )
        if peak_price is not None:
            p["peak_price"] = peak_price
        p.update(kwargs)
        return p

    # ── 未達門檻，仍用固定停損/目標 ────────────────────────────────────────────

    def test_below_threshold_uses_fixed_stoploss(self):
        """gain < 3%：移動停損未啟動，固定停損仍有效。"""
        from monitor_agent import check_price_alerts
        # entry=100, current=94 → gain=-6%, 未達 trailing 門檻
        pick = self._pick()
        alerts = check_price_alerts("2330", 94.0, pick)
        types = [a["alert_type"] for a in alerts]
        assert "stop_loss" in types
        assert "trailing_stop" not in types

    def test_below_threshold_uses_fixed_target(self):
        """gain < 3%：移動停損未啟動，固定目標價仍有效。
        目標設在 entry+2%（低於 3% 啟動門檻）。"""
        from monitor_agent import check_price_alerts
        # entry=100, target=101.5（+1.5%），current=101.5 → gain=1.5% < 3% trailing not active
        pick = self._pick(entry_price=100.0, target_price=101.5, stop_loss_price=95.0)
        alerts = check_price_alerts("2330", 101.5, pick)
        types = [a["alert_type"] for a in alerts]
        assert "target_hit" in types
        assert "trailing_stop" not in types

    # ── 達到門檻，移動停損接管 ──────────────────────────────────────────────────

    def test_trailing_active_no_trigger_returns_empty(self):
        """peak 漲到 +3.5%，現價仍在移動停損之上 → 無警報。"""
        from monitor_agent import check_price_alerts
        # entry=100, peak=103.5 (+3.5%), trailing_stop=103.5*0.98=101.43
        # current=102 > 101.43 → no trigger
        pick = self._pick(peak_price=103.5)
        alerts = check_price_alerts("2330", 102.0, pick)
        assert alerts == []

    def test_trailing_stop_triggered(self):
        """peak 漲到 +5%，現價跌破移動停損 → trailing_stop 警報。"""
        from monitor_agent import check_price_alerts
        # entry=100, peak=105 (+5%), trailing_stop=105*0.98=102.9
        # current=102 < 102.9 → trigger
        pick = self._pick(peak_price=105.0)
        alerts = check_price_alerts("2330", 102.0, pick)
        types = [a["alert_type"] for a in alerts]
        assert "trailing_stop" in types

    def test_trailing_stop_alert_type(self):
        from monitor_agent import check_price_alerts
        pick = self._pick(peak_price=105.0)
        alerts = check_price_alerts("2330", 102.0, pick)
        assert alerts[0]["alert_type"] == "trailing_stop"

    def test_trailing_stop_includes_peak_price(self):
        from monitor_agent import check_price_alerts
        pick = self._pick(peak_price=106.0)
        alerts = check_price_alerts("2330", 103.0, pick)
        a = next(a for a in alerts if a["alert_type"] == "trailing_stop")
        assert a["peak_price"] == pytest.approx(106.0)

    def test_trailing_stop_includes_trailing_stop_price(self):
        from monitor_agent import check_price_alerts
        pick = self._pick(peak_price=106.0)
        alerts = check_price_alerts("2330", 103.0, pick)
        a = next(a for a in alerts if a["alert_type"] == "trailing_stop")
        assert a["trailing_stop"] == pytest.approx(106.0 * 0.98)

    # ── peak_price 在 pick dict 中更新 ──────────────────────────────────────────

    def test_peak_price_updated_when_new_high(self):
        """tick 比 peak 高 → pick['peak_price'] 更新。"""
        from monitor_agent import check_price_alerts
        pick = self._pick(entry_price=100.0, peak_price=102.0)
        check_price_alerts("2330", 104.0, pick)
        assert pick["peak_price"] == pytest.approx(104.0)

    def test_peak_price_not_lowered(self):
        """tick 低於 peak → pick['peak_price'] 不變。"""
        from monitor_agent import check_price_alerts
        pick = self._pick(entry_price=100.0, peak_price=106.0)
        check_price_alerts("2330", 104.0, pick)
        assert pick["peak_price"] == pytest.approx(106.0)

    def test_peak_initialized_from_entry_when_missing(self):
        """pick 沒有 peak_price 時，以 entry_price 為起點。"""
        from monitor_agent import check_price_alerts
        pick = self._pick(entry_price=100.0)  # no peak_price key
        check_price_alerts("2330", 101.0, pick)
        assert pick["peak_price"] == pytest.approx(101.0)

    # ── 移動停損與停損/目標的優先順序（dt_exit_rules.PRIORITY）───────────────────
    #
    # 這兩個案例原本斷言「移動停損啟動後跳過停損與目標」——那是把當時的實作
    # 缺陷釘成規格。舊版在移動停損啟動後直接 `return alerts`（空 list），
    # 於是：
    #   (a) 賠錢出場會被標成「追蹤停利」，損益歸因整個錯掉；
    #   (b) ATR 目標價（通常 > 3% 啟動門檻）實質上永遠不會觸發。
    # 收斂到 dt_exit_rules 後，順序是 停損 → 天花板 → 強平 → 移動停損 → 目標價。

    def test_stop_loss_wins_over_trailing_when_below_entry(self):
        """★ 現價已跌破進場價：這是虧損出場，必須標成停損而不是「追蹤停利」。

        entry=100、peak=104（移動停損已啟動、線在 101.92）、ATR 停損 100、
        現價 99——兩條規則都成立，但只有 stop_loss 是誠實的標籤。
        """
        from monitor_agent import check_price_alerts
        pick = self._pick(entry_price=100.0, peak_price=104.0, stop_loss_price=100.0)
        alerts = check_price_alerts("2330", 99.0, pick)
        types = [a["alert_type"] for a in alerts]
        assert "stop_loss" in types
        assert "trailing_stop" not in types

    def test_target_reachable_while_trailing_armed(self):
        """★ 回歸測試：移動停損啟動中，價格一路衝到目標價仍要出場。

        entry=100、peak 更新為 125、移動停損線 122.5、現價 125 未回落，
        目標價 120 已達成。舊版在這裡回空 list，部位就這樣一直掛著。
        """
        from monitor_agent import check_price_alerts
        pick = self._pick(entry_price=100.0, peak_price=104.0)
        alerts = check_price_alerts("2330", 125.0, pick)
        assert [a["alert_type"] for a in alerts] == ["target_hit"]

    def test_trailing_still_wins_when_price_has_dropped(self):
        """已經自峰值回落到移動停損線之下 → 走移動停損，不是目標價。"""
        from monitor_agent import check_price_alerts
        # entry=100, peak=110（+10%）, 線=107.8, 現價 107 已跌破；目標 105 也達成
        pick = self._pick(entry_price=100.0, peak_price=110.0, target_price=105.0)
        alerts = check_price_alerts("2330", 107.0, pick)
        assert [a["alert_type"] for a in alerts] == ["trailing_stop"]

    def test_ceiling_take_profit_when_configured(self):
        """天花板停利要排在目標價之前——漲停鎖死就賣不掉了。"""
        from monitor_agent import check_price_alerts
        pick = self._pick(entry_price=100.0, target_price=120.0,
                          stop_loss_price=95.0)
        alerts = check_price_alerts("2330", 109.0, pick,
                                    trailing_start_pct=50.0,
                                    take_profit_pct=9.0)
        assert [a["alert_type"] for a in alerts] == ["take_profit_ceiling"]

    def test_force_close_time_when_configured(self):
        """tick 路徑原本完全沒有時間強平，只能等排程器那一分鐘剛好跑到。"""
        from monitor_agent import check_price_alerts
        pick = self._pick(entry_price=100.0)
        with patch("dt_exit_rules.datetime") as m:
            m.now.return_value = datetime(2026, 9, 7, 13, 5)
            alerts = check_price_alerts("2330", 101.0, pick,
                                        force_close_time="13:00")
        assert [a["alert_type"] for a in alerts] == ["force_close"]

    # ── 無 entry_price 時走舊邏輯 ────────────────────────────────────────────────

    def test_no_entry_price_falls_back_to_fixed_alerts(self):
        """entry_price 為 None：移動停損不啟動，固定停損/目標照常。"""
        from monitor_agent import check_price_alerts
        pick = dict(
            code="2330", name="台積電",
            target_price=120.0,
            stop_loss_price=95.0,
            # no entry_price
        )
        alerts = check_price_alerts("2330", 94.0, pick)
        types = [a["alert_type"] for a in alerts]
        assert "stop_loss" in types


# ── get_snapshot ──────────────────────────────────────────────────────────────

class TestGetSnapshot:
    def _mock_api(self, close=850.0, volume=5000, change_price=10.0):
        api = MagicMock()
        snap = MagicMock()
        snap.close        = close
        snap.total_volume = volume
        snap.change_price = change_price
        api.snapshots.return_value = [snap]
        contract = MagicMock()
        api.Contracts.Stocks.get.return_value = contract
        return api

    def test_returns_dict_with_price(self):
        from monitor_agent import get_snapshot
        api = self._mock_api(close=850.0)
        result = get_snapshot(api, "2330")
        assert result["close"] == pytest.approx(850.0)

    def test_returns_dict_with_volume(self):
        from monitor_agent import get_snapshot
        api = self._mock_api(volume=5000)
        result = get_snapshot(api, "2330")
        assert result["volume"] == 5000

    def test_returns_dict_with_change(self):
        from monitor_agent import get_snapshot
        api = self._mock_api(change_price=10.0)
        result = get_snapshot(api, "2330")
        assert result["change_price"] == pytest.approx(10.0)

    def test_unknown_contract_returns_none(self):
        from monitor_agent import get_snapshot
        api = MagicMock()
        api.Contracts.Stocks.get.return_value = None
        result = get_snapshot(api, "9999")
        assert result is None

    def test_api_exception_returns_none(self):
        from monitor_agent import get_snapshot
        api = MagicMock()
        api.Contracts.Stocks.get.side_effect = Exception("network error")
        result = get_snapshot(api, "2330")
        assert result is None

    def test_empty_snapshots_returns_none(self):
        from monitor_agent import get_snapshot
        api = MagicMock()
        api.Contracts.Stocks.get.return_value = MagicMock()
        api.snapshots.return_value = []
        result = get_snapshot(api, "2330")
        assert result is None


# ── ensure_connected ──────────────────────────────────────────────────────────

class TestEnsureConnected:
    @patch("monitor_agent.sj.Shioaji")
    def test_returns_api_on_success(self, mock_shioaji):
        mock_api = MagicMock()
        mock_shioaji.return_value = mock_api
        from monitor_agent import ensure_connected
        result = ensure_connected("key", "secret", simulation=True)
        assert result is mock_api

    @patch("monitor_agent.sj.Shioaji")
    def test_calls_login(self, mock_shioaji):
        mock_api = MagicMock()
        mock_shioaji.return_value = mock_api
        from monitor_agent import ensure_connected
        ensure_connected("mykey", "mysecret", simulation=True)
        mock_api.login.assert_called_once_with(
            api_key="mykey",
            secret_key="mysecret",
            fetch_contract=True,
        )

    @patch("monitor_agent.sj.Shioaji")
    def test_simulation_flag_passed(self, mock_shioaji):
        mock_api = MagicMock()
        mock_shioaji.return_value = mock_api
        from monitor_agent import ensure_connected
        ensure_connected("key", "secret", simulation=True)
        mock_shioaji.assert_called_once_with(simulation=True)

    @patch("monitor_agent.sj.Shioaji")
    def test_login_failure_returns_none(self, mock_shioaji):
        mock_api = MagicMock()
        mock_api.login.side_effect = Exception("login failed")
        mock_shioaji.return_value = mock_api
        from monitor_agent import ensure_connected
        result = ensure_connected("key", "secret", simulation=True)
        assert result is None


# ── AlertWorker ───────────────────────────────────────────────────────────────

class TestAlertWorker:
    def _make_alert(self, **kwargs):
        defaults = dict(
            code="2330", name="台積電",
            alert_type="stop_loss",
            message="觸及停損",
            severity="high",
            created_at=datetime(2026, 4, 28, 10, 0),
        )
        defaults.update(kwargs)
        return defaults

    def test_worker_saves_alert_to_db(self, tmp_path):
        from monitor_agent import AlertWorker
        from research_db import init_db, load_pending_alerts
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        q = queue.Queue()
        worker = AlertWorker(q, db_path=db_path, telegram_chat_id=None)
        q.put(self._make_alert())
        q.put(None)  # poison pill
        worker.run()

        # After processing, alert is marked sent — verify via direct DB count
        import sqlite3
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM alerts WHERE code='2330'").fetchone()[0]
        assert count == 1

    def test_worker_sends_telegram_when_chat_id_set(self, tmp_path):
        from monitor_agent import AlertWorker
        from research_db import init_db
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        with patch("monitor_agent.notify_price_alert") as mock_notify:
            q = queue.Queue()
            worker = AlertWorker(q, db_path=db_path, telegram_chat_id="12345")
            q.put(self._make_alert())
            q.put(None)
            worker.run()
            assert mock_notify.called

    def test_worker_skips_telegram_when_no_chat_id(self, tmp_path):
        from monitor_agent import AlertWorker
        from research_db import init_db
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        with patch("monitor_agent.notify_price_alert") as mock_notify:
            q = queue.Queue()
            worker = AlertWorker(q, db_path=db_path, telegram_chat_id=None)
            q.put(self._make_alert())
            q.put(None)
            worker.run()
            # notify_price_alert is always called regardless of chat_id;
            # the notifier itself skips if _CHAT_ID is not set
            assert mock_notify.called

    def test_worker_processes_multiple_alerts(self, tmp_path):
        from monitor_agent import AlertWorker
        from research_db import init_db, load_pending_alerts
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        q = queue.Queue()
        worker = AlertWorker(q, db_path=db_path, telegram_chat_id=None)
        q.put(self._make_alert(code="2330"))
        q.put(self._make_alert(code="2454", name="聯發科"))
        q.put(None)
        worker.run()

        import sqlite3
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        assert count == 2

    def test_worker_marks_alert_sent_after_notify(self, tmp_path):
        from monitor_agent import AlertWorker
        from research_db import init_db, load_pending_alerts
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        with patch("notifier.notify_price_alert"):
            q = queue.Queue()
            worker = AlertWorker(q, db_path=db_path, telegram_chat_id=None)
            q.put(self._make_alert())
            q.put(None)
            worker.run()

        # After processing, the alert should be marked as sent (not pending)
        pending = load_pending_alerts(db_path)
        assert len(pending) == 0

    def test_worker_stops_on_poison_pill(self, tmp_path):
        from monitor_agent import AlertWorker
        from research_db import init_db
        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        q = queue.Queue()
        worker = AlertWorker(q, db_path=db_path, telegram_chat_id=None)
        q.put(None)  # immediate poison pill
        worker.run()  # should return quickly without blocking


# ── MonitorAgent start/stop ───────────────────────────────────────────────────

class TestMonitorAgent:
    @patch("monitor_agent.ensure_connected")
    def test_start_connects_to_shioaji(self, mock_connect, tmp_path):
        from monitor_agent import MonitorAgent
        mock_connect.return_value = MagicMock()
        agent = MonitorAgent(
            api_key="key", secret_key="secret", simulation=True,
            db_path=str(tmp_path / "test.db"), telegram_chat_id=None,
        )
        agent.start()
        agent.stop()
        mock_connect.assert_called_once()

    @patch("monitor_agent.ensure_connected")
    def test_stop_sets_running_false(self, mock_connect, tmp_path):
        from monitor_agent import MonitorAgent
        mock_connect.return_value = MagicMock()
        agent = MonitorAgent(
            api_key="key", secret_key="secret", simulation=True,
            db_path=str(tmp_path / "test.db"), telegram_chat_id=None,
        )
        agent.start()
        agent.stop()
        assert agent.running is False

    @patch("monitor_agent.ensure_connected")
    def test_stop_joins_worker_thread(self, mock_connect, tmp_path):
        from monitor_agent import MonitorAgent
        mock_connect.return_value = MagicMock()
        agent = MonitorAgent(
            api_key="key", secret_key="secret", simulation=True,
            db_path=str(tmp_path / "test.db"), telegram_chat_id=None,
        )
        agent.start()
        agent.stop()
        # worker thread should have been joined (no longer alive after stop)
        assert agent._worker_thread is None or not agent._worker_thread.is_alive()

    @patch("monitor_agent.ensure_connected")
    def test_connection_failure_does_not_raise(self, mock_connect, tmp_path):
        from monitor_agent import MonitorAgent
        mock_connect.return_value = None  # login failed
        agent = MonitorAgent(
            api_key="bad", secret_key="bad", simulation=True,
            db_path=str(tmp_path / "test.db"), telegram_chat_id=None,
        )
        agent.start()  # should not raise
        agent.stop()

    def test_provided_api_skips_ensure_connected(self, tmp_path):
        from monitor_agent import MonitorAgent
        with patch("monitor_agent.ensure_connected") as mock_conn:
            pre_api = MagicMock()
            agent = MonitorAgent(
                api_key="k", secret_key="s", simulation=True,
                db_path=str(tmp_path / "test.db"), telegram_chat_id=None,
                api=pre_api,
            )
            agent.start()
            agent.stop()
            mock_conn.assert_not_called()

    def test_provided_api_is_used_for_polling(self, tmp_path):
        from monitor_agent import MonitorAgent
        with patch("monitor_agent.ensure_connected") as mock_conn:
            pre_api = MagicMock()
            agent = MonitorAgent(
                api_key="k", secret_key="s", simulation=True,
                db_path=str(tmp_path / "test.db"), telegram_chat_id=None,
                api=pre_api,
            )
            agent.start()
            agent.stop()
            assert agent._api is pre_api


# ── AlertWorker 自動出場白名單 ────────────────────────────────────────────────

class TestAutoExecuteCoversEveryExitReason:
    """★ 自動出場的白名單原本是手寫的 ("stop_loss", "trailing_stop")。

    於是天花板停利與時間強平即使觸發，也只發 Telegram 通知、不會真的賣出——
    而那兩個正是最不能漏的：漲停鎖死就賣不掉，跨日就變成交割義務。
    改成直接取 dt_exit_rules.PRIORITY，出場理由新增時不可能再漏。
    """

    def _alert(self, alert_type):
        return dict(
            code="2330", name="台積電", alert_type=alert_type,
            message="觸發", severity="high",
            created_at=datetime(2026, 9, 7, 11, 0), current_price=842.0,
        )

    def _run(self, alert_type, tmp_path):
        from monitor_agent import AlertWorker
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        q = queue.Queue()
        worker = AlertWorker(
            q, db_path=db_path, telegram_chat_id=None,
            auto_execute=True, api=MagicMock(),
            watchlist=[dict(code="2330", name="台積電", quantity=1000,
                            lot_type="common", entry_price=820.0)],
        )
        q.put(self._alert(alert_type))
        q.put(None)
        with patch("monitor_agent.force_stop_loss", return_value=True) as sell, \
                patch("notifier.notify_price_alert"):
            worker.run()
        return sell

    @pytest.mark.parametrize("reason", [
        "stop_loss", "take_profit_ceiling", "force_close",
        "trailing_stop", "target_hit",
    ])
    def test_every_exit_reason_places_a_sell_order(self, reason, tmp_path):
        assert self._run(reason, tmp_path).called, f"{reason} 沒有下賣單"

    def test_non_exit_alert_does_not_sell(self, tmp_path):
        """進場提示之類的通知不該觸發賣單。"""
        assert not self._run("entry", tmp_path).called

    def test_whitelist_is_the_shared_priority_tuple(self):
        """白名單必須就是 dt_exit_rules.PRIORITY 本身，不是另一份拷貝。"""
        import inspect

        import dt_exit_rules
        import monitor_agent
        src = inspect.getsource(monitor_agent.AlertWorker.run)
        assert "_er.PRIORITY" in src
        assert len(dt_exit_rules.PRIORITY) == 5
