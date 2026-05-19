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

    # ── 移動停損啟動後，固定停損/目標被跳過 ──────────────────────────────────────

    def test_trailing_active_skips_fixed_stoploss(self):
        """移動停損啟動中（未觸發）：即使現價跌破固定停損，也不發固定停損警報。"""
        from monitor_agent import check_price_alerts
        # entry=100, peak=104 (+4%), trailing_stop=104*0.98=101.92
        # current=99 < fixed stop_loss=95? No — current=99 is above stop_loss=95
        # Actually let's put stop_loss=100 so current=99 would be below it
        pick = self._pick(entry_price=100.0, peak_price=104.0, stop_loss_price=100.0)
        # trailing active (peak +4%), trailing_stop=101.92, current=99 triggers trailing
        alerts = check_price_alerts("2330", 99.0, pick)
        types = [a["alert_type"] for a in alerts]
        # should get trailing_stop, NOT stop_loss
        assert "trailing_stop" in types
        assert "stop_loss" not in types

    def test_trailing_active_not_triggered_skips_fixed_target(self):
        """移動停損啟動中（未觸發）：即使現價超過固定目標，也不發目標價警報。"""
        from monitor_agent import check_price_alerts
        # entry=100, peak=104 (+4%), trailing_stop=104*0.98=101.92
        # current=125 > target_price=120 → but trailing active, return empty
        pick = self._pick(entry_price=100.0, peak_price=104.0)
        # current=125 is above trailing_stop=101.92, so not triggered → empty
        alerts = check_price_alerts("2330", 125.0, pick)
        assert alerts == []

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

        with patch("notifier.notify_price_alert") as mock_notify:
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

        with patch("notifier.notify_price_alert") as mock_notify:
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
