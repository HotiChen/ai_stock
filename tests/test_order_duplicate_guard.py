from __future__ import annotations

"""
tests/test_order_duplicate_guard.py — 重複下單防護與硬限額測試

覆蓋範圍（不重複 test_executor.py 已有的基礎案例）：

test_executor.py 已涵蓋：
  - is_duplicate_order() 的 no_prior / same_code_action_today / different cases
  - check_hard_limit() 的 within/equal/exceeds
  - place_stock_order() 回傳 success=True/False 的基本場景
  - Shioaji Action.Buy/Sell 正規化

本文件新增（未涵蓋）：
  (a) 重複意圖：place_stock_order() 在收到 same-code+same-action 的 prior_orders
      時，回傳 success=False 且 reason 包含「重複」，且不呼叫 api.place_order
  (b) ORDER_HARD_LIMIT 超限：amount > hard_limit → 回傳 success=False，
      且不呼叫 api.place_order（驗證 broker API 未被觸發）
  (c) PAPER_TRADING=true（env var 路徑）：order_id 以 "PAPER-" 開頭，
      api.place_order 從未被呼叫；success=True
"""

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest


# ── 輔助：構建帶有真實 contract 的 mock api ───────────────────────────────────

def _real_api(order_id: str = "ORD001") -> MagicMock:
    """回傳可正常下單的 Shioaji mock api。"""
    api = MagicMock()
    contract = MagicMock()
    api.Contracts.Stocks.get.return_value = contract
    trade = MagicMock()
    trade.order.id = order_id
    api.place_order.return_value = trade
    return api


# ── (a) 重複下單攔截 ──────────────────────────────────────────────────────────


class TestDuplicateBuyGuardInPlaceStockOrder:
    """
    place_stock_order() 的 Guard 1（重複下單）：
    prior_orders 已含相同 code+action+today → 拒絕，不觸碰 api.place_order。
    """

    def _prior_today(self, code: str, action: str) -> list[dict]:
        return [{"code": code, "action": action, "date": date.today().isoformat()}]

    def test_duplicate_buy_returns_failure(self):
        """同代號同方向今日已下單 → success=False。"""
        from executor import place_stock_order
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000,
            prior_orders=self._prior_today("2330", "buy"),
            paper_trading=False,
        )
        assert result.success is False

    def test_duplicate_buy_reason_contains_chinese_duplicate_keyword(self):
        """重複下單的拒絕 reason 應包含「重複」。"""
        from executor import place_stock_order
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000,
            prior_orders=self._prior_today("2330", "buy"),
            paper_trading=False,
        )
        assert "重複" in result.reason

    def test_duplicate_buy_does_not_call_api_place_order(self):
        """重複下單必須在到達 api.place_order 之前就被攔截。"""
        from executor import place_stock_order
        api = _real_api()
        place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000,
            prior_orders=self._prior_today("2330", "buy"),
            paper_trading=False,
        )
        api.place_order.assert_not_called()

    def test_duplicate_sell_blocked_same_logic(self):
        """Sell 方向同樣套用重複防護。"""
        from executor import place_stock_order
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="sell", budget=100_000, price=50.0,
            hard_limit=500_000,
            prior_orders=self._prior_today("2330", "sell"),
            paper_trading=False,
        )
        assert result.success is False
        assert "重複" in result.reason
        api.place_order.assert_not_called()

    def test_different_code_is_not_blocked(self):
        """prior_orders 含不同代號的記錄，不應攔截新代號的下單。"""
        from executor import place_stock_order
        api = _real_api()
        result = place_stock_order(
            api=api, code="2454", name="聯發科",
            action="buy", budget=200_000, price=50.0,
            hard_limit=500_000,
            prior_orders=self._prior_today("2330", "buy"),  # 不同代號
            paper_trading=False,
        )
        # 2454 之前未下過，應允許進入下單流程
        assert result.success is True

    def test_shioaji_action_buy_in_prior_still_blocked(self):
        """prior_orders 中 action 為 Shioaji 原始格式 'Action.Buy'
        也應被正規化後攔截（通過 is_duplicate_order 的 _normalize_action）。"""
        from executor import place_stock_order
        api = _real_api()
        prior = [{"code": "2330", "action": "Action.Buy",
                  "date": date.today().isoformat()}]
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000,
            prior_orders=prior,
            paper_trading=False,
        )
        assert result.success is False
        api.place_order.assert_not_called()


# ── (b) ORDER_HARD_LIMIT 超限攔截 ────────────────────────────────────────────


