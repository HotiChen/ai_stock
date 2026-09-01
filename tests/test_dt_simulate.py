"""
tests/test_dt_simulate.py — 每筆預測的虛擬損益

需求：所有預測（含 AI 判 skip 的）都套用同一套買賣計畫——買固定金額、
漲 X% 停利、跌 Y% 停損——然後看到底賺多少賠多少。

為什麼 long 和 skip 要用同一套規則
----------------------------------
skip 的預測沒有 target_price / stop_loss（AI 沒給），若沿用「預測自己的
目標價」就只有 long 算得出損益，無從回答「AI 說不要的那些，如果我買了會
怎樣」——而那正是衡量 AI 過濾價值的唯一方法。

為什麼一定要分鐘 K
------------------
只有日 OHLC 時，當日同時觸及停利與停損就無法判斷先後，只能保守假設停損
先觸發，系統性低估績效。分鐘 K 逐根掃描才知道真正先發生的是哪一個。
"""
import pytest

import dt_fees
import dt_simulate as sim

CAPITAL = 30_000.0


def _bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


class TestFindExit:
    """進場 100，停利 109（+9%），停損 97（-3%）。"""

    def _find(self, bars, force_close_time=None):
        return sim.find_exit(bars, tp_price=109.0, sl_price=97.0,
                             force_close_time=force_close_time)

    def test_take_profit_when_touched_first(self):
        bars = [
            _bar("09:00", 100.0, 102.0, 99.0, 101.0),
            _bar("10:00", 101.0, 110.0, 100.0, 109.5),   # 觸停利
            _bar("11:00", 109.0, 109.0, 95.0, 96.0),     # 之後才觸停損
        ]
        assert self._find(bars) == (109.0, "take_profit")

    def test_stop_loss_when_touched_first(self):
        bars = [
            _bar("09:00", 100.0, 101.0, 96.0, 97.5),     # 觸停損
            _bar("10:00", 97.0, 115.0, 97.0, 114.0),     # 之後才觸停利
        ]
        assert self._find(bars) == (97.0, "stop_loss")

    def test_same_bar_both_touched_is_conservative(self):
        """★ 同一根 K 內兩者都碰到，無法確認先後 → 保守取停損。

        這是唯一誠實的處理：假設停利先觸發會讓回測績效系統性偏高。
        """
        bars = [_bar("09:00", 100.0, 110.0, 96.0, 100.0)]
        assert self._find(bars) == (97.0, "stop_loss")

    def test_neither_touched_exits_at_last_close(self):
        bars = [
            _bar("09:00", 100.0, 102.0, 99.0, 101.0),
            _bar("13:25", 101.0, 103.0, 100.0, 102.5),
        ]
        assert self._find(bars) == (102.5, "close")

    def test_force_close_uses_that_bar_close(self):
        """★ 強制平倉：13:15 之後的行情不算數。

        台股 13:30 收盤但系統 13:15 就強平；用收盤價結算會高估那些
        「尾盤才拉回來」的部位。
        """
        bars = [
            _bar("09:00", 100.0, 102.0, 99.0, 101.0),
            _bar("13:15", 101.0, 102.0, 100.0, 100.5),   # 強平點
            _bar("13:25", 100.5, 108.0, 100.0, 107.0),   # 之後才拉回，不算
        ]
        assert self._find(bars, force_close_time="13:15") == (100.5, "force_close")

    def test_take_profit_before_force_close_still_wins(self):
        bars = [
            _bar("09:00", 100.0, 110.0, 99.0, 109.5),    # 早上就觸停利
            _bar("13:15", 109.0, 109.0, 108.0, 108.5),
        ]
        assert self._find(bars, force_close_time="13:15") == (109.0, "take_profit")

    def test_force_close_time_missing_from_bars_falls_back_to_last(self):
        """該時點沒有 K 線（暫停交易、資料缺漏）時，退回最後一根收盤。"""
        bars = [_bar("09:00", 100.0, 102.0, 99.0, 101.0)]
        price, reason = self._find(bars, force_close_time="13:15")
        assert (price, reason) == (101.0, "close")

    def test_empty_bars_returns_none(self):
        assert sim.find_exit([], tp_price=109.0, sl_price=97.0) is None


