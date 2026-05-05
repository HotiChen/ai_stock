from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from portfolio import Position, SimulatedPortfolio

TODAY = date(2026, 5, 3)


# ── Position ──────────────────────────────────────────────────────────────────

class TestPosition:
    def _make_whole(self, qty=1, avg_cost=100.0) -> Position:
        return Position(
            code="2330", name="台積電",
            quantity=qty, is_fractional=False, shares=0,
            avg_cost=avg_cost, entry_date=TODAY, entry_reason="RSI低檔反彈",
        )

    def _make_frac(self, shares=50, avg_cost=100.0) -> Position:
        return Position(
            code="2330", name="台積電",
            quantity=0, is_fractional=True, shares=shares,
            avg_cost=avg_cost, entry_date=TODAY, entry_reason="零股測試",
        )

    # total_shares
    def test_total_shares_whole_lot(self):
        p = self._make_whole(qty=2)
        assert p.total_shares == 2_000

    def test_total_shares_fractional(self):
        p = self._make_frac(shares=50)
        assert p.total_shares == 50

    # total_cost
    def test_total_cost_whole_lot(self):
        # 1張 @ 100/股 = 100 * 1000 = 100,000
        p = self._make_whole(qty=1, avg_cost=100.0)
        assert p.total_cost == pytest.approx(100_000.0)

    def test_total_cost_fractional(self):
        # 50股 @ 100/股 = 5,000
        p = self._make_frac(shares=50, avg_cost=100.0)
        assert p.total_cost == pytest.approx(5_000.0)

    # market_value
    def test_market_value_gain(self):
        p = self._make_whole(qty=1, avg_cost=100.0)
        assert p.market_value(current_price=110.0) == pytest.approx(110_000.0)

    def test_market_value_loss(self):
        p = self._make_whole(qty=1, avg_cost=100.0)
        assert p.market_value(current_price=90.0) == pytest.approx(90_000.0)

    # unrealized_pnl
    def test_unrealized_pnl_positive(self):
        p = self._make_whole(qty=1, avg_cost=100.0)
        assert p.unrealized_pnl(110.0) == pytest.approx(10_000.0)

    def test_unrealized_pnl_negative(self):
        p = self._make_whole(qty=1, avg_cost=100.0)
        assert p.unrealized_pnl(90.0) == pytest.approx(-10_000.0)

    def test_unrealized_pnl_fractional(self):
        # 50股 @ 100，現價 120 → pnl = 50 * 20 = 1,000
        p = self._make_frac(shares=50, avg_cost=100.0)
        assert p.unrealized_pnl(120.0) == pytest.approx(1_000.0)

    # unrealized_pnl_pct
    def test_unrealized_pnl_pct_positive(self):
        p = self._make_whole(qty=1, avg_cost=100.0)
        assert p.unrealized_pnl_pct(110.0) == pytest.approx(10.0)

    def test_unrealized_pnl_pct_negative(self):
        p = self._make_whole(qty=1, avg_cost=100.0)
        assert p.unrealized_pnl_pct(90.0) == pytest.approx(-10.0)


# ── SimulatedPortfolio — init ─────────────────────────────────────────────────

class TestSimulatedPortfolioInit:
    def test_initial_cash_equals_capital(self):
        p = SimulatedPortfolio(initial_capital=30_000.0)
        assert p.get_available_capital() == 30_000.0

    def test_initial_positions_empty(self):
        p = SimulatedPortfolio(initial_capital=30_000.0)
        assert p.get_positions() == []

    def test_initial_total_value_equals_capital(self):
        p = SimulatedPortfolio(initial_capital=30_000.0)
        assert p.get_total_value({}) == pytest.approx(30_000.0)


# ── open_position ─────────────────────────────────────────────────────────────

