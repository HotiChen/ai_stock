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

        alerts = load_pending_alerts(db_path)
        assert len(alerts) == 1
        assert alerts[0]["code"] == "2330"

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

        with patch("monitor_agent.send_telegram") as mock_send:
            q = queue.Queue()
            worker = AlertWorker(q, db_path=db_path, telegram_chat_id=None)
            q.put(self._make_alert())
            q.put(None)
            worker.run()
            assert not mock_send.called

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

        alerts = load_pending_alerts(db_path)
        assert len(alerts) == 2

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
    def test_connection_failure_does_not_raise(self, mock_connect, tmp_path):
        from monitor_agent import MonitorAgent
        mock_connect.return_value = None  # login failed
        agent = MonitorAgent(
            api_key="bad", secret_key="bad", simulation=True,
            db_path=str(tmp_path / "test.db"), telegram_chat_id=None,
        )
        agent.start()  # should not raise
        agent.stop()
