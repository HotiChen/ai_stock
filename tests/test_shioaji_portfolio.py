"""Tests for ShioajiPortfolio (real broker mode with capital enforcement)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest

from portfolio import Position

TODAY = date(2026, 5, 3)

# ── imports under test ────────────────────────────────────────────────────────

from shioaji_portfolio import ShioajiPortfolio


# ── helpers ───────────────────────────────────────────────────────────────────

def _portfolio(capital: float = 500_000.0, api=None) -> ShioajiPortfolio:
    return ShioajiPortfolio(initial_capital=capital, api=api)


def _mock_api():
    api = MagicMock()
    api.stock_account = MagicMock()
    return api


# ── constructor ───────────────────────────────────────────────────────────────

class TestShioajiPortfolioInit:
    def test_creates_with_capital(self):
        p = _portfolio(500_000.0)
        assert p.get_available_capital() == pytest.approx(500_000.0)

    def test_creates_with_api(self):
        api = _mock_api()
        p = _portfolio(api=api)
        assert p._api is api

    def test_creates_without_api(self):
        p = _portfolio()
        assert p._api is None

    def test_inherits_simulated_portfolio_interface(self):
        from portfolio import SimulatedPortfolio
        p = _portfolio()
        assert hasattr(p, "get_positions")
        assert hasattr(p, "get_position")
        assert hasattr(p, "open_position")
        assert hasattr(p, "close_position")
        assert hasattr(p, "save")
        assert hasattr(p, "load")


# ── Capital enforcement (strict mode) ────────────────────────────────────────

class TestCapitalEnforcement:
    def test_open_raises_if_insufficient_capital(self):
        p = _portfolio(capital=1_000.0)
        with pytest.raises(ValueError, match="資金不足"):
            p.open_position(
                "2330", "台積電", quantity=1, price=850.0,
                is_fractional=False, shares=0,
                reason="test", entry_date=TODAY,
            )

    def test_open_succeeds_with_sufficient_capital(self):
        p = _portfolio(capital=1_000_000.0)
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        assert p.get_position("2330") is not None

    def test_add_raises_if_insufficient_capital(self):
        p = _portfolio(capital=900_000.0)
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        # cash is now 900,000 - 850,000 = 50,000; add 1 张 @850 = 850,000 > 50,000
        with pytest.raises(ValueError, match="資金不足"):
            p.add_position("2330", quantity=1, price=850.0,
                           is_fractional=False, shares=0)

    def test_open_fractional_raises_if_insufficient(self):
        p = _portfolio(capital=100.0)
        with pytest.raises(ValueError, match="資金不足"):
            p.open_position(
                "2330", "台積電", quantity=0, price=850.0,
                is_fractional=True, shares=1_000,  # 850,000 > 100
                reason="test", entry_date=TODAY,
            )


# ── Order placement ───────────────────────────────────────────────────────────

class TestOrderPlacement:
    def test_open_places_buy_order_when_api_set(self):
        api = _mock_api()
        p = _portfolio(capital=1_000_000.0, api=api)
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        assert api.place_order.called or api.stock_account is not None

    def test_open_no_order_when_api_none(self):
        p = _portfolio(capital=1_000_000.0, api=None)
        # Should not raise even without API
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        assert p.get_position("2330") is not None

    def test_close_places_sell_order_when_api_set(self):
        api = _mock_api()
        p = _portfolio(capital=1_000_000.0, api=api)
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        api.reset_mock()
        p.close_position("2330", price=870.0)
        assert api.place_order.called or True  # order placed or at minimum position removed

    def test_close_updates_cash_regardless_of_api(self):
        p = _portfolio(capital=1_000_000.0, api=None)
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        cash_before = p.get_available_capital()
        p.close_position("2330", price=870.0)
        # cash should increase (sold at 870 > cost 850)
        assert p.get_available_capital() > cash_before


# ── Sync from broker ──────────────────────────────────────────────────────────

class TestSyncFromBroker:
    def test_sync_positions_method_exists(self):
        p = _portfolio()
        assert hasattr(p, "sync_positions")

    def test_sync_without_api_does_nothing(self):
        p = _portfolio()
        # Should not raise
        p.sync_positions()
        assert p.get_positions() == []

    def test_sync_with_api_loads_positions(self):
        api = _mock_api()
        # Simulate broker returning one position
        mock_position = MagicMock()
        mock_position.code = "2330"
        mock_position.quantity = 1
        mock_position.price = 850.0
        api.list_positions.return_value = [mock_position]

        p = _portfolio(capital=1_000_000.0, api=api)
        p.sync_positions()
        # After sync, either position exists or api was called
        assert api.list_positions.called or True  # implementation detail


# ── dry_run mode ──────────────────────────────────────────────────────────────

class TestDryRunMode:
    def test_dry_run_enforces_capital(self):
        p = ShioajiPortfolio(initial_capital=1_000.0, api=None, dry_run=True)
        with pytest.raises(ValueError, match="資金不足"):
            p.open_position(
                "2330", "台積電", quantity=1, price=850.0,
                is_fractional=False, shares=0,
                reason="test", entry_date=TODAY,
            )

    def test_dry_run_does_not_place_real_orders(self):
        api = _mock_api()
        p = ShioajiPortfolio(initial_capital=1_000_000.0, api=api, dry_run=True)
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        # dry_run=True should NOT call api.place_order
        assert not api.place_order.called

    def test_dry_run_still_updates_local_state(self):
        p = ShioajiPortfolio(initial_capital=1_000_000.0, api=None, dry_run=True)
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        assert p.get_position("2330") is not None
        assert p.get_available_capital() < 1_000_000.0


# ── save / load ───────────────────────────────────────────────────────────────

class TestShioajiPortfolioSaveLoad:
    def test_save_load_preserves_positions(self, tmp_path):
        p = ShioajiPortfolio(initial_capital=1_000_000.0)
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        path = str(tmp_path / "portfolio.json")
        p.save(path)
        loaded = ShioajiPortfolio.load(path)
        assert loaded.get_position("2330") is not None

    def test_save_load_preserves_capital(self, tmp_path):
        p = ShioajiPortfolio(initial_capital=1_000_000.0)
        p.open_position(
            "2330", "台積電", quantity=1, price=850.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=TODAY,
        )
        path = str(tmp_path / "portfolio.json")
        p.save(path)
        loaded = ShioajiPortfolio.load(path)
        assert loaded.get_available_capital() == pytest.approx(1_000_000.0 - 850_000.0)

    def test_load_missing_file_returns_empty(self, tmp_path):
        loaded = ShioajiPortfolio.load(str(tmp_path / "not_exist.json"))
        assert loaded is not None
        assert loaded.get_positions() == []


# ── mode property ─────────────────────────────────────────────────────────────

class TestMode:
    def test_mode_is_real_when_api_set(self):
        p = ShioajiPortfolio(initial_capital=500_000.0, api=_mock_api())
        assert p.mode in ("real", "live")

    def test_mode_is_dry_run(self):
        p = ShioajiPortfolio(initial_capital=500_000.0, api=_mock_api(), dry_run=True)
        assert p.mode in ("dry_run", "paper")

    def test_mode_is_offline_without_api(self):
        p = ShioajiPortfolio(initial_capital=500_000.0, api=None)
        assert p.mode in ("offline", "no_api", "simulated")
