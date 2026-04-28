from __future__ import annotations

"""
TDD tests for research_db.py new tables:
  - daily_plans   : 每日選股計畫（validate_plan 後的 approved picks）
  - daily_trades  : 每日成交紀錄（模擬或實單）
  - alerts        : 警報佇列（monitor_agent 發出的警報）
"""

import json
import tempfile
from datetime import date, datetime

import pytest

from research_db import init_db


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test_v2.db")
    init_db(path)
    return path


# ── daily_plans ───────────────────────────────────────────────────────────────

class TestDailyPlans:
    def _make_picks(self):
        return [
            {"code": "2330", "name": "台積電", "budget": 5000.0,
             "sector": "半導體", "signal": "buy", "confidence": 8},
            {"code": "2454", "name": "聯發科", "budget": 3000.0,
             "sector": "半導體", "signal": "buy", "confidence": 7},
        ]

    def test_save_and_load_daily_plan(self, db):
        from research_db import save_daily_plan, load_daily_plan
        save_daily_plan(date(2026, 4, 28), self._make_picks(), db)
        result = load_daily_plan(date(2026, 4, 28), db)
        assert result is not None
        assert len(result) == 2

    def test_loaded_plan_preserves_code(self, db):
        from research_db import save_daily_plan, load_daily_plan
        save_daily_plan(date(2026, 4, 28), self._make_picks(), db)
        result = load_daily_plan(date(2026, 4, 28), db)
        codes = [p["code"] for p in result]
        assert "2330" in codes
        assert "2454" in codes

    def test_loaded_plan_preserves_budget(self, db):
        from research_db import save_daily_plan, load_daily_plan
        save_daily_plan(date(2026, 4, 28), self._make_picks(), db)
        result = load_daily_plan(date(2026, 4, 28), db)
        pick = next(p for p in result if p["code"] == "2330")
        assert pick["budget"] == pytest.approx(5000.0)

    def test_load_nonexistent_date_returns_empty(self, db):
        from research_db import load_daily_plan
        result = load_daily_plan(date(2000, 1, 1), db)
        assert result == []

    def test_overwrite_same_date_keeps_latest(self, db):
        from research_db import save_daily_plan, load_daily_plan
        save_daily_plan(date(2026, 4, 28), self._make_picks(), db)
        new_picks = [{"code": "1301", "name": "台塑", "budget": 2000.0,
                      "sector": "石化", "signal": "buy", "confidence": 6}]
        save_daily_plan(date(2026, 4, 28), new_picks, db)
        result = load_daily_plan(date(2026, 4, 28), db)
        codes = [p["code"] for p in result]
        assert "1301" in codes
        assert "2330" not in codes

    def test_different_dates_independent(self, db):
        from research_db import save_daily_plan, load_daily_plan
        save_daily_plan(date(2026, 4, 28), self._make_picks(), db)
        other = [{"code": "1301", "name": "台塑", "budget": 2000.0,
                  "sector": "石化", "signal": "buy", "confidence": 6}]
        save_daily_plan(date(2026, 4, 29), other, db)
        r28 = load_daily_plan(date(2026, 4, 28), db)
        r29 = load_daily_plan(date(2026, 4, 29), db)
        assert any(p["code"] == "2330" for p in r28)
        assert any(p["code"] == "1301" for p in r29)

    def test_empty_picks_saves_and_loads(self, db):
        from research_db import save_daily_plan, load_daily_plan
        save_daily_plan(date(2026, 4, 28), [], db)
        result = load_daily_plan(date(2026, 4, 28), db)
        assert result == []

    def test_reject_pick_removes_code(self, db):
        from research_db import save_daily_plan, load_daily_plan, reject_pick_from_plan
        save_daily_plan(date(2026, 4, 28), self._make_picks(), db)
        reject_pick_from_plan(date(2026, 4, 28), "2330", db)
        result = load_daily_plan(date(2026, 4, 28), db)
        codes = [p["code"] for p in result]
        assert "2330" not in codes
        assert "2454" in codes

    def test_reject_pick_is_idempotent_on_missing_code(self, db):
        from research_db import save_daily_plan, load_daily_plan, reject_pick_from_plan
        save_daily_plan(date(2026, 4, 28), self._make_picks(), db)
        reject_pick_from_plan(date(2026, 4, 28), "9999", db)
        result = load_daily_plan(date(2026, 4, 28), db)
        assert len(result) == 2

    def test_reject_pick_on_nonexistent_date_does_not_raise(self, db):
        from research_db import reject_pick_from_plan
        reject_pick_from_plan(date(2000, 1, 1), "2330", db)

    def test_reject_all_picks_clears_plan(self, db):
        from research_db import save_daily_plan, load_daily_plan, reject_all_picks_from_plan
        save_daily_plan(date(2026, 4, 28), self._make_picks(), db)
        reject_all_picks_from_plan(date(2026, 4, 28), db)
        result = load_daily_plan(date(2026, 4, 28), db)
        assert result == []

    def test_reject_all_on_nonexistent_date_does_not_raise(self, db):
        from research_db import reject_all_picks_from_plan
        reject_all_picks_from_plan(date(2000, 1, 1), db)


