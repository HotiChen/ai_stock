import json
import tempfile
from datetime import date
from pathlib import Path

import pytest

from trades import TradeAction, TradeRecord, calc_total_pnl, calc_win_rate, load_trades, save_trade


def make_trade(pnl: float, action: TradeAction = TradeAction.SELL) -> TradeRecord:
    return TradeRecord(
        stock_code="2330",
        action=action,
        quantity=10,
        price=100.0,
        pnl=pnl,
        trade_date=date(2024, 1, 1),
    )


class TestCalcTotalPnl:
    def test_all_profit(self):
        trades = [make_trade(100), make_trade(200)]
        assert calc_total_pnl(trades) == pytest.approx(300.0)

    def test_mixed(self):
        trades = [make_trade(300), make_trade(-100)]
        assert calc_total_pnl(trades) == pytest.approx(200.0)

    def test_empty(self):
        assert calc_total_pnl([]) == 0.0

    def test_only_buys_excluded(self):
        trades = [make_trade(0, TradeAction.BUY), make_trade(100, TradeAction.SELL)]
        assert calc_total_pnl(trades) == pytest.approx(100.0)


class TestCalcWinRate:
    def test_all_win(self):
        trades = [make_trade(100), make_trade(200)]
        assert calc_win_rate(trades) == pytest.approx(1.0)

    def test_half_win(self):
        trades = [make_trade(100), make_trade(-100)]
        assert calc_win_rate(trades) == pytest.approx(0.5)

    def test_all_loss(self):
        trades = [make_trade(-100), make_trade(-200)]
        assert calc_win_rate(trades) == pytest.approx(0.0)

    def test_empty_returns_zero(self):
        assert calc_win_rate([]) == 0.0

    def test_buys_excluded(self):
        trades = [make_trade(0, TradeAction.BUY), make_trade(100), make_trade(-50)]
        assert calc_win_rate(trades) == pytest.approx(0.5)


class TestSaveAndLoadTrades:
    def test_save_and_load_roundtrip(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = Path(f.name)

        trade = make_trade(150)
        save_trade(trade, path)
        loaded = load_trades(path)

        assert len(loaded) == 1
        assert loaded[0].stock_code == "2330"
        assert loaded[0].pnl == pytest.approx(150.0)
        path.unlink()

    def test_append_multiple(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = Path(f.name)

        save_trade(make_trade(100), path)
        save_trade(make_trade(200), path)
        loaded = load_trades(path)

        assert len(loaded) == 2
        path.unlink()

    def test_load_nonexistent_returns_empty(self):
        result = load_trades(Path("/tmp/nonexistent_trades_xyz.csv"))
        assert result == []
