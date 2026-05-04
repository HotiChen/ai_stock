"""Tests for sim_position_store and sim_settlement modules."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import tempfile
import os

# ---------------------------------------------------------------------------
# sim_position_store tests
# ---------------------------------------------------------------------------

from sim_position_store import SimPosition, save_sim_positions, load_sim_positions, clear_sim_positions


class TestSaveAndLoadPositions:
    def test_save_and_load_positions(self, tmp_path):
        path = str(tmp_path / "positions.json")
        pos = SimPosition(
            code="2330",
            name="台積電",
            entry_price=800.0,
            shares=100,
            quantity=0,
            is_fractional=True,
            plan_type="aggressive",
            entry_date="2026-05-05",
        )
        save_sim_positions([pos], path=path)
        loaded = load_sim_positions(path=path)

        assert len(loaded) == 1
        p = loaded[0]
        assert p.code == "2330"
        assert p.name == "台積電"
        assert p.entry_price == 800.0
        assert p.shares == 100
        assert p.quantity == 0
        assert p.is_fractional is True
        assert p.plan_type == "aggressive"
        assert p.entry_date == "2026-05-05"

    def test_load_empty_returns_empty_list(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        result = load_sim_positions(path=path)
        assert result == []

    def test_clear_positions(self, tmp_path):
        path = str(tmp_path / "positions.json")
        pos = SimPosition(
            code="2454",
            name="聯發科",
            entry_price=1000.0,
            shares=50,
            quantity=0,
            is_fractional=True,
            plan_type="balanced",
            entry_date="2026-05-05",
        )
        save_sim_positions([pos], path=path)
        clear_sim_positions(path=path)
        result = load_sim_positions(path=path)
        assert result == []


# ---------------------------------------------------------------------------
# sim_settlement tests
# ---------------------------------------------------------------------------

from sim_settlement import calc_position_pnl, settle_positions, fetch_closing_prices


class TestCalcPositionPnl:
    def test_calc_pnl_fractional(self):
        pos = SimPosition(
            code="2330",
            name="台積電",
            entry_price=50.0,
            shares=100,
            quantity=0,
            is_fractional=True,
            plan_type="aggressive",
            entry_date="2026-05-05",
        )
        pnl = calc_position_pnl(pos, closing_price=55.0)
        assert pnl == pytest.approx(500.0)

    def test_calc_pnl_whole_lot(self):
        pos = SimPosition(
            code="2330",
            name="台積電",
            entry_price=50.0,
            shares=0,
            quantity=1,
            is_fractional=False,
            plan_type="balanced",
            entry_date="2026-05-05",
        )
        pnl = calc_position_pnl(pos, closing_price=55.0)
        # 1 張 = 1000 股，(55 - 50) * 1 * 1000 = 5000
        assert pnl == pytest.approx(5000.0)

    def test_calc_pnl_loss(self):
        pos = SimPosition(
            code="2330",
            name="台積電",
            entry_price=50.0,
            shares=100,
            quantity=0,
            is_fractional=True,
            plan_type="conservative",
            entry_date="2026-05-05",
        )
        pnl = calc_position_pnl(pos, closing_price=45.0)
        assert pnl == pytest.approx(-500.0)


class TestSettlePositions:
    def _make_pos(self, code, entry_price, shares=100, is_fractional=True, quantity=0):
        return SimPosition(
            code=code,
            name=code,
            entry_price=entry_price,
            shares=shares,
            quantity=quantity,
            is_fractional=is_fractional,
            plan_type="balanced",
            entry_date="2026-05-05",
        )

    def test_settle_positions(self):
        pos1 = self._make_pos("2330", entry_price=50.0, shares=100)  # pnl = +500
        pos2 = self._make_pos("2454", entry_price=100.0, shares=200)  # pnl = -1000 if closing=95
        price_map = {"2330": 55.0, "2454": 95.0}

        result = settle_positions([pos1, pos2], price_map)

        assert "total_pnl" in result
        assert "total_pnl_pct" in result
        assert "per_stock" in result
        # pos1: (55-50)*100 = 500, pos2: (95-100)*200 = -1000 → total = -500
        assert result["total_pnl"] == pytest.approx(-500.0)

    def test_settle_positions_missing_price(self):
        pos1 = self._make_pos("2330", entry_price=50.0, shares=100)
        pos2 = self._make_pos("2454", entry_price=100.0, shares=100)
        price_map = {"2330": 55.0}  # 2454 missing

        result = settle_positions([pos1, pos2], price_map)
        # Only 2330 contributes: (55-50)*100 = 500
        assert result["total_pnl"] == pytest.approx(500.0)
        assert len(result["per_stock"]) == 1


class TestFetchClosingPrices:
    def test_fetch_closing_prices_timeout(self):
        """api.snapshots blocks → timeout → return {}"""
        api = MagicMock()

        def slow_snapshots(*args, **kwargs):
            threading.Event().wait(timeout=30)  # blocks for 30s
            return []

        api.snapshots.side_effect = slow_snapshots

        result = fetch_closing_prices(api, ["2330", "2454"])
        assert result == {}

    def test_fetch_closing_prices_success(self):
        """api.snapshots returns normally → price_map has values"""
        api = MagicMock()

        snapshot_2330 = MagicMock()
        snapshot_2330.code = "2330"
        snapshot_2330.close = 850.0

        snapshot_2454 = MagicMock()
        snapshot_2454.code = "2454"
        snapshot_2454.close = 1200.0

        api.snapshots.return_value = [snapshot_2330, snapshot_2454]

        result = fetch_closing_prices(api, ["2330", "2454"])
        assert result == {"2330": 850.0, "2454": 1200.0}
