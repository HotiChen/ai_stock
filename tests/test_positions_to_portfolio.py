"""
tests/test_positions_to_portfolio.py

TDD tests for positions_to_portfolio() function.
Converts fetch_positions() output (Shioaji format) into SimulatedPortfolio.
"""
from __future__ import annotations

import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from portfolio import SimulatedPortfolio, positions_to_portfolio


# ── Fixtures ─────────────────────────────────────────────────────────────────

TSMC = {
    "stock_code": "2330",
    "name": "台積電",
    "cost": 850.0,
    "current": 862.0,
    "quantity": 1,
}

FUBON = {
    "stock_code": "2881",
    "name": "富邦金",
    "cost": 72.0,
    "current": 75.5,
    "quantity": 2,
}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_empty_positions():
    """空 list 回傳 SimulatedPortfolio，get_positions() == []"""
    port = positions_to_portfolio([])
    assert isinstance(port, SimulatedPortfolio)
    assert port.get_positions() == []


def test_single_position_code():
    """1 筆持倉 → get_position("2330") is not None"""
    port = positions_to_portfolio([TSMC])
    assert port.get_position("2330") is not None


def test_single_position_avg_cost():
    """avg_cost == 850.0"""
    port = positions_to_portfolio([TSMC])
    pos = port.get_position("2330")
    assert pos.avg_cost == 850.0


def test_single_position_current_price():
    """current_price == 862.0"""
    port = positions_to_portfolio([TSMC])
    pos = port.get_position("2330")
    assert pos.current_price == 862.0


def test_single_position_quantity():
    """quantity == 1 (張數)"""
    port = positions_to_portfolio([TSMC])
    pos = port.get_position("2330")
    assert pos.quantity == 1


def test_multiple_positions():
    """2 筆持倉 → get_positions() 有 2 筆"""
    port = positions_to_portfolio([TSMC, FUBON])
    assert len(port.get_positions()) == 2


def test_available_cash():
    """available_cash=50000 → get_available_capital() == 50000.0"""
    port = positions_to_portfolio([TSMC], available_cash=50000.0)
    assert port.get_available_capital() == 50000.0


def test_unrealized_pnl_positive():
    """current > cost → unrealized_pnl > 0"""
    port = positions_to_portfolio([TSMC])
    pos = port.get_position("2330")
    # TSMC: current=862 > cost=850, quantity=1 張 = 1000 股
    pnl = (pos.current_price - pos.avg_cost) * pos.total_shares
    assert pnl > 0


def test_name_preserved():
    """name == '台積電'"""
    port = positions_to_portfolio([TSMC])
    pos = port.get_position("2330")
    assert pos.name == "台積電"


def test_zero_current_price_handled():
    """current=0.0 → 不 raise，current_price=0.0"""
    zero_price_pos = {**TSMC, "current": 0.0}
    port = positions_to_portfolio([zero_price_pos])
    pos = port.get_position("2330")
    assert pos.current_price == 0.0
