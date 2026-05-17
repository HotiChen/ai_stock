"""tests/test_daytrading_monitor.py — TDD tests for daytrading_monitor.py (PR-5)"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _pos(code="2330", name="台積電", entry_low=99.0, entry_high=101.0,
         target=105.0, stop=97.0, dt_score=7):
    from daytrading_monitor import DaytradingPosition
    return DaytradingPosition(
        code=code, name=name,
        entry_low=entry_low, entry_high=entry_high,
        target_price=target, stop_loss=stop,
        dt_score=dt_score,
    )


# ── DaytradingPosition dataclass ──────────────────────────────────────────────

class TestDaytradingPositionDataclass:
    def test_fields_accessible(self):
        p = _pos()
        assert p.code == "2330"
        assert p.entry_low == 99.0
        assert p.stop_loss == 97.0

    def test_default_status_is_watching(self):
        p = _pos()
        assert p.status == "watching"

    def test_default_alerts_sent_empty(self):
        p = _pos()
        assert p.alerts_sent == []

    def test_optional_entry_zone(self):
        from daytrading_monitor import DaytradingPosition
        p = DaytradingPosition(
            code="2330", name="台積電",
            entry_low=None, entry_high=None,
            target_price=None, stop_loss=None,
            dt_score=5,
        )
        assert p.entry_low is None


# ── check_position_alerts (pure, rule-based) ──────────────────────────────────

class TestCheckPositionAlerts:
    def _check(self, price, **kw):
        from daytrading_monitor import check_position_alerts
        return check_position_alerts(_pos(**kw), current_price=price)

    def test_price_in_entry_zone_triggers_entry_alert(self):
        alerts = self._check(price=100.0)  # in [99, 101]
        assert any(a.alert_type == "entry" for a in alerts)

    def test_price_above_entry_zone_no_entry_alert(self):
        alerts = self._check(price=102.0)
        assert not any(a.alert_type == "entry" for a in alerts)

    def test_price_at_target_triggers_target_alert(self):
        alerts = self._check(price=105.0)
        assert any(a.alert_type == "target" for a in alerts)

    def test_price_above_target_also_triggers_target(self):
        alerts = self._check(price=106.0)
        assert any(a.alert_type == "target" for a in alerts)

    def test_price_at_stoploss_triggers_stoploss_alert(self):
        alerts = self._check(price=97.0)
        assert any(a.alert_type == "stoploss" for a in alerts)

    def test_price_below_stoploss_triggers_stoploss(self):
        alerts = self._check(price=96.0)
        assert any(a.alert_type == "stoploss" for a in alerts)

    def test_already_alerted_entry_not_repeated(self):
        from daytrading_monitor import DaytradingPosition, check_position_alerts
        pos = _pos()
        pos.alerts_sent = ["entry"]
        alerts = check_position_alerts(pos, current_price=100.0)
        assert not any(a.alert_type == "entry" for a in alerts)

    def test_already_alerted_target_not_repeated(self):
        from daytrading_monitor import check_position_alerts
        pos = _pos()
        pos.alerts_sent = ["target"]
        alerts = check_position_alerts(pos, current_price=105.0)
        assert not any(a.alert_type == "target" for a in alerts)

    def test_no_entry_zone_skips_entry_check(self):
        from daytrading_monitor import DaytradingPosition, check_position_alerts
        pos = DaytradingPosition(
            code="2330", name="台積電",
            entry_low=None, entry_high=None,
            target_price=105.0, stop_loss=97.0,
            dt_score=7,
        )
        alerts = check_position_alerts(pos, current_price=100.0)
        assert not any(a.alert_type == "entry" for a in alerts)

    def test_neutral_price_no_alerts(self):
        alerts = self._check(price=102.0)  # above entry, below target, above stoploss
        assert len(alerts) == 0

    def test_alert_contains_code_name_price(self):
        alerts = self._check(price=100.0)
        a = alerts[0]
        assert a.code == "2330"
        assert a.name == "台積電"
        assert a.price == 100.0


# ── DaytradingAlert dataclass ─────────────────────────────────────────────────

class TestDaytradingAlertDataclass:
    def test_fields_accessible(self):
        from daytrading_monitor import DaytradingAlert
        a = DaytradingAlert(
            code="2330", name="台積電",
            alert_type="entry", price=100.0,
            message="進場區間到了", time=datetime.now(),
        )
        assert a.alert_type == "entry"
        assert a.price == 100.0


# ── fetch_current_price ───────────────────────────────────────────────────────

class TestFetchCurrentPrice:
    def test_shioaji_success(self):
        from daytrading_monitor import fetch_current_price
        api = MagicMock()
        snap = MagicMock()
        snap.close = 101.5
        api.snapshots.return_value = [snap]
        result = fetch_current_price("2330", api=api)
        assert result == pytest.approx(101.5)

    def test_shioaji_failure_falls_back_to_yfinance(self):
        from daytrading_monitor import fetch_current_price
        import sys, pandas as pd
        api = MagicMock()
        api.snapshots.side_effect = Exception("error")
        mock_yf = MagicMock()
        df = pd.DataFrame({"Close": [100.0]},
                          index=pd.date_range("2026-05-17", periods=1))
        mock_yf.Ticker.return_value.history.return_value = df
        with patch.dict(sys.modules, {"yfinance": mock_yf}):
            result = fetch_current_price("2330", api=api)
        assert result == pytest.approx(100.0)

    def test_all_fail_returns_none(self):
        from daytrading_monitor import fetch_current_price
        import sys
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.side_effect = Exception("timeout")
        with patch.dict(sys.modules, {"yfinance": mock_yf}):
            result = fetch_current_price("2330")
        assert result is None


# ── save / load positions ─────────────────────────────────────────────────────

class TestSaveLoadDaytradingPositions:
    def test_roundtrip(self):
        from daytrading_monitor import save_daytrading_positions, load_daytrading_positions
        positions = [_pos("2330", "台積電"), _pos("2454", "聯發科", dt_score=6)]
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        save_daytrading_positions(positions, path=path)
        loaded = load_daytrading_positions(path=path)
        assert len(loaded) == 2
        assert loaded[0].code == "2330"
        assert loaded[1].dt_score == 6

    def test_load_missing_file_returns_empty(self):
        from daytrading_monitor import load_daytrading_positions
        result = load_daytrading_positions(path="/tmp/nonexistent_xyz.json")
        assert result == []

    def test_alerts_sent_persisted(self):
        from daytrading_monitor import save_daytrading_positions, load_daytrading_positions
        pos = _pos()
        pos.alerts_sent = ["entry"]
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        save_daytrading_positions([pos], path=path)
        loaded = load_daytrading_positions(path=path)
        assert "entry" in loaded[0].alerts_sent


# ── run_daytrading_monitor ────────────────────────────────────────────────────

class TestRunDaytradingMonitor:
    def _run(self, positions, prices):
        from daytrading_monitor import run_daytrading_monitor
        with patch("daytrading_monitor.load_daytrading_positions", return_value=positions), \
             patch("daytrading_monitor.save_daytrading_positions"), \
             patch("daytrading_monitor.fetch_current_price", side_effect=lambda code, api=None: prices.get(code)):
            return run_daytrading_monitor()

    def test_returns_list_of_alerts(self):
        alerts = self._run([_pos("2330")], {"2330": 100.0})
        assert isinstance(alerts, list)

    def test_entry_zone_triggers_alert(self):
        alerts = self._run([_pos("2330", entry_low=99.0, entry_high=101.0)], {"2330": 100.0})
        assert any(a.alert_type == "entry" and a.code == "2330" for a in alerts)

    def test_price_none_no_alert(self):
        alerts = self._run([_pos("2330")], {"2330": None})
        assert len(alerts) == 0

    def test_no_positions_returns_empty(self):
        alerts = self._run([], {})
        assert alerts == []

    def test_multiple_stocks_checked(self):
        positions = [_pos("2330"), _pos("2454", entry_low=199.0, entry_high=201.0)]
        alerts = self._run(positions, {"2330": 100.0, "2454": 200.0})
        codes = [a.code for a in alerts]
        assert "2330" in codes
        assert "2454" in codes


# ── format_alerts_message ─────────────────────────────────────────────────────

class TestFormatAlertsMessage:
    def _alert(self, alert_type="entry", code="2330", name="台積電", price=100.0):
        from daytrading_monitor import DaytradingAlert
        return DaytradingAlert(
            code=code, name=name,
            alert_type=alert_type, price=price,
            message="測試", time=datetime(2026, 5, 17, 10, 0),
        )

    def test_contains_code_and_name(self):
        from daytrading_monitor import format_alerts_message
        msg = format_alerts_message([self._alert()])
        assert "2330" in msg and "台積電" in msg

    def test_entry_alert_mentions_entry(self):
        from daytrading_monitor import format_alerts_message
        msg = format_alerts_message([self._alert(alert_type="entry")])
        assert "進場" in msg

    def test_target_alert_mentions_target(self):
        from daytrading_monitor import format_alerts_message
        msg = format_alerts_message([self._alert(alert_type="target", price=105.0)])
        assert "停利" in msg or "目標" in msg

    def test_stoploss_alert_mentions_stoploss(self):
        from daytrading_monitor import format_alerts_message
        msg = format_alerts_message([self._alert(alert_type="stoploss", price=97.0)])
        assert "停損" in msg

    def test_empty_alerts_returns_empty_string(self):
        from daytrading_monitor import format_alerts_message
        assert format_alerts_message([]) == ""
