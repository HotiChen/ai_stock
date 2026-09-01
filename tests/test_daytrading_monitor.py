"""tests/test_daytrading_monitor.py — TDD tests for daytrading_monitor.py (PR-5/PR-6)"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, time as dtime
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


def _active_pos(code="2330", name="台積電", entry_price=100.0,
                peak_price=102.0, quantity=1, lot_type="common"):
    """建立已進場的 active 持倉（用於追蹤停利測試）。"""
    from daytrading_monitor import DaytradingPosition
    return DaytradingPosition(
        code=code, name=name,
        entry_low=None, entry_high=None,
        target_price=None, stop_loss=None,
        dt_score=7,
        status="active",
        entry_price=entry_price,
        peak_price=peak_price,
        quantity=quantity,
        lot_type=lot_type,
    )


def _cfg(
    stop_loss_pct=3.0,
    take_profit_pct=9.0,
    trailing_start_pct=2.0,
    trailing_gap_pct=0.5,
    force_close_time="13:00",
    budget_per_stock=30000.0,
    require_manual_confirm=True,
):
    from daytrading_config import DaytradingConfig
    return DaytradingConfig(
        budget_per_stock=budget_per_stock,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_start_pct=trailing_start_pct,
        trailing_gap_pct=trailing_gap_pct,
        force_close_time=force_close_time,
        require_manual_confirm=require_manual_confirm,
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

    def test_shioaji_failure_returns_none(self):
        """yfinance 備援已移除：Shioaji 取不到就是 None，不再猜價格。"""
        from daytrading_monitor import fetch_current_price
        api = MagicMock()
        api.Contracts.Stocks.get.side_effect = Exception("contracts not ready")
        assert fetch_current_price("2330", api=api) is None

    def test_zero_close_treated_as_no_quote(self):
        """★ 報價 session 未暖機時 snapshot.close 是 0——那是「沒有報價」，
        不是「股價 0 元」。回 0.0 會讓下游算出完全錯誤的停損與部位。"""
        from daytrading_monitor import fetch_current_price
        snap = MagicMock()
        snap.close = 0
        api = MagicMock()
        api.Contracts.Stocks.get.return_value = MagicMock()
        api.snapshots.return_value = [snap]
        assert fetch_current_price("2330", api=api) is None


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


# ── TrailingStopResult dataclass ──────────────────────────────────────────────

class TestTrailingStopResult:
    def test_fields_accessible(self):
        from daytrading_monitor import TrailingStopResult
        r = TrailingStopResult(
            should_sell=True, reason="stop_loss",
            message="停損觸發", trigger_price=97.0,
        )
        assert r.should_sell is True
        assert r.reason == "stop_loss"
        assert r.trigger_price == 97.0

    def test_no_sell_result(self):
        from daytrading_monitor import TrailingStopResult
        r = TrailingStopResult(
            should_sell=False, reason="", message="", trigger_price=101.0,
        )
        assert r.should_sell is False


# ── check_trailing_stop ───────────────────────────────────────────────────────

class TestCheckTrailingStop:
    """
    規則測試（優先順序）：
      1. 跌幅 >= stop_loss_pct        → stop_loss
      2. 漲幅 >= take_profit_pct      → take_profit_ceiling
      3. 時間 >= force_close_time     → force_close
      4. 峰值漲幅 >= trailing_start
         且峰值回落 >= trailing_stop  → trailing_stop
    """

    def _check(self, entry, current, peak, cfg=None, t=None):
        from daytrading_monitor import check_trailing_stop
        cfg = cfg or _cfg()
        now = datetime(2026, 5, 18, t[0], t[1]) if t else datetime(2026, 5, 18, 10, 0)
        return check_trailing_stop(entry, current, peak, cfg, current_time=now)

    # ── stop loss ─────────────────────────────────────────────────────────────
    def test_stop_loss_exact_threshold(self):
        r = self._check(100.0, 97.0, 100.0)   # -3.0% exactly
        assert r.should_sell is True
        assert r.reason == "stop_loss"

    def test_stop_loss_below_threshold(self):
        r = self._check(100.0, 96.5, 100.0)   # -3.5%
        assert r.should_sell is True
        assert r.reason == "stop_loss"

    def test_no_stop_loss_just_above(self):
        r = self._check(100.0, 97.1, 100.0)   # -2.9%
        assert r.should_sell is False

    # ── take profit ceiling ───────────────────────────────────────────────────
    def test_take_profit_ceiling_exact(self):
        r = self._check(100.0, 109.0, 109.0)  # +9.0%
        assert r.should_sell is True
        assert r.reason == "take_profit_ceiling"

    def test_take_profit_ceiling_above(self):
        r = self._check(100.0, 110.0, 110.0)  # +10%
        assert r.should_sell is True
        assert r.reason == "take_profit_ceiling"

    def test_no_take_profit_just_below(self):
        r = self._check(100.0, 108.9, 108.9)  # +8.9%
        assert r.should_sell is False

    # ── force close ───────────────────────────────────────────────────────────
    def test_force_close_exact_time(self):
        r = self._check(100.0, 101.0, 102.0, t=(13, 0))   # 13:00
        assert r.should_sell is True
        assert r.reason == "force_close"

    def test_force_close_past_time(self):
        r = self._check(100.0, 101.0, 102.0, t=(13, 15))  # 13:15
        assert r.should_sell is True
        assert r.reason == "force_close"

    def test_no_force_close_before_time(self):
        # At 12:59, force_close reason should NOT fire;
        # use a price that also doesn't trigger trailing stop
        # (peak=100, no trailing threshold reached, no stop loss, no ceiling)
        r = self._check(100.0, 100.5, 100.5, t=(12, 59))
        assert r.reason != "force_close"
        assert r.should_sell is False

    def test_force_close_custom_time(self):
        cfg = _cfg(force_close_time="12:30")
        r = self._check(100.0, 101.0, 102.0, cfg=cfg, t=(12, 30))
        assert r.should_sell is True
        assert r.reason == "force_close"

    # ── trailing stop ─────────────────────────────────────────────────────────
    def test_trailing_stop_activated_after_2pct_gain_then_drop(self):
        # peak = +4% → trailing activated, current drops to +3.4% (peak drop = 0.577%)
        entry, peak = 100.0, 104.0
        current = 103.4   # (103.4-104)/104 = -0.577% drop from peak
        r = self._check(entry, current, peak)
        assert r.should_sell is True
        assert r.reason == "trailing_stop"

    def test_trailing_stop_not_activated_below_start_threshold(self):
        # peak gain = 1.5% < trailing_start_pct=2.0% → trailing NOT activated
        entry, peak = 100.0, 101.5
        current = 101.0   # drop from peak = 0.494%
        r = self._check(entry, current, peak)
        assert r.should_sell is False

    def test_trailing_stop_peak_not_dropped_enough(self):
        # peak gain = 4%, current = 3.6% → peak drop = (3.6-4)/104 = -0.385% < 0.5%
        entry, peak = 100.0, 104.0
        current = 103.6   # drop = 0.385% < trailing_stop_pct=0.5%
        r = self._check(entry, current, peak)
        assert r.should_sell is False

    def test_trailing_stop_exact_threshold(self):
        # peak = +2%, drop exactly 0.5% from peak → sell
        entry, peak = 100.0, 102.0
        current = 102.0 * (1 - 0.005)  # exactly -0.5% from peak
        r = self._check(entry, current, peak)
        assert r.should_sell is True
        assert r.reason == "trailing_stop"

    def test_user_example_4pct_peak_to_35pct(self):
        """用戶範例：漲到4%後，從峰值回落超過0.5% → 追蹤停利觸發
        peak=104, 0.5%回落點 = 104*0.995 = 103.48；使用 103.47 避免浮點邊界問題。
        """
        entry = 100.0
        peak  = 104.0    # +4%
        current = 103.47  # (103.47-104)/104 = -0.5096% → 超過 0.5% 回落
        r = self._check(entry, current, peak)
        assert r.should_sell is True
        assert r.reason == "trailing_stop"

    # ── priority order ────────────────────────────────────────────────────────
    def test_stop_loss_beats_trailing(self):
        # gain=-3.5% so stop loss fires, not trailing (peak already>2%)
        entry, peak = 100.0, 103.0
        current = 96.5    # -3.5%
        r = self._check(entry, current, peak)
        assert r.reason == "stop_loss"

    def test_take_profit_ceiling_beats_force_close(self):
        # both gain>=9% AND time>=13:00, take_profit_ceiling should win
        entry, peak = 100.0, 110.0
        current = 110.0
        r = self._check(entry, current, peak, t=(13, 5))
        assert r.reason == "take_profit_ceiling"

    # ── message content ───────────────────────────────────────────────────────
    def test_stop_loss_message_contains_pct(self):
        r = self._check(100.0, 97.0, 100.0)
        assert "停損" in r.message

    def test_trailing_stop_message_contains_pct(self):
        r = self._check(100.0, 103.47, 104.0)  # 103.47 → >0.5% drop from 104
        assert "追蹤停利" in r.message or "高點" in r.message

    def test_no_sell_message_empty(self):
        r = self._check(100.0, 101.5, 102.0)  # gain=1.5%, peak_gain=2%
        # peak gain = 2%, drop from peak = (101.5-102)/102 = -0.49% < 0.5%
        assert r.message == ""


# ── update_peak_price ─────────────────────────────────────────────────────────

class TestUpdatePeakPrice:
    def test_updates_when_higher(self):
        from daytrading_monitor import update_peak_price
        pos = _active_pos(peak_price=100.0)
        changed = update_peak_price(pos, 101.0)
        assert changed is True
        assert pos.peak_price == 101.0

    def test_no_update_when_lower(self):
        from daytrading_monitor import update_peak_price
        pos = _active_pos(peak_price=103.0)
        changed = update_peak_price(pos, 101.0)
        assert changed is False
        assert pos.peak_price == 103.0

    def test_no_update_when_equal(self):
        from daytrading_monitor import update_peak_price
        pos = _active_pos(peak_price=102.0)
        changed = update_peak_price(pos, 102.0)
        assert changed is False

    def test_initializes_when_none(self):
        from daytrading_monitor import update_peak_price
        pos = _active_pos(peak_price=None)
        changed = update_peak_price(pos, 101.5)
        assert changed is True
        assert pos.peak_price == 101.5


# ── mark_position_entered ─────────────────────────────────────────────────────

class TestMarkPositionEntered:
    def test_sets_active_status_and_entry_fields(self):
        from daytrading_monitor import mark_position_entered
        pos = _pos()
        mark_position_entered(pos, entry_price=100.5, quantity=2, lot_type="common")
        assert pos.status == "active"
        assert pos.entry_price == 100.5
        assert pos.peak_price == 100.5   # peak initialised to entry
        assert pos.quantity == 2
        assert pos.lot_type == "common"

    def test_intraday_odd_lot(self):
        from daytrading_monitor import mark_position_entered
        pos = _pos()
        mark_position_entered(pos, entry_price=50.0, quantity=500, lot_type="intraday_odd")
        assert pos.lot_type == "intraday_odd"
        assert pos.quantity == 500


# ── check_position_alerts (active mode) ──────────────────────────────────────

class TestCheckPositionAlertsActive:
    def _check_active(self, current_price, entry_price=100.0, peak_price=102.0,
                      cfg=None, t=(10, 0)):
        from daytrading_monitor import check_position_alerts
        pos = _active_pos(entry_price=entry_price, peak_price=peak_price)
        cfg = cfg or _cfg()
        with patch("daytrading_monitor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 18, t[0], t[1])
            # check_trailing_stop uses its own datetime.now, not patched here
            # so we pass current_time manually by patching inside check_trailing_stop
        return check_position_alerts(pos, current_price, config=cfg)

    def test_active_stop_loss_sell_required(self):
        from daytrading_monitor import check_position_alerts
        pos = _active_pos(entry_price=100.0, peak_price=100.0)
        cfg = _cfg()
        t = datetime(2026, 5, 18, 10, 0)
        with patch("daytrading_monitor.check_trailing_stop") as mock_ct:
            from daytrading_monitor import TrailingStopResult
            mock_ct.return_value = TrailingStopResult(
                should_sell=True, reason="stop_loss",
                message="停損觸發", trigger_price=97.0,
            )
            alerts = check_position_alerts(pos, 97.0, config=cfg)
        assert len(alerts) == 1
        assert alerts[0].sell_required is True
        assert alerts[0].alert_type == "stop_loss"

    def test_active_no_signal_returns_empty(self):
        from daytrading_monitor import check_position_alerts
        pos = _active_pos(entry_price=100.0, peak_price=101.0)
        cfg = _cfg()
        with patch("daytrading_monitor.check_trailing_stop") as mock_ct:
            from daytrading_monitor import TrailingStopResult
            mock_ct.return_value = TrailingStopResult(
                should_sell=False, reason="", message="", trigger_price=101.5,
            )
            alerts = check_position_alerts(pos, 101.5, config=cfg)
        assert len(alerts) == 0

    def test_active_sell_refires_for_retry(self):
        """Task 3：賣單訊號不再被 alerts_sent 去重——出場未成功前，每輪都要能再次
        觸發賣單以支援重試（先前語意為「已發過就靜音」，會讓失敗的出場卡住）。"""
        from daytrading_monitor import check_position_alerts, TrailingStopResult
        pos = _active_pos(entry_price=100.0, peak_price=104.0)
        pos.alerts_sent = ["trailing_stop"]   # 先前已觸發過
        cfg = _cfg()
        with patch("daytrading_monitor.check_trailing_stop") as mock_ct:
            mock_ct.return_value = TrailingStopResult(
                should_sell=True, reason="trailing_stop",
                message="追蹤停利", trigger_price=103.5,
            )
            alerts = check_position_alerts(pos, 103.5, config=cfg)
        assert len(alerts) == 1               # 仍再次觸發（供重試）
        assert alerts[0].sell_required is True

    def test_watching_mode_ignores_config_none(self):
        # config=None → falls through to watching logic (no crash)
        pos = _pos()
        from daytrading_monitor import check_position_alerts
        alerts = check_position_alerts(pos, 100.0, config=None)
        assert isinstance(alerts, list)


# ── Persistence: new fields ───────────────────────────────────────────────────

class TestPersistenceNewFields:
    def test_roundtrip_active_position(self):
        from daytrading_monitor import (
            save_daytrading_positions, load_daytrading_positions,
            DaytradingPosition,
        )
        pos = DaytradingPosition(
            code="2330", name="台積電",
            entry_low=None, entry_high=None,
            target_price=None, stop_loss=None,
            dt_score=7,
            status="active",
            entry_price=100.5,
            peak_price=103.2,
            quantity=2,
            lot_type="common",
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        save_daytrading_positions([pos], path=path)
        loaded = load_daytrading_positions(path=path)
        assert len(loaded) == 1
        lp = loaded[0]
        assert lp.status == "active"
        assert lp.entry_price == 100.5
        assert lp.peak_price == 103.2
        assert lp.quantity == 2
        assert lp.lot_type == "common"

    def test_backward_compat_missing_fields(self):
        """JSON 不含新欄位時，應使用預設值（向下相容）。"""
        from daytrading_monitor import load_daytrading_positions
        old_format = [
            {
                "code": "2330", "name": "台積電",
                "entry_low": 99.0, "entry_high": 101.0,
                "target_price": 105.0, "stop_loss": 97.0,
                "dt_score": 7, "status": "watching", "alerts_sent": [],
            }
        ]
        import json as _json
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
        ) as f:
            _json.dump(old_format, f)
            path = f.name
        loaded = load_daytrading_positions(path=path)
        assert len(loaded) == 1
        p = loaded[0]
        assert p.entry_price is None
        assert p.peak_price is None
        assert p.quantity == 0
        assert p.lot_type == "common"


# ── DaytradingAlert sell_required field ──────────────────────────────────────

class TestDaytradingAlertSellRequired:
    def test_default_sell_required_false(self):
        from daytrading_monitor import DaytradingAlert
        a = DaytradingAlert(
            code="2330", name="台積電",
            alert_type="entry", price=100.0,
            message="進場區間", time=datetime.now(),
        )
        assert a.sell_required is False

    def test_sell_required_true(self):
        from daytrading_monitor import DaytradingAlert
        a = DaytradingAlert(
            code="2330", name="台積電",
            alert_type="trailing_stop", price=103.5,
            message="追蹤停利觸發", time=datetime.now(),
            sell_required=True,
        )
        assert a.sell_required is True


# ── format_alerts_message with sell_required ─────────────────────────────────

class TestFormatAlertsMessageSellRequired:
    def _sell_alert(self, alert_type="trailing_stop"):
        from daytrading_monitor import DaytradingAlert
        return DaytradingAlert(
            code="2330", name="台積電",
            alert_type=alert_type, price=103.5,
            message="追蹤停利觸發", time=datetime(2026, 5, 18, 10, 30),
            sell_required=True,
        )

    def test_sell_alert_contains_sell_tag(self):
        from daytrading_monitor import format_alerts_message
        msg = format_alerts_message([self._sell_alert()])
        assert "賣出" in msg or "出場" in msg

    def test_force_close_label(self):
        from daytrading_monitor import format_alerts_message
        msg = format_alerts_message([self._sell_alert("force_close")])
        assert "強制平倉" in msg or "平倉" in msg

    def test_take_profit_ceiling_label(self):
        from daytrading_monitor import format_alerts_message
        msg = format_alerts_message([self._sell_alert("take_profit_ceiling")])
        assert "天花板" in msg or "停利" in msg