# ── daily_trades ──────────────────────────────────────────────────────────────

class TestDailyTrades:
    def _make_trade(self, **kwargs):
        defaults = dict(
            trade_date=date(2026, 4, 28),
            code="2330",
            name="台積電",
            action="buy",
            quantity=1,
            price=850.0,
            amount=850_000.0,
            pnl=None,
            note="模擬",
        )
        defaults.update(kwargs)
        return defaults

    def test_save_and_load_trade(self, db):
        from research_db import save_daily_trade, load_daily_trades
        save_daily_trade(self._make_trade(), db)
        result = load_daily_trades(date(2026, 4, 28), db)
        assert len(result) == 1

    def test_loaded_trade_has_code(self, db):
        from research_db import save_daily_trade, load_daily_trades
        save_daily_trade(self._make_trade(), db)
        result = load_daily_trades(date(2026, 4, 28), db)
        assert result[0]["code"] == "2330"

    def test_loaded_trade_has_price(self, db):
        from research_db import save_daily_trade, load_daily_trades
        save_daily_trade(self._make_trade(), db)
        result = load_daily_trades(date(2026, 4, 28), db)
        assert result[0]["price"] == pytest.approx(850.0)

    def test_multiple_trades_same_date(self, db):
        from research_db import save_daily_trade, load_daily_trades
        save_daily_trade(self._make_trade(code="2330"), db)
        save_daily_trade(self._make_trade(code="2454", name="聯發科", price=700.0, amount=700_000.0), db)
        result = load_daily_trades(date(2026, 4, 28), db)
        assert len(result) == 2

    def test_load_empty_date_returns_empty(self, db):
        from research_db import load_daily_trades
        result = load_daily_trades(date(2000, 1, 1), db)
        assert result == []

    def test_pnl_none_stored_as_null(self, db):
        from research_db import save_daily_trade, load_daily_trades
        save_daily_trade(self._make_trade(pnl=None), db)
        result = load_daily_trades(date(2026, 4, 28), db)
        assert result[0]["pnl"] is None

    def test_pnl_value_preserved(self, db):
        from research_db import save_daily_trade, load_daily_trades
        save_daily_trade(self._make_trade(pnl=1500.0), db)
        result = load_daily_trades(date(2026, 4, 28), db)
        assert result[0]["pnl"] == pytest.approx(1500.0)

    def test_action_buy_preserved(self, db):
        from research_db import save_daily_trade, load_daily_trades
        save_daily_trade(self._make_trade(action="buy"), db)
        result = load_daily_trades(date(2026, 4, 28), db)
        assert result[0]["action"] == "buy"

    def test_action_sell_preserved(self, db):
        from research_db import save_daily_trade, load_daily_trades
        save_daily_trade(self._make_trade(action="sell"), db)
        result = load_daily_trades(date(2026, 4, 28), db)
        assert result[0]["action"] == "sell"


