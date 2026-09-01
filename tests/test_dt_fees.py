"""
tests/test_dt_fees.py — 台股當沖費用單一真相來源

原本手續費常數散在兩處（dt_paper_trade._COMMISSION / dt_backtest.BacktestParams），
模擬層若再寫第三份，三邊算出的損益就無法互相比較——「AI 過濾值不值得」這個
問題正是靠比較不同策略的損益來回答的，分母不一致等於沒有答案。

順帶修正一個既有的近似：原 dt_paper_trade._calc_pnl 把賣出手續費與證交稅
都算在「買進金額」上。實際上賣方的費用是按**賣出金額**計收，賺錢時會低估
費用、賠錢時高估。金額不大（成交額的 0.3%），但沒有理由算錯。
"""
import pytest

import dt_fees


class TestCommission:
    def test_standard_rate(self):
        assert dt_fees.commission(100_000) == pytest.approx(142.5)

    def test_minimum_fee_applies_to_small_amounts(self):
        """券商手續費有最低 20 元門檻，小額交易佔比會明顯變高。"""
        assert dt_fees.commission(1_000) == pytest.approx(20.0)

    def test_discount_applied(self):
        """券商折扣（如 6 折）直接影響當沖是否划算，必須可設定。"""
        assert dt_fees.commission(100_000, discount=0.6) == pytest.approx(85.5)

    def test_discount_still_respects_minimum(self):
        assert dt_fees.commission(10_000, discount=0.6) == pytest.approx(20.0)


class TestTax:
    def test_intraday_tax_is_half(self):
        """當沖證交稅減半：0.3% → 0.15%。"""
        assert dt_fees.tax(100_000) == pytest.approx(150.0)

    def test_tax_has_no_minimum(self):
        assert dt_fees.tax(1_000) == pytest.approx(1.5)


class TestNetPnl:
    def test_profit_after_fees(self):
        """買 30,000、賣出價漲 9% → 毛利 2,700，扣掉三筆費用。"""
        pnl, pct = dt_fees.net_pnl(30_000, entry=100.0, exit_price=109.0)
        exit_amount = 30_000 * 109.0 / 100.0            # 32,700
        expected = (exit_amount - 30_000
                    - dt_fees.commission(30_000)
                    - dt_fees.commission(exit_amount)
                    - dt_fees.tax(exit_amount))
        assert pnl == pytest.approx(round(expected, 0))
        assert pct == pytest.approx(round(pnl / 30_000, 4))

    def test_loss_after_fees_is_worse_than_gross(self):
        """賠錢時費用讓虧損更大，不會因為賣出金額較小而變成賺。"""
        pnl, _ = dt_fees.net_pnl(30_000, entry=100.0, exit_price=97.0)
        assert pnl < -900.0

    def test_flat_exit_still_loses_fees(self):
        """★ 原價進出仍是虧的——當沖的成本底線。忽略這點會高估勝率。"""
        pnl, _ = dt_fees.net_pnl(30_000, entry=100.0, exit_price=100.0)
        assert pnl < 0

    def test_sell_side_fees_use_exit_amount_not_capital(self):
        """★ 賣出手續費與證交稅按賣出金額計收，不是買進金額。

        大賺時賣出金額遠高於買進金額，用買進金額算會低估費用。
        """
        pnl_correct, _ = dt_fees.net_pnl(30_000, entry=100.0, exit_price=200.0)
        exit_amount = 60_000.0
        wrong = (exit_amount - 30_000
                 - dt_fees.commission(30_000) * 2
                 - dt_fees.tax(30_000))
        assert pnl_correct < round(wrong, 0), "賣方費用應按賣出金額計算"

    def test_zero_entry_returns_zero(self):
        """進場價 0（無報價）不得除以零。"""
        assert dt_fees.net_pnl(30_000, entry=0.0, exit_price=100.0) == (0.0, 0.0)


class TestSingleSourceOfTruth:
    def test_paper_trade_delegates_to_dt_fees(self):
        """dt_paper_trade 必須用同一套公式，否則模擬倉與模擬損益不可比。"""
        import dt_paper_trade
        a = dt_paper_trade._calc_pnl(30_000, 100.0, 109.0)
        b = dt_fees.net_pnl(30_000, 100.0, 109.0)
        assert a == b

    def test_backtest_uses_same_rates(self):
        import dt_backtest
        p = dt_backtest.BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
        )
        assert p.commission == dt_fees.COMMISSION_RATE
        assert p.tax == dt_fees.TAX_RATE_INTRADAY
