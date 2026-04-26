from datetime import date

import pytest

from dashboard import DashboardData, build_dashboard, get_portfolio_summary
from strategies import calc_position_pnl
from trades import TradeAction, TradeRecord


def make_trade(pnl: float, action: TradeAction = TradeAction.SELL) -> TradeRecord:
    return TradeRecord("2330", action, 1000, 100.0, pnl, date(2024, 1, 1))


def make_position(cost: float, current: float, qty: int = 1000) -> dict:
    return {"stock_code": "2330", "cost": cost, "current": current, "quantity": qty}


class TestGetPortfolioSummary:
    def test_basic_profit(self):
        positions = [make_position(100, 120)]
        result = get_portfolio_summary(positions)
        assert result["total_pnl"] == pytest.approx(20_000)
        assert result["total_cost"] == pytest.approx(100_000)
        assert result["total_market_value"] == pytest.approx(120_000)

    def test_mixed_positions(self):
        positions = [make_position(100, 120), make_position(200, 180)]
        result = get_portfolio_summary(positions)
        assert result["total_pnl"] == pytest.approx(20_000 - 20_000)

    def test_empty_positions(self):
        result = get_portfolio_summary([])
        assert result["total_pnl"] == 0
        assert result["position_count"] == 0

    def test_position_count(self):
        positions = [make_position(100, 110), make_position(200, 190)]
        result = get_portfolio_summary(positions)
        assert result["position_count"] == 2

    def test_pnl_pct_correct(self):
        positions = [make_position(100, 110, qty=1000)]
        result = get_portfolio_summary(positions)
        assert result["total_pnl_pct"] == pytest.approx(10.0)

    def test_zero_cost_does_not_crash(self):
        positions = [make_position(0, 100)]
        result = get_portfolio_summary(positions)
        assert result["total_pnl_pct"] == 0.0


class TestBuildDashboard:
    def test_returns_dashboard_data(self):
        result = build_dashboard(
            positions=[make_position(100, 110)],
            trades=[make_trade(500)],
            alert_count=2,
            ai_log_entry={"decision": {"action": "buy", "reason": "RSI低"}, "news_sentiment": "positive"},
            cash=30_000,
        )
        assert isinstance(result, DashboardData)

    def test_no_ai_log_defaults_gracefully(self):
        result = build_dashboard([], [], 0, None, 30_000)
        assert result.latest_ai_action == "—"
        assert result.latest_ai_reason == "尚無記錄"

    def test_active_alerts_count(self):
        result = build_dashboard([], [], 3, None, 30_000)
        assert result.active_alerts == 3

    def test_win_rate_from_trades(self):
        trades = [make_trade(100), make_trade(-50), make_trade(200)]
        result = build_dashboard([], trades, 0, None, 30_000)
        assert result.win_rate == pytest.approx(2 / 3)

    def test_cash_stored(self):
        result = build_dashboard([], [], 0, None, 28_500)
        assert result.cash == pytest.approx(28_500)