# ── alerts ────────────────────────────────────────────────────────────────────

class TestAlerts:
    def _make_alert(self, **kwargs):
        defaults = dict(
            code="2330",
            name="台積電",
            alert_type="stop_loss",
            message="觸及停損價 800.0",
            severity="high",
            created_at=datetime(2026, 4, 28, 10, 30),
        )
        defaults.update(kwargs)
        return defaults

    def test_save_and_load_pending_alerts(self, db):
        from research_db import save_alert, load_pending_alerts
        save_alert(self._make_alert(), db)
        result = load_pending_alerts(db)
        assert len(result) == 1

    def test_loaded_alert_has_code(self, db):
        from research_db import save_alert, load_pending_alerts
        save_alert(self._make_alert(), db)
        result = load_pending_alerts(db)
        assert result[0]["code"] == "2330"

    def test_loaded_alert_has_message(self, db):
        from research_db import save_alert, load_pending_alerts
        save_alert(self._make_alert(), db)
        result = load_pending_alerts(db)
        assert "停損" in result[0]["message"]

    def test_loaded_alert_has_severity(self, db):
        from research_db import save_alert, load_pending_alerts
        save_alert(self._make_alert(), db)
        result = load_pending_alerts(db)
        assert result[0]["severity"] == "high"

    def test_mark_alert_sent_removes_from_pending(self, db):
        from research_db import save_alert, load_pending_alerts, mark_alert_sent
        save_alert(self._make_alert(), db)
        alerts = load_pending_alerts(db)
        mark_alert_sent(alerts[0]["id"], db)
        result = load_pending_alerts(db)
        assert len(result) == 0

    def test_multiple_alerts_all_returned(self, db):
        from research_db import save_alert, load_pending_alerts
        save_alert(self._make_alert(code="2330", alert_type="stop_loss"), db)
        save_alert(self._make_alert(code="2454", name="聯發科", alert_type="target_hit"), db)
        result = load_pending_alerts(db)
        assert len(result) == 2

    def test_sent_alert_not_in_pending(self, db):
        from research_db import save_alert, load_pending_alerts, mark_alert_sent
        save_alert(self._make_alert(code="2330"), db)
        save_alert(self._make_alert(code="2454", name="聯發科"), db)
        alerts = load_pending_alerts(db)
        mark_alert_sent(alerts[0]["id"], db)
        remaining = load_pending_alerts(db)
        assert len(remaining) == 1

    def test_alert_has_id_field(self, db):
        from research_db import save_alert, load_pending_alerts
        save_alert(self._make_alert(), db)
        result = load_pending_alerts(db)
        assert "id" in result[0]

    def test_alert_type_preserved(self, db):
        from research_db import save_alert, load_pending_alerts
        save_alert(self._make_alert(alert_type="target_hit"), db)
        result = load_pending_alerts(db)
        assert result[0]["alert_type"] == "target_hit"

    def test_created_at_preserved(self, db):
        from research_db import save_alert, load_pending_alerts
        dt = datetime(2026, 4, 28, 10, 30)
        save_alert(self._make_alert(created_at=dt), db)
        result = load_pending_alerts(db)
        assert result[0]["created_at"] == dt.isoformat()

    def test_save_alert_returns_integer_id(self, db):
        from research_db import save_alert
        alert_id = save_alert(self._make_alert(), db)
        assert isinstance(alert_id, int) and alert_id > 0

    def test_save_alert_ids_are_sequential(self, db):
        from research_db import save_alert
        id1 = save_alert(self._make_alert(code="2330"), db)
        id2 = save_alert(self._make_alert(code="2454", name="聯發科"), db)
        assert id2 > id1

    def test_save_alert_id_matches_loaded_id(self, db):
        from research_db import save_alert, load_pending_alerts
        returned_id = save_alert(self._make_alert(), db)
        loaded = load_pending_alerts(db)
        assert loaded[0]["id"] == returned_id
