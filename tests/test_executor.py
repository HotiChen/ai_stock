from __future__ import annotations

"""
TDD tests for executor.py

Covers:
- calc_lot_type()         : 根據 budget 和 price 決定整股/零股
- calc_quantity()         : 計算下單股數（整股=張數, 零股=股數）
- check_hard_limit()      : 單次委託不超過硬性金額上限
- is_duplicate_order()    : 防重複下單（同 code 同 action 同日）
- place_stock_order()     : 向 Shioaji 實際送出委託
- force_stop_loss()       : 停損單（賣出現有持倉）
- ExecutionResult dataclass
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import date, datetime


# ── calc_lot_type ─────────────────────────────────────────────────────────────

class TestCalcLotType:
    def test_budget_enough_for_one_lot_returns_common(self):
        from executor import calc_lot_type
        # 1 張 = 1000 股；price=50, 1張=50000
        result = calc_lot_type(budget=60_000, price=50.0)
        assert result == "common"

    def test_budget_less_than_one_lot_returns_intraday_odd(self):
        from executor import calc_lot_type
        # 1 張台積電 850*1000=850000，budget 只有 10000
        result = calc_lot_type(budget=10_000, price=850.0)
        assert result == "intraday_odd"

    def test_budget_exactly_one_lot_returns_common(self):
        from executor import calc_lot_type
        result = calc_lot_type(budget=50_000, price=50.0)
        assert result == "common"

    def test_high_price_small_budget_returns_intraday_odd(self):
        from executor import calc_lot_type
        result = calc_lot_type(budget=5_000, price=600.0)
        assert result == "intraday_odd"

    def test_zero_price_returns_intraday_odd(self):
        from executor import calc_lot_type
        result = calc_lot_type(budget=10_000, price=0.0)
        assert result == "intraday_odd"


# ── calc_quantity ─────────────────────────────────────────────────────────────

class TestCalcQuantity:
    def test_common_returns_lot_count(self):
        from executor import calc_quantity
        # budget=100000, price=50 → 100000/(50*1000)=2 張
        qty = calc_quantity(budget=100_000, price=50.0, lot_type="common")
        assert qty == 2

    def test_common_rounds_down(self):
        from executor import calc_quantity
        # budget=149999, price=50 → 149999/50000 = 2.9 → 2 張
        qty = calc_quantity(budget=149_999, price=50.0, lot_type="common")
        assert qty == 2

    def test_intraday_odd_returns_share_count(self):
        from executor import calc_quantity
        # budget=5000, price=50 → 5000/50 = 100 股
        qty = calc_quantity(budget=5_000, price=50.0, lot_type="intraday_odd")
        assert qty == 100

    def test_intraday_odd_rounds_down(self):
        from executor import calc_quantity
        # budget=5099, price=50 → 5099/50 = 101.98 → 101 股
        qty = calc_quantity(budget=5_099, price=50.0, lot_type="intraday_odd")
        assert qty == 101

    def test_zero_price_returns_zero(self):
        from executor import calc_quantity
        qty = calc_quantity(budget=10_000, price=0.0, lot_type="common")
        assert qty == 0

    def test_common_budget_less_than_one_lot_returns_zero(self):
        from executor import calc_quantity
        # budget=40000 < 50*1000=50000 → 0 張
        qty = calc_quantity(budget=40_000, price=50.0, lot_type="common")
        assert qty == 0


# ── check_hard_limit ──────────────────────────────────────────────────────────

class TestCheckHardLimit:
    def test_within_limit_returns_true(self):
        from executor import check_hard_limit
        assert check_hard_limit(amount=9_000, limit=10_000) is True

    def test_equal_limit_returns_true(self):
        from executor import check_hard_limit
        assert check_hard_limit(amount=10_000, limit=10_000) is True

    def test_exceeds_limit_returns_false(self):
        from executor import check_hard_limit
        assert check_hard_limit(amount=10_001, limit=10_000) is False

    def test_zero_amount_returns_true(self):
        from executor import check_hard_limit
        assert check_hard_limit(amount=0, limit=10_000) is True


# ── is_duplicate_order ────────────────────────────────────────────────────────

class TestIsDuplicateOrder:
    def test_no_prior_orders_returns_false(self):
        from executor import is_duplicate_order
        assert is_duplicate_order("2330", "buy", prior_orders=[]) is False

    def test_same_code_and_action_today_returns_true(self):
        from executor import is_duplicate_order
        prior = [{"code": "2330", "action": "buy", "date": date.today().isoformat()}]
        assert is_duplicate_order("2330", "buy", prior_orders=prior) is True

    def test_same_code_different_action_returns_false(self):
        from executor import is_duplicate_order
        prior = [{"code": "2330", "action": "sell", "date": date.today().isoformat()}]
        assert is_duplicate_order("2330", "buy", prior_orders=prior) is False

    def test_different_code_same_action_returns_false(self):
        from executor import is_duplicate_order
        prior = [{"code": "2454", "action": "buy", "date": date.today().isoformat()}]
        assert is_duplicate_order("2330", "buy", prior_orders=prior) is False

    def test_old_order_different_date_returns_false(self):
        from executor import is_duplicate_order
        prior = [{"code": "2330", "action": "buy", "date": "2020-01-01"}]
        assert is_duplicate_order("2330", "buy", prior_orders=prior) is False


# ── ExecutionResult ───────────────────────────────────────────────────────────

class TestExecutionResult:
    def test_success_result_fields(self):
        from executor import ExecutionResult
        r = ExecutionResult(
            code="2330", name="台積電", action="buy",
            quantity=2, price=850.0, amount=1_700_000.0,
            lot_type="common", success=True, order_id="abc123", reason="",
        )
        assert r.success is True
        assert r.order_id == "abc123"

    def test_failure_result_has_reason(self):
        from executor import ExecutionResult
        r = ExecutionResult(
            code="2330", name="台積電", action="buy",
            quantity=0, price=850.0, amount=0.0,
            lot_type="common", success=False, order_id=None,
            reason="超過硬性金額上限",
        )
        assert r.success is False
        assert "上限" in r.reason


# ── place_stock_order ─────────────────────────────────────────────────────────

class TestPlaceStockOrder:
    def _mock_api(self, order_id="ORD001"):
        api = MagicMock()
        contract = MagicMock()
        api.Contracts.Stocks.get.return_value = contract
        trade = MagicMock()
        trade.order.id = order_id
        api.place_order.return_value = trade
        return api

    def test_success_returns_execution_result_with_success_true(self):
        from executor import place_stock_order
        api = self._mock_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000, prior_orders=[],
        )
        assert result.success is True

    def test_success_result_has_order_id(self):
        from executor import place_stock_order
        api = self._mock_api(order_id="XYZ999")
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000, prior_orders=[],
        )
        assert result.order_id == "XYZ999"

    def test_duplicate_order_returns_failure(self):
        from executor import place_stock_order
        api = self._mock_api()
        prior = [{"code": "2330", "action": "buy", "date": date.today().isoformat()}]
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000, prior_orders=prior,
        )
        assert result.success is False
        assert "重複" in result.reason

    def test_exceeds_hard_limit_returns_failure(self):
        from executor import place_stock_order
        api = self._mock_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=10_000,  # limit < amount
            prior_orders=[],
        )
        assert result.success is False
        assert "上限" in result.reason

    def test_zero_quantity_returns_failure(self):
        from executor import place_stock_order
        api = self._mock_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100, price=850.0,  # too small
            hard_limit=500_000, prior_orders=[],
        )
        assert result.success is False

    def test_contract_not_found_returns_failure(self):
        from executor import place_stock_order
        api = MagicMock()
        api.Contracts.Stocks.get.return_value = None
        result = place_stock_order(
            api=api, code="9999", name="不存在",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000, prior_orders=[],
        )
        assert result.success is False

    def test_api_exception_returns_failure(self):
        from executor import place_stock_order
        api = MagicMock()
        api.Contracts.Stocks.get.side_effect = Exception("network error")
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000, prior_orders=[],
        )
        assert result.success is False

    def test_odd_lot_uses_intraday_odd_lot_type(self):
        from executor import place_stock_order
        import shioaji.constant as sc
        api = self._mock_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=5_000, price=850.0,  # too small for 1 lot
            hard_limit=500_000, prior_orders=[],
        )
        # Should have used IntradayOdd lot type
        assert result.lot_type == "intraday_odd"

    def test_common_lot_used_when_budget_sufficient(self):
        from executor import place_stock_order
        api = self._mock_api()
        result = place_stock_order(
            api=api, code="0050", name="元大台灣50",
            action="buy", budget=200_000, price=150.0,
            hard_limit=500_000, prior_orders=[],
        )
        assert result.lot_type == "common"


# ── force_stop_loss ───────────────────────────────────────────────────────────

class TestForceStopLoss:
    def _mock_api(self):
        api = MagicMock()
        contract = MagicMock()
        api.Contracts.Stocks.get.return_value = contract
        trade = MagicMock()
        trade.order.id = "STOP001"
        api.place_order.return_value = trade
        return api

    def test_stop_loss_calls_place_order(self):
        from executor import force_stop_loss
        api = self._mock_api()
        result = force_stop_loss(
            api=api, code="2330", name="台積電",
            quantity=2, lot_type="common",
        )
        assert api.place_order.called

    def test_stop_loss_action_is_sell(self):
        from executor import force_stop_loss
        import shioaji.constant as sc
        api = self._mock_api()
        force_stop_loss(api=api, code="2330", name="台積電", quantity=2, lot_type="common")
        call_args = api.place_order.call_args
        order = call_args[0][1] if call_args[0] else call_args[1].get("order")
        assert order.action == sc.Action.Sell

    def test_stop_loss_uses_market_price(self):
        from executor import force_stop_loss
        import shioaji.constant as sc
        api = self._mock_api()
        force_stop_loss(api=api, code="2330", name="台積電", quantity=2, lot_type="common")
        call_args = api.place_order.call_args
        order = call_args[0][1] if call_args[0] else call_args[1].get("order")
        assert order.price_type == sc.StockPriceType.MKT

    def test_stop_loss_returns_true_on_success(self):
        from executor import force_stop_loss
        api = self._mock_api()
        result = force_stop_loss(api=api, code="2330", name="台積電", quantity=2, lot_type="common")
        assert result is True

    def test_stop_loss_returns_false_on_api_error(self):
        from executor import force_stop_loss
        api = MagicMock()
        api.Contracts.Stocks.get.side_effect = Exception("error")
        result = force_stop_loss(api=api, code="2330", name="台積電", quantity=2, lot_type="common")
        assert result is False
