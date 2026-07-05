"""tests/test_dt_backtest.py — TDD tests for dt_backtest.py

當沖出場規則歷史回測：在合成 K 線上驗證四規則出場（停損 → 天花板停利 →
強制平倉 → 移動停利）與成本模型（手續費 + 交易稅 + 滑價），完全不依賴網路。
"""
from __future__ import annotations

import pytest


# ── 手算的期望值 helper（獨立於實作，作為驗收契約） ─────────────────────────

def _trigger_up(entry: float, pct: float) -> float:
    return entry * (1 + pct / 100)


def _trigger_down(entry: float, pct: float) -> float:
    return entry * (1 - pct / 100)


def _fill(trigger_price: float, slippage_pct: float) -> float:
    return trigger_price * (1 - slippage_pct / 100)


def _net_ret(exit_price: float, entry: float, commission: float, tax: float) -> float:
    gross = (exit_price - entry) / entry
    return gross - 2 * commission - tax


# ── BacktestParams ────────────────────────────────────────────────────────────

class TestBacktestParams:
    def test_defaults(self):
        from dt_backtest import BacktestParams
        p = BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
        )
        assert p.force_close_time == "13:00"
        assert p.commission == 0.001425
        assert p.tax == 0.0015
        assert p.slippage_pct == 0.05

    def test_overrides(self):
        from dt_backtest import BacktestParams
        p = BacktestParams(
            stop_loss_pct=2.0, take_profit_pct=6.0,
            trailing_start_pct=1.5, trailing_gap_pct=1.0,
            force_close_time="12:30", commission=0.001, tax=0.001,
            slippage_pct=0.0,
        )
        assert p.stop_loss_pct == 2.0
        assert p.force_close_time == "12:30"
        assert p.slippage_pct == 0.0


# ── simulate_day：四規則出場 ───────────────────────────────────────────────

class TestSimulateDayStopLoss:
    def test_stop_loss_triggers_on_bar_low(self):
        from dt_backtest import BacktestParams, simulate_day
        entry = 100.0
        params = BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
        )
        bars = [
            {"time": "09:05", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
            {"time": "09:10", "open": 100.5, "high": 100.5, "low": 96.5, "close": 97.0},
        ]
        r = simulate_day(bars, entry, params)

        trigger = _trigger_down(entry, 3.0)
        exit_price = _fill(trigger, params.slippage_pct)
        assert r.exit_reason == "stop_loss"
        assert r.exit_time == "09:10"
        assert r.exit_price == pytest.approx(exit_price)
        assert r.gross_ret == pytest.approx((exit_price - entry) / entry)
        assert r.net_ret == pytest.approx(
            _net_ret(exit_price, entry, params.commission, params.tax)
        )


class TestSimulateDayTakeProfit:
    def test_take_profit_triggers_on_bar_high(self):
        from dt_backtest import BacktestParams, simulate_day
        entry = 100.0
        params = BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
        )
        bars = [
            {"time": "09:05", "open": 100.0, "high": 108.0, "low": 99.0, "close": 107.0},
            {"time": "09:10", "open": 107.0, "high": 110.0, "low": 106.0, "close": 109.0},
        ]
        r = simulate_day(bars, entry, params)

        trigger = _trigger_up(entry, 9.0)
        exit_price = _fill(trigger, params.slippage_pct)
        assert r.exit_reason == "take_profit_ceiling"
        assert r.exit_time == "09:10"
        assert r.exit_price == pytest.approx(exit_price)
        assert r.net_ret == pytest.approx(
            _net_ret(exit_price, entry, params.commission, params.tax)
        )


class TestSimulateDaySameBarDoubleTrigger:
    def test_stop_loss_wins_when_both_hit_same_bar(self):
        """同一 bar 同時觸及停損與停利門檻時，保守判定為停損。"""
        from dt_backtest import BacktestParams, simulate_day
        entry = 100.0
        params = BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
        )
        bars = [
            {"time": "09:05", "open": 100.0, "high": 112.0, "low": 96.0, "close": 100.0},
        ]
        r = simulate_day(bars, entry, params)

        trigger = _trigger_down(entry, 3.0)
        exit_price = _fill(trigger, params.slippage_pct)
        assert r.exit_reason == "stop_loss"
        assert r.exit_time == "09:05"
        assert r.exit_price == pytest.approx(exit_price)


