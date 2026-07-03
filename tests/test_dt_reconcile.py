"""tests/test_dt_reconcile.py — Task 4: 券商對帳."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


TD = "2026-07-03"


def _pos(code, status="active", quantity=1, lot_type="common", **kw):
    from daytrading_monitor import DaytradingPosition
    base = dict(
        code=code, name=f"N{code}", entry_low=None, entry_high=None,
        target_price=None, stop_loss=None, dt_score=90, status=status,
        entry_price=100.0, peak_price=100.0, quantity=quantity, lot_type=lot_type,
    )
    base.update(kw)
    return DaytradingPosition(**base)


class _FakeApi:
    def __init__(self, positions, raise_on_list=False):
        self._positions = positions
        self._raise = raise_on_list
        self.stock_account = object()

    def list_positions(self, account):
        if self._raise:
            raise RuntimeError("broker down")
        return self._positions


@pytest.fixture
def paths(tmp_path):
    return {"db_path": str(tmp_path / "pos.db"), "json_path": str(tmp_path / "pos.json")}


class TestReconcile:
    def test_broker_fetch_failure_returns_none(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330")], trade_date=TD, **paths)
        api = _FakeApi([], raise_on_list=True)
        assert st.reconcile_with_broker(api, trade_date=TD, **paths) is None

    def test_matched(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330", quantity=1, lot_type="common")],
                          trade_date=TD, **paths)
        api = _FakeApi([SimpleNamespace(code="2330", quantity=1000)])
        report = st.reconcile_with_broker(api, trade_date=TD, **paths)
        assert report["matched"] == ["2330"]
        assert not report["db_only"] and not report["qty_mismatch"]

    def test_db_only(self, paths):
        """DB active 但券商查無。"""
        import dt_position_store as st
        st.save_positions([_pos("2330")], trade_date=TD, **paths)
        api = _FakeApi([])
        report = st.reconcile_with_broker(api, trade_date=TD, **paths)
        assert report["db_only"] == ["2330"]

    def test_qty_mismatch(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330", quantity=2, lot_type="common")],
                          trade_date=TD, **paths)  # 預期 2000 股
        api = _FakeApi([SimpleNamespace(code="2330", quantity=1000)])
        report = st.reconcile_with_broker(api, trade_date=TD, **paths)
        assert report["qty_mismatch"] == [("2330", 2000, 1000)]

    def test_broker_only_logged_not_actionable(self, paths):
        import dt_position_store as st
        st.save_positions([], trade_date=TD, **paths)
        api = _FakeApi([SimpleNamespace(code="1234", quantity=1000)])
        with patch("telegram_bot.send_text") as mock_send:
            report = st.reconcile_with_broker(api, trade_date=TD, chat_id="999", **paths)
        assert report["broker_only"] == ["1234"]
        # broker_only 不是 actionable → 不發告警
        assert not mock_send.called

    def test_sends_alert_on_discrepancy(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330")], trade_date=TD, **paths)
        api = _FakeApi([])  # db_only
        with patch("telegram_bot.send_text") as mock_send:
            st.reconcile_with_broker(api, trade_date=TD, chat_id="999", **paths)
        assert mock_send.called

    def test_ignores_non_active_db_positions(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330", status="watching"),
                           _pos("2454", status="closed")], trade_date=TD, **paths)
        api = _FakeApi([])
        report = st.reconcile_with_broker(api, trade_date=TD, **paths)
        assert report["db_only"] == []