class TestSimulate:
    def _bars_up(self):
        return [
            _bar("09:00", 100.0, 102.0, 99.0, 101.0),
            _bar("10:00", 101.0, 110.0, 100.0, 109.5),
        ]

    def test_entry_is_first_bar_open(self):
        r = sim.simulate(self._bars_up(), capital=CAPITAL,
                         take_profit_pct=9.0, stop_loss_pct=3.0)
        assert r.entry == 100.0

    def test_take_profit_result(self):
        r = sim.simulate(self._bars_up(), capital=CAPITAL,
                         take_profit_pct=9.0, stop_loss_pct=3.0)
        assert r.exit_reason == "take_profit"
        assert r.exit == pytest.approx(109.0)

    def test_pnl_matches_dt_fees(self):
        """損益必須與 dt_fees 一致——三個模組共用同一份公式才可互相比較。"""
        r = sim.simulate(self._bars_up(), capital=CAPITAL,
                         take_profit_pct=9.0, stop_loss_pct=3.0)
        expected = dt_fees.net_pnl(CAPITAL, 100.0, 109.0)
        assert (r.pnl, r.pnl_pct) == expected

    def test_nine_percent_gain_nets_less_than_gross(self):
        """★ +9% 的 30,000 部位實際入袋必須少於 2,700——費用不能被忽略。"""
        r = sim.simulate(self._bars_up(), capital=CAPITAL,
                         take_profit_pct=9.0, stop_loss_pct=3.0)
        assert 0 < r.pnl < 2_700

    def test_fixed_capital_not_compounding(self):
        """★ 每筆獨立 30,000，不受前一筆盈虧影響——要看的是單筆表現，
        滾動本金會讓早期的運氣主導後面所有數字。"""
        a = sim.simulate(self._bars_up(), capital=CAPITAL,
                         take_profit_pct=9.0, stop_loss_pct=3.0)
        b = sim.simulate(self._bars_up(), capital=CAPITAL,
                         take_profit_pct=9.0, stop_loss_pct=3.0)
        assert a.pnl == b.pnl

    def test_stop_loss_result_is_negative(self):
        bars = [_bar("09:00", 100.0, 100.5, 96.0, 96.5)]
        r = sim.simulate(bars, capital=CAPITAL,
                         take_profit_pct=9.0, stop_loss_pct=3.0)
        assert r.exit_reason == "stop_loss"
        assert r.pnl < 0

    def test_flat_day_still_loses_fees(self):
        """原價進出仍是虧的。忽略這點會把「沒觸發」的日子當成打平。"""
        bars = [_bar("09:00", 100.0, 101.0, 99.0, 100.0)]
        r = sim.simulate(bars, capital=CAPITAL,
                         take_profit_pct=9.0, stop_loss_pct=3.0)
        assert r.exit_reason == "close"
        assert r.pnl < 0

    def test_returns_none_for_empty_bars(self):
        assert sim.simulate([], capital=CAPITAL,
                            take_profit_pct=9.0, stop_loss_pct=3.0) is None

    def test_returns_none_when_entry_price_invalid(self):
        """★ 開盤價 0 代表沒有報價，不是「股價 0 元」——不得產生一筆假交易。"""
        bars = [_bar("09:00", 0.0, 0.0, 0.0, 0.0)]
        assert sim.simulate(bars, capital=CAPITAL,
                            take_profit_pct=9.0, stop_loss_pct=3.0) is None

    def test_untestable_flat_day_excluded(self):
        """當日振幅趨近 0（券商報價異常）不得產生假交易紀錄——
        與 daytrading_review._determine_outcome 的 untestable 守衛同一個理由。"""
        bars = [_bar("09:00", 100.0, 100.001, 99.999, 100.0)]
        assert sim.simulate(bars, capital=CAPITAL, take_profit_pct=9.0,
                            stop_loss_pct=3.0) is None

    def test_force_close_time_passed_through(self):
        bars = [
            _bar("09:00", 100.0, 102.0, 99.0, 101.0),
            _bar("13:15", 101.0, 102.0, 100.0, 100.5),
            _bar("13:25", 100.5, 108.0, 100.0, 107.0),
        ]
        r = sim.simulate(bars, capital=CAPITAL, take_profit_pct=9.0,
                         stop_loss_pct=3.0, force_close_time="13:15")
        assert r.exit_reason == "force_close"
        assert r.exit == pytest.approx(100.5)


class TestPriceLevels:
    def test_tp_sl_derived_from_entry(self):
        assert sim.price_levels(100.0, take_profit_pct=9.0,
                                stop_loss_pct=3.0) == (109.0, 97.0)

    def test_rounded_to_two_decimals(self):
        tp, sl = sim.price_levels(33.33, take_profit_pct=9.0, stop_loss_pct=3.0)
        assert tp == round(tp, 2) and sl == round(sl, 2)