class TestSimulateDayForceClose:
    def test_force_close_exits_at_bar_close_when_time_reached(self):
        from dt_backtest import BacktestParams, simulate_day
        entry = 100.0
        params = BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
            force_close_time="13:00",
        )
        bars = [
            {"time": "09:05", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5},
            {"time": "13:00", "open": 100.5, "high": 101.5, "low": 100.0, "close": 101.0},
        ]
        r = simulate_day(bars, entry, params)

        exit_price = _fill(101.0, params.slippage_pct)  # 未觸價 → 以該 bar 收盤出場
        assert r.exit_reason == "force_close"
        assert r.exit_time == "13:00"
        assert r.exit_price == pytest.approx(exit_price)
        assert r.net_ret == pytest.approx(
            _net_ret(exit_price, entry, params.commission, params.tax)
        )

    def test_no_trigger_before_force_close_falls_through_to_eod(self):
        """資料在強平時間之前就結束：以最後一根收盤出場（reason=eod）。"""
        from dt_backtest import BacktestParams, simulate_day
        entry = 100.0
        params = BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
            force_close_time="13:00",
        )
        bars = [
            {"time": "09:05", "open": 100.0, "high": 100.8, "low": 99.5, "close": 100.3},
        ]
        r = simulate_day(bars, entry, params)
        assert r.exit_reason == "eod"
        assert r.exit_time == "09:05"


class TestSimulateDayTrailingStop:
    def test_trailing_stop_after_rally_then_pullback(self):
        from dt_backtest import BacktestParams, simulate_day
        entry = 100.0
        params = BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
        )
        bars = [
            # 先漲到 +6%（peak=106，超過 trailing_start_pct=3%），
            # 但本 bar 低點未跌破用「前一 bar 峰值」算出的停利線，不觸發。
            {"time": "09:05", "open": 100.0, "high": 106.0, "low": 99.5, "close": 105.5},
            # 用已確立的峰值 106 計算停利線 = 106*(1-2%) = 103.88，
            # 本 bar 低點 103.5 跌破 → 觸發移動停利。
            {"time": "09:10", "open": 105.5, "high": 107.0, "low": 103.5, "close": 104.0},
        ]
        r = simulate_day(bars, entry, params)

        peak = 106.0
        trigger = peak * (1 - params.trailing_gap_pct / 100)
        exit_price = _fill(trigger, params.slippage_pct)
        assert r.exit_reason == "trailing_stop"
        assert r.exit_time == "09:10"
        assert r.exit_price == pytest.approx(exit_price)
        assert r.net_ret == pytest.approx(
            _net_ret(exit_price, entry, params.commission, params.tax)
        )

    def test_no_trigger_when_peak_gain_below_trailing_start(self):
        """峰值漲幅未達 trailing_start_pct，移動停利不啟動。"""
        from dt_backtest import BacktestParams, simulate_day
        entry = 100.0
        params = BacktestParams(
            stop_loss_pct=10.0, take_profit_pct=20.0,
            trailing_start_pct=5.0, trailing_gap_pct=1.0,
            force_close_time="13:00",
        )
        bars = [
            {"time": "09:05", "open": 100.0, "high": 102.0, "low": 100.0, "close": 101.5},
            {"time": "09:10", "open": 101.5, "high": 102.5, "low": 100.5, "close": 101.0},
        ]
        r = simulate_day(bars, entry, params)
        # peak 最高只到 102.5（+2.5%），未達 trailing_start_pct=5%，
        # 也未觸及停損/停利/強平 → 應該落到 eod。
        assert r.exit_reason == "eod"


class TestSimulateDayEdgeCases:
    def test_empty_bars_returns_no_data(self):
        from dt_backtest import BacktestParams, simulate_day
        params = BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
        )
        r = simulate_day([], 100.0, params)
        assert r.exit_reason == "no_data"
        assert r.exit_price == 100.0
        assert r.gross_ret == 0.0
        assert r.net_ret == 0.0


class TestCostModelManualCalc:
    def test_fee_and_slippage_manual_calc(self):
        """手算一筆對數字：entry=100 → 觸價 110（漲幅10%），滑價 0，
        commission=0.001，tax=0.001。
        gross = (110-100)/100 = 0.10
        net   = 0.10 - 2*0.001 - 0.001 = 0.097
        """
        from dt_backtest import BacktestParams, simulate_day
        entry = 100.0
        params = BacktestParams(
            stop_loss_pct=50.0, take_profit_pct=10.0,
            trailing_start_pct=50.0, trailing_gap_pct=50.0,
            force_close_time="13:00",
            commission=0.001, tax=0.001, slippage_pct=0.0,
        )
        bars = [
            {"time": "09:05", "open": 100.0, "high": 110.0, "low": 100.0, "close": 110.0},
        ]
        r = simulate_day(bars, entry, params)
        assert r.exit_reason == "take_profit_ceiling"
        assert r.exit_price == pytest.approx(110.0)
        assert r.gross_ret == pytest.approx(0.10)
        assert r.net_ret == pytest.approx(0.097)


# ── run_backtest：彙總統計 ────────────────────────────────────────────────