class TestOpenPosition:
    def test_open_creates_position(self):
        p = SimulatedPortfolio(100_000.0)
        p.open_position("2330", "台積電", quantity=1, price=50.0,
                        is_fractional=False, shares=0,
                        reason="RSI低", entry_date=TODAY)
        assert len(p.get_positions()) == 1

    def test_open_correct_code(self):
        p = SimulatedPortfolio(100_000.0)
        p.open_position("2330", "台積電", quantity=1, price=50.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        assert p.get_position("2330").code == "2330"

    def test_open_deducts_cash_whole_lot(self):
        # 1張 @ 50/股 = 50,000
        p = SimulatedPortfolio(100_000.0)
        p.open_position("2330", "台積電", quantity=1, price=50.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        assert p.get_available_capital() == pytest.approx(50_000.0)

    def test_open_deducts_cash_fractional(self):
        # 100股 @ 50/股 = 5,000
        p = SimulatedPortfolio(30_000.0)
        p.open_position("2330", "台積電", quantity=0, price=50.0,
                        is_fractional=True, shares=100, reason="", entry_date=TODAY)
        assert p.get_available_capital() == pytest.approx(25_000.0)

    def test_open_avg_cost_equals_price(self):
        p = SimulatedPortfolio(100_000.0)
        p.open_position("2330", "台積電", quantity=1, price=85.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        assert p.get_position("2330").avg_cost == pytest.approx(85.0)

    def test_open_reason_stored(self):
        p = SimulatedPortfolio(100_000.0)
        p.open_position("2330", "台積電", quantity=1, price=50.0,
                        is_fractional=False, shares=0, reason="突破月線", entry_date=TODAY)
        assert p.get_position("2330").entry_reason == "突破月線"

    def test_open_insufficient_capital_raises(self):
        p = SimulatedPortfolio(1_000.0)
        with pytest.raises(ValueError, match="資金不足"):
            p.open_position("2330", "台積電", quantity=1, price=50.0,
                            is_fractional=False, shares=0, reason="", entry_date=TODAY)

    def test_open_duplicate_code_raises(self):
        """已持有同一檔，要用 add_position，不能重複 open"""
        p = SimulatedPortfolio(200_000.0)
        p.open_position("2330", "台積電", quantity=1, price=50.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        with pytest.raises(ValueError, match="已持有"):
            p.open_position("2330", "台積電", quantity=1, price=55.0,
                            is_fractional=False, shares=0, reason="", entry_date=TODAY)


# ── add_position ──────────────────────────────────────────────────────────────

class TestAddPosition:
    def _portfolio_with_2330(self) -> SimulatedPortfolio:
        # 300k 初始資金：開 1張@100 = 100k，剩 200k 可供加碼測試
        p = SimulatedPortfolio(300_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="初始", entry_date=TODAY)
        return p

    def test_add_increases_quantity(self):
        p = self._portfolio_with_2330()
        p.add_position("2330", quantity=1, price=110.0,
                       is_fractional=False, shares=0)
        assert p.get_position("2330").quantity == 2

    def test_add_updates_avg_cost(self):
        # 1張 @100 + 1張 @110 → avg = (100,000 + 110,000) / 2000 = 105
        p = self._portfolio_with_2330()
        p.add_position("2330", quantity=1, price=110.0,
                       is_fractional=False, shares=0)
        assert p.get_position("2330").avg_cost == pytest.approx(105.0)

    def test_add_deducts_cash(self):
        p = self._portfolio_with_2330()
        capital_before = p.get_available_capital()
        p.add_position("2330", quantity=1, price=110.0,
                       is_fractional=False, shares=0)
        assert p.get_available_capital() == pytest.approx(capital_before - 110_000.0)

    def test_add_nonexistent_position_raises(self):
        p = SimulatedPortfolio(200_000.0)
        with pytest.raises(ValueError, match="未持有"):
            p.add_position("9999", quantity=1, price=50.0,
                           is_fractional=False, shares=0)

    def test_add_insufficient_capital_raises(self):
        p = SimulatedPortfolio(105_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        with pytest.raises(ValueError, match="資金不足"):
            p.add_position("2330", quantity=1, price=110.0,
                           is_fractional=False, shares=0)


# ── reduce_position ───────────────────────────────────────────────────────────

class TestReducePosition:
    def _portfolio_with_2330(self) -> SimulatedPortfolio:
        p = SimulatedPortfolio(300_000.0)
        p.open_position("2330", "台積電", quantity=2, price=100.0,
                        is_fractional=False, shares=0, reason="初始", entry_date=TODAY)
        return p

    def test_reduce_decreases_quantity(self):
        p = self._portfolio_with_2330()
        p.reduce_position("2330", quantity=1, price=110.0,
                          is_fractional=False, shares=0)
        assert p.get_position("2330").quantity == 1

    def test_reduce_adds_cash(self):
        p = self._portfolio_with_2330()
        capital_before = p.get_available_capital()
        p.reduce_position("2330", quantity=1, price=110.0,
                          is_fractional=False, shares=0)
        assert p.get_available_capital() == pytest.approx(capital_before + 110_000.0)

    def test_reduce_avg_cost_unchanged(self):
        """減碼不影響剩餘部位的成本"""
        p = self._portfolio_with_2330()
        p.reduce_position("2330", quantity=1, price=110.0,
                          is_fractional=False, shares=0)
        assert p.get_position("2330").avg_cost == pytest.approx(100.0)

    def test_reduce_to_zero_removes_position(self):
        p = self._portfolio_with_2330()
        p.reduce_position("2330", quantity=2, price=110.0,
                          is_fractional=False, shares=0)
        assert p.get_position("2330") is None

    def test_reduce_more_than_held_raises(self):
        p = self._portfolio_with_2330()
        with pytest.raises(ValueError, match="持股不足"):
            p.reduce_position("2330", quantity=3, price=110.0,
                              is_fractional=False, shares=0)

    def test_reduce_nonexistent_raises(self):
        p = SimulatedPortfolio(100_000.0)
        with pytest.raises(ValueError, match="未持有"):
            p.reduce_position("9999", quantity=1, price=50.0,
                              is_fractional=False, shares=0)


# ── close_position ────────────────────────────────────────────────────────────

class TestClosePosition:
    def test_close_removes_position(self):
        p = SimulatedPortfolio(200_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        p.close_position("2330", price=110.0)
        assert p.get_position("2330") is None

    def test_close_returns_cash_at_current_price(self):
        p = SimulatedPortfolio(200_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        p.close_position("2330", price=110.0)
        # 開始 200k, 買1張扣100k, 賣110k回來 → 210k
        assert p.get_available_capital() == pytest.approx(210_000.0)

    def test_close_at_loss(self):
        p = SimulatedPortfolio(200_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        p.close_position("2330", price=90.0)
        assert p.get_available_capital() == pytest.approx(190_000.0)

    def test_close_nonexistent_raises(self):
        p = SimulatedPortfolio(100_000.0)
        with pytest.raises(ValueError, match="未持有"):
            p.close_position("9999", price=50.0)

    def test_close_fractional_position(self):
        p = SimulatedPortfolio(30_000.0)
        p.open_position("2330", "台積電", quantity=0, price=100.0,
                        is_fractional=True, shares=50, reason="", entry_date=TODAY)
        p.close_position("2330", price=120.0)
        # 50股 @ 120 = 6,000 回來
        assert p.get_available_capital() == pytest.approx(30_000.0 - 5_000.0 + 6_000.0)


# ── get_total_value ───────────────────────────────────────────────────────────

class TestGetTotalValue:
    def test_total_value_no_positions(self):
        p = SimulatedPortfolio(30_000.0)
        assert p.get_total_value({}) == pytest.approx(30_000.0)

    def test_total_value_with_position(self):
        p = SimulatedPortfolio(200_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        # cash = 100k, position = 1張 @ current 110 = 110k → total 210k
        assert p.get_total_value({"2330": 110.0}) == pytest.approx(210_000.0)

    def test_total_value_missing_price_uses_cost(self):
        """現價未提供時用成本價（不會讓 total_value 憑空減少）"""
        p = SimulatedPortfolio(200_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        assert p.get_total_value({}) == pytest.approx(200_000.0)


# ── update_prices ─────────────────────────────────────────────────────────────

class TestUpdatePrices:
    def test_update_sets_current_price(self):
        p = SimulatedPortfolio(200_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="", entry_date=TODAY)
        p.update_prices({"2330": 115.0})
        pos = p.get_position("2330")
        assert pos.current_price == pytest.approx(115.0)

    def test_update_ignores_unknown_codes(self):
        """更新不持有的股票不會報錯"""
        p = SimulatedPortfolio(30_000.0)
        p.update_prices({"9999": 999.0})  # should not raise


# ── persistence ───────────────────────────────────────────────────────────────

class TestPersistence:
    # ── C3: 原子寫入 ──────────────────────────────────────────────────────────

    def test_save_no_tmp_file_left(self, tmp_path):
        """save() 完成後不應留下 .tmp 暫存檔（原子寫入）。"""
        p = SimulatedPortfolio(30_000.0)
        fpath = tmp_path / "portfolio.json"
        p.save(str(fpath))
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"找到殘留 .tmp 檔：{tmp_files}"

    def test_save_uses_atomic_write(self, tmp_path):
        """save() 必須先寫入 .tmp 再 rename（驗證原子性路徑）。"""
        import os
        from unittest.mock import patch, call as mcall

        p = SimulatedPortfolio(30_000.0)
        fpath = tmp_path / "portfolio.json"

        with patch("os.replace") as mock_replace:
            p.save(str(fpath))
            assert mock_replace.called, "save() 沒有呼叫 os.replace()（非原子寫入）"
            dest = mock_replace.call_args[0][1]
            assert str(dest) == str(fpath), "os.replace 目標路徑不符"

    def test_save_creates_parent_dirs(self, tmp_path):
        """save() 可以自動建立不存在的父目錄。"""
        p = SimulatedPortfolio(30_000.0)
        fpath = tmp_path / "nested" / "deep" / "portfolio.json"
        p.save(str(fpath))
        assert fpath.exists()

    def test_save_creates_file(self, tmp_path):
        p = SimulatedPortfolio(30_000.0)
        fpath = tmp_path / "portfolio.json"
        p.save(str(fpath))
        assert fpath.exists()

    def test_load_restores_cash(self, tmp_path):
        p = SimulatedPortfolio(30_000.0)
        fpath = tmp_path / "portfolio.json"
        p.save(str(fpath))
        p2 = SimulatedPortfolio.load(str(fpath))
        assert p2.get_available_capital() == pytest.approx(30_000.0)

    def test_load_restores_positions(self, tmp_path):
        p = SimulatedPortfolio(200_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="測試", entry_date=TODAY)
        fpath = tmp_path / "portfolio.json"
        p.save(str(fpath))
        p2 = SimulatedPortfolio.load(str(fpath))
        assert len(p2.get_positions()) == 1
        assert p2.get_position("2330").avg_cost == pytest.approx(100.0)

    def test_load_restores_entry_reason(self, tmp_path):
        p = SimulatedPortfolio(200_000.0)
        p.open_position("2330", "台積電", quantity=1, price=100.0,
                        is_fractional=False, shares=0, reason="RSI低檔", entry_date=TODAY)
        fpath = tmp_path / "portfolio.json"
        p.save(str(fpath))
        p2 = SimulatedPortfolio.load(str(fpath))
        assert p2.get_position("2330").entry_reason == "RSI低檔"

    def test_load_nonexistent_returns_default(self, tmp_path):
        """檔案不存在時回傳初始 portfolio"""
        p = SimulatedPortfolio.load(str(tmp_path / "nonexistent.json"),
                                    default_capital=30_000.0)
        assert p.get_available_capital() == pytest.approx(30_000.0)
        assert p.get_positions() == []


# ── C1+C2: 資金驗證 ───────────────────────────────────────────────────────────

class TestCapitalValidation:
    def test_open_position_raises_when_insufficient_cash(self):
        p = SimulatedPortfolio(initial_capital=1000.0)
        with pytest.raises(ValueError, match="資金不足"):
            p.open_position(
                code="2330", name="台積電",
                quantity=1, price=900.0,       # cost = 900*1000 = 900_000 >> 1000
                is_fractional=False, shares=0,
                reason="test", entry_date=date.today()
            )

    def test_open_position_succeeds_when_sufficient(self):
        p = SimulatedPortfolio(initial_capital=1_000_000.0)
        p.open_position(
            code="2330", name="台積電",
            quantity=1, price=800.0,
            is_fractional=False, shares=0,
            reason="test", entry_date=date.today()
        )
        assert p.get_available_capital() == pytest.approx(1_000_000 - 800 * 1000)

    def test_repair_cash_resets_to_initial(self):
        p = SimulatedPortfolio(initial_capital=30_000.0)
        p._cash = -2_000_000.0   # 手動弄爆
        p.repair_to_initial()
        assert p._cash == pytest.approx(30_000.0)
        assert p.get_positions() == []   # 清掉不合理的持倉
