"""tests/test_dt_positions_atomic.py — Task 8: save_daytrading_positions 原子寫入."""
from __future__ import annotations

from unittest.mock import patch


def _pos():
    from daytrading_monitor import DaytradingPosition
    return DaytradingPosition(
        code="2330", name="台積電", entry_low=100.0, entry_high=105.0,
        target_price=110.0, stop_loss=95.0, dt_score=90, status="active",
        entry_price=101.0, peak_price=103.0, quantity=1000, lot_type="common",
    )


class TestSaveAtomic:
    def test_uses_atomic_write_json(self, tmp_path):
        import daytrading_monitor as dm
        path = str(tmp_path / "pos.json")
        with patch.object(dm, "atomic_write_json", wraps=dm.atomic_write_json) as spy:
            dm.save_daytrading_positions([_pos()], path=path)
        assert spy.called

    def test_roundtrip_and_no_tmp_leftover(self, tmp_path):
        from daytrading_monitor import save_daytrading_positions, load_daytrading_positions
        path = tmp_path / "pos.json"
        save_daytrading_positions([_pos()], path=str(path))
        loaded = load_daytrading_positions(path=str(path))
        assert len(loaded) == 1
        assert loaded[0].code == "2330"
        assert loaded[0].quantity == 1000
        # no leftover .tmp file
        assert not (tmp_path / "pos.json.tmp").exists()