class TestRunBacktest:
    def _daily_bars(self):
        # 第一筆：停損出場（虧損）
        loss_bars = [
            {"time": "09:05", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
            {"time": "09:10", "open": 100.5, "high": 100.5, "low": 96.5, "close": 97.0},
        ]
        # 第二筆：天花板停利出場（獲利）
        win_bars = [
            {"time": "09:05", "open": 100.0, "high": 108.0, "low": 99.0, "close": 107.0},
            {"time": "09:10", "open": 107.0, "high": 110.0, "low": 106.0, "close": 109.0},
        ]
        return {
            ("2026-06-30", "2330"): loss_bars,
            ("2026-07-01", "2454"): win_bars,
        }

    def _entries(self):
        return [
            {"date": "2026-06-30", "code": "2330", "entry_price": 100.0},
            {"date": "2026-07-01", "code": "2454", "entry_price": 100.0},
        ]

    def _params(self):
        from dt_backtest import BacktestParams
        return BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
        )

    def test_aggregates_n_win_rate_and_exit_reason_dist(self):
        from dt_backtest import run_backtest
        params = self._params()
        report = run_backtest(self._daily_bars(), self._entries(), params)

        assert report.n == 2
        assert report.win_rate == pytest.approx(0.5)
        assert report.exit_reason_dist == {"stop_loss": 1, "take_profit_ceiling": 1}

        loss_exit = _fill(_trigger_down(100.0, 3.0), params.slippage_pct)
        win_exit = _fill(_trigger_up(100.0, 9.0), params.slippage_pct)
        loss_net = _net_ret(loss_exit, 100.0, params.commission, params.tax)
        win_net = _net_ret(win_exit, 100.0, params.commission, params.tax)

        assert report.avg_loss == pytest.approx(loss_net)
        assert report.avg_win == pytest.approx(win_net)
        assert report.expectancy_net == pytest.approx((loss_net + win_net) / 2)

    def test_max_drawdown_from_compounded_equity_curve(self):
        from dt_backtest import run_backtest
        params = self._params()
        report = run_backtest(self._daily_bars(), self._entries(), params)

        loss_exit = _fill(_trigger_down(100.0, 3.0), params.slippage_pct)
        loss_net = _net_ret(loss_exit, 100.0, params.commission, params.tax)
        # 第一筆虧損後，權益從 1.0 跌到 1+loss_net，這就是本次回測的最深回撤，
        # 因為第二筆獲利後權益又創新高。
        expected_dd = -loss_net
        assert report.max_drawdown == pytest.approx(expected_dd)

    def test_missing_bars_are_skipped_gracefully(self):
        from dt_backtest import run_backtest
        params = self._params()
        entries = self._entries() + [
            {"date": "2026-07-02", "code": "9999", "entry_price": 50.0},  # 無對應 bars
        ]
        report = run_backtest(self._daily_bars(), entries, params)
        assert report.n == 2  # 缺資料的那筆不計入

    def test_empty_entries_returns_zeroed_report(self):
        from dt_backtest import run_backtest
        params = self._params()
        report = run_backtest({}, [], params)
        assert report.n == 0
        assert report.win_rate == 0.0
        assert report.max_drawdown == 0.0
        assert report.exit_reason_dist == {}


# ── sweep：參數網格掃描 ────────────────────────────────────────────────────

class TestSweep:
    def test_sweep_sorts_by_expectancy_descending(self):
        from dt_backtest import sweep

        # 單一交易日，價格持續下跌：
        #   bar1 low=97.5 (-2.5%)，bar2 low=97.0 (-3.0%)，bar2 time=13:00 也達強平時間，
        #   bar2 close=98.5。
        # stop_loss_pct=2.0 → bar1 就停損出場（97.951 附近，虧損較小）
        # stop_loss_pct=3.0 → bar2 停損出場（96.952 附近，虧損最大，因為停損線更低）
        # stop_loss_pct=5.0 → 都不觸發停損，bar2 觸發強平，以收盤 98.5 出場（虧損最小）
        bars = [
            {"time": "09:05", "open": 100.0, "high": 100.5, "low": 97.5, "close": 98.0},
            {"time": "13:00", "open": 98.0, "high": 99.0, "low": 97.0, "close": 98.5},
        ]
        daily_bars_by_stock = {("2026-06-30", "2330"): bars}
        entries = [{"date": "2026-06-30", "code": "2330", "entry_price": 100.0}]

        grid = {"stop_loss_pct": [2.0, 3.0, 5.0]}
        results = sweep(daily_bars_by_stock, entries, grid)

        assert len(results) == 3
        # 依 expectancy_net 由高到低排序
        expectancies = [r.expectancy_net for _, r in results]
        assert expectancies == sorted(expectancies, reverse=True)
        # 驗證排序邏輯確實反映規則差異，而非單純照參數值排序
        assert [p.stop_loss_pct for p, _ in results] == [5.0, 2.0, 3.0]

    def test_sweep_uses_baseline_defaults_for_unspecified_fields(self):
        from dt_backtest import sweep, BacktestParams
        bars = [
            {"time": "09:05", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5},
        ]
        daily_bars_by_stock = {("2026-06-30", "2330"): bars}
        entries = [{"date": "2026-06-30", "code": "2330", "entry_price": 100.0}]

        results = sweep(daily_bars_by_stock, entries, {"stop_loss_pct": [3.0]})
        params, report = results[0]
        assert isinstance(params, BacktestParams)
        assert params.take_profit_pct == 9.0
        assert params.trailing_start_pct == 3.0
        assert params.trailing_gap_pct == 2.0
