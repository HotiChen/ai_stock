from datetime import date

import pytest

from strategies import (
    calc_position_pnl,
    check_dip_buy,
    should_periodic_buy,
    trailing_stop_triggered,
)


class TestTrailingStop:
    def test_not_triggered_when_still_rising(self):
        assert not trailing_stop_triggered(
            entry=100, current=110, peak=110, stop_pct=5
        )

    def test_triggered_when_dropped_from_peak(self):
        # peak 120, current 113 → drop 5.8% → triggered at 5%
        assert trailing_stop_triggered(
            entry=100, current=113, peak=120, stop_pct=5
        )

    def test_not_triggered_within_stop_range(self):
        # peak 120, current 115 → drop 4.2% → not triggered at 5%
        assert not trailing_stop_triggered(
            entry=100, current=115, peak=120, stop_pct=5
        )

    def test_triggered_exactly_at_stop(self):
        # peak 120, current 114 → drop exactly 5%
        assert trailing_stop_triggered(
            entry=100, current=114, peak=120, stop_pct=5
        )

    def test_peak_below_entry_not_triggered(self):
        # losing trade, no trailing stop
        assert not trailing_stop_triggered(
            entry=100, current=90, peak=100, stop_pct=5
        )

    def test_zero_stop_pct_always_triggered_when_any_drop(self):
        assert trailing_stop_triggered(
            entry=100, current=119, peak=120, stop_pct=0
        )


class TestDipBuy:
    def test_buy_when_at_target(self):
        assert check_dip_buy(current=90, target=90)

    def test_buy_when_below_target(self):
        assert check_dip_buy(current=85, target=90)

    def test_no_buy_when_above_target(self):
        assert not check_dip_buy(current=95, target=90)

    def test_buy_with_threshold(self):
        # target 90, threshold 2% → buy when current <= 90 * (1 + 0.02) = 91.8
        assert check_dip_buy(current=91, target=90, threshold_pct=2)

    def test_no_buy_above_threshold(self):
        assert not check_dip_buy(current=93, target=90, threshold_pct=2)


class TestPeriodicBuy:
    def test_buy_on_interval(self):
        assert should_periodic_buy(
            last_buy=date(2024, 1, 1),
            today=date(2024, 2, 1),
            interval_days=30,
        )

    def test_no_buy_before_interval(self):
        assert not should_periodic_buy(
            last_buy=date(2024, 1, 1),
            today=date(2024, 1, 20),
            interval_days=30,
        )

    def test_buy_when_overdue(self):
        assert should_periodic_buy(
            last_buy=date(2024, 1, 1),
            today=date(2024, 3, 1),
            interval_days=30,
        )

    def test_no_buy_same_day(self):
        assert not should_periodic_buy(
            last_buy=date(2024, 1, 1),
            today=date(2024, 1, 1),
            interval_days=30,
        )


class TestCalcPositionPnl:
    def test_profit(self):
        result = calc_position_pnl(cost=100, current=120, quantity=10)
        assert result["pnl_amount"] == pytest.approx(200.0)
        assert result["pnl_pct"] == pytest.approx(20.0)

    def test_loss(self):
        result = calc_position_pnl(cost=100, current=80, quantity=10)
        assert result["pnl_amount"] == pytest.approx(-200.0)
        assert result["pnl_pct"] == pytest.approx(-20.0)

    def test_breakeven(self):
        result = calc_position_pnl(cost=100, current=100, quantity=10)
        assert result["pnl_amount"] == pytest.approx(0.0)
        assert result["pnl_pct"] == pytest.approx(0.0)

    def test_fractional_shares(self):
        result = calc_position_pnl(cost=200, current=210, quantity=5)
        assert result["pnl_amount"] == pytest.approx(50.0)

    def test_result_has_required_keys(self):
        result = calc_position_pnl(cost=100, current=110, quantity=1)
        assert "pnl_amount" in result
        assert "pnl_pct" in result
        assert "market_value" in result
        assert "cost_value" in result