class TestHardLimitGuardInPlaceStockOrder:
    """
    Guard 3（硬性金額上限）：amount > hard_limit → 拒絕，不觸碰 api.place_order。
    test_executor.py 已驗 success=False 與 reason 包含「上限」；
    本類補充驗證 api.place_order 未被呼叫。
    """

    def test_exceeds_hard_limit_does_not_call_api_place_order(self):
        """超過上限時，broker API 不應被碰觸。"""
        from executor import place_stock_order
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=10_000,  # amount=100_000 >> 10_000
            prior_orders=[],
            paper_trading=False,
        )
        assert result.success is False
        api.place_order.assert_not_called()

    def test_hard_limit_explicit_very_small_blocks_order(self):
        """明確傳入極小 hard_limit → 超過限額被攔截，api.place_order 不被呼叫。

        此測試不依賴 env var 重載，確認 guard 邏輯本身正確運作。
        """
        from executor import place_stock_order
        api = _real_api()
        # budget=100_000, price=50 → 2 張，amount=100_000，遠超 hard_limit=500
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500,  # 極小上限
            prior_orders=[],
            paper_trading=False,
        )
        assert result.success is False
        api.place_order.assert_not_called()

    def test_hard_limit_boundary_equal_allows_order(self):
        """amount == hard_limit 邊界值應被允許（≤ 而非 <）。"""
        from executor import place_stock_order
        # price=50, budget=50000 → 1 張，amount=50000
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=50_000, price=50.0,
            hard_limit=50_000,  # amount == hard_limit
            prior_orders=[],
            paper_trading=False,
        )
        assert result.success is True


# ── (c) PAPER_TRADING=true 路徑 ──────────────────────────────────────────────


class TestPaperTradingMode:
    """
    paper_trading=True（或 env PAPER_TRADING=true）時：
      - 回傳 success=True
      - order_id 以 "PAPER-" 開頭
      - api.place_order 從未被呼叫（不觸碰 broker）
    """

    def test_paper_trading_param_true_returns_success(self):
        """明確傳入 paper_trading=True → success=True。"""
        from executor import place_stock_order
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=200_000, price=150.0,
            hard_limit=500_000, prior_orders=[],
            paper_trading=True,
        )
        assert result.success is True

    def test_paper_trading_param_true_order_id_starts_with_paper(self):
        """paper_trading=True → order_id 以 'PAPER-' 開頭。"""
        from executor import place_stock_order
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=200_000, price=150.0,
            hard_limit=500_000, prior_orders=[],
            paper_trading=True,
        )
        assert result.order_id is not None
        assert result.order_id.startswith("PAPER-"), (
            f"paper_trading order_id 應以 'PAPER-' 開頭，實際 {result.order_id!r}"
        )

    def test_paper_trading_param_true_never_calls_api_place_order(self):
        """paper_trading=True → api.place_order 絕對不被呼叫。"""
        from executor import place_stock_order
        api = _real_api()
        place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=200_000, price=150.0,
            hard_limit=500_000, prior_orders=[],
            paper_trading=True,
        )
        api.place_order.assert_not_called()

    def test_paper_trading_env_var_true_never_calls_api(self, monkeypatch):
        """環境變數 PAPER_TRADING=true（預設值）→ paper_trading=None 時自動啟用。"""
        from executor import place_stock_order
        monkeypatch.setenv("PAPER_TRADING", "true")
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=200_000, price=150.0,
            hard_limit=500_000, prior_orders=[],
            paper_trading=None,  # 讀 env
        )
        assert result.success is True
        api.place_order.assert_not_called()

    def test_paper_trading_env_var_false_calls_api(self, monkeypatch):
        """PAPER_TRADING=false → paper_trading=None 路徑走真實下單。"""
        from executor import place_stock_order
        monkeypatch.setenv("PAPER_TRADING", "false")
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=200_000, price=150.0,
            hard_limit=500_000, prior_orders=[],
            paper_trading=None,  # 讀 env → false → 走真實路徑
        )
        # 真實路徑應呼叫 api.place_order（mock 成功 → success=True）
        assert result.success is True
        api.place_order.assert_called_once()

    def test_paper_trading_order_id_contains_date_and_code(self):
        """paper_trading order_id 格式應為 PAPER-{YYYYMMDD}-{code}。"""
        from executor import place_stock_order
        api = _real_api()
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=200_000, price=150.0,
            hard_limit=500_000, prior_orders=[],
            paper_trading=True,
        )
        today_str = date.today().strftime("%Y%m%d")
        expected_id = f"PAPER-{today_str}-2330"
        assert result.order_id == expected_id, (
            f"期望 order_id={expected_id!r}，實際 {result.order_id!r}"
        )

    def test_paper_trading_guards_still_applied_before_paper_path(self):
        """Guards 1（重複）和 2（數量不足）在 paper_trading 路徑前已攔截。

        paper_trading=True 不應讓 guard 失效。
        """
        from executor import place_stock_order
        api = _real_api()

        # Guard 1：重複下單 — paper_trading=True 仍應回傳 False
        prior = [{"code": "2330", "action": "buy", "date": date.today().isoformat()}]
        result = place_stock_order(
            api=api, code="2330", name="台積電",
            action="buy", budget=100_000, price=50.0,
            hard_limit=500_000, prior_orders=prior,
            paper_trading=True,
        )
        assert result.success is False, (
            "paper_trading=True 下重複下單仍應被 Guard 1 攔截"
        )
