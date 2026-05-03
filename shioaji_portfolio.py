"""shioaji_portfolio.py

真實券商模式：繼承 SimulatedPortfolio 介面，加入：
1. 嚴格資金上限（open/add 前先檢查 capital）
2. 透過 Shioaji API 下真實委託單
3. dry_run=True：嚴格資金檢查但不下單（紙面交易）
4. sync_positions()：從券商同步目前持倉

模式：
  - api=None, dry_run=False → "offline"（不下單，不檢查，等同 SimulatedPortfolio）
  - api=None, dry_run=True  → "dry_run"（資金嚴格，但不下單）
  - api=<Shioaji>, dry_run=True  → "dry_run"（資金嚴格，不下單）
  - api=<Shioaji>, dry_run=False → "real"（資金嚴格，下真實委託）
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from portfolio import Position, SimulatedPortfolio, _DEFAULT_CAPITAL

log = logging.getLogger(__name__)


class ShioajiPortfolio(SimulatedPortfolio):
    """
    Shioaji 真實券商模式 Portfolio。

    Parameters
    ----------
    initial_capital : float
        帳戶初始資金
    api : shioaji.Shioaji | None
        已登入的 Shioaji 實例。None 時不下單。
    dry_run : bool
        True = 紙面交易（資金嚴格，不下真實單）
    """

    def __init__(
        self,
        initial_capital: float = _DEFAULT_CAPITAL,
        api=None,
        dry_run: bool = False,
    ):
        super().__init__(initial_capital=initial_capital)
        self._api     = api
        self._dry_run = dry_run

    # ── mode property ─────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        if self._api is not None and not self._dry_run:
            return "real"
        if self._dry_run:
            return "dry_run"
        return "offline"

    # ── Capital-enforced overrides ────────────────────────────────────────────

    def open_position(
        self,
        code: str,
        name: str,
        quantity: int,
        price: float,
        is_fractional: bool,
        shares: int,
        reason: str,
        entry_date: date,
    ) -> None:
        """新建倉位，嚴格檢查資金，並視模式下真實委託單。"""
        self._check_capital(price, quantity, is_fractional, shares)

        # 先更新本地狀態
        super().open_position(
            code=code, name=name,
            quantity=quantity, price=price,
            is_fractional=is_fractional, shares=shares,
            reason=reason, entry_date=entry_date,
        )

        # 下委託單（real 模式才下）
        if self._api is not None and not self._dry_run:
            self._place_buy_order(code, quantity, price, is_fractional, shares)

    def add_position(
        self,
        code: str,
        quantity: int,
        price: float,
        is_fractional: bool,
        shares: int,
    ) -> None:
        """加碼，嚴格檢查資金。"""
        self._check_capital(price, quantity, is_fractional, shares)
        super().add_position(
            code=code, quantity=quantity, price=price,
            is_fractional=is_fractional, shares=shares,
        )
        if self._api is not None and not self._dry_run:
            self._place_buy_order(code, quantity, price, is_fractional, shares)

    def close_position(self, code: str, price: float) -> None:
        """全部平倉，並下賣出委託。"""
        pos = self._positions.get(code)
        if pos is None:
            raise ValueError(f"未持有 {code}")

        qty      = pos.quantity
        shares   = pos.shares
        is_frac  = pos.is_fractional

        # 更新本地狀態
        super().close_position(code=code, price=price)

        # 下委託單
        if self._api is not None and not self._dry_run:
            self._place_sell_order(code, qty, price, is_frac, shares)

    def reduce_position(
        self,
        code: str,
        quantity: int,
        price: float,
        is_fractional: bool,
        shares: int,
    ) -> None:
        """減碼，視模式下賣出委託。"""
        super().reduce_position(
            code=code, quantity=quantity, price=price,
            is_fractional=is_fractional, shares=shares,
        )
        if self._api is not None and not self._dry_run:
            self._place_sell_order(code, quantity, price, is_fractional, shares)

    # ── Broker sync ───────────────────────────────────────────────────────────

    def sync_positions(self) -> None:
        """從 Shioaji 同步目前持倉到本地狀態。api=None 時靜默跳過。"""
        if self._api is None:
            return
        try:
            broker_positions = self._api.list_positions(
                self._api.stock_account
            )
            if not broker_positions:
                return
            # Clear and rebuild from broker data
            self._positions.clear()
            for bp in broker_positions:
                code  = str(bp.code)
                qty   = getattr(bp, "quantity", 0)
                price = getattr(bp, "price", 0.0)
                name  = getattr(bp, "name", code)
                self._positions[code] = Position(
                    code=code, name=name,
                    quantity=qty, is_fractional=False, shares=0,
                    avg_cost=float(price),
                    entry_date=date.today(),
                    entry_reason="broker_sync",
                )
            log.info("sync_positions: synced %d positions from broker", len(self._positions))
        except Exception as e:
            log.error("sync_positions failed: %s", e)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _check_capital(
        self,
        price: float,
        quantity: int,
        is_fractional: bool,
        shares: int,
    ) -> None:
        cost = self._calc_cost(price, quantity, is_fractional, shares)
        if cost > self._cash:
            raise ValueError(
                f"資金不足：需要 {cost:,.0f} 元，帳戶剩餘 {self._cash:,.0f} 元"
            )

    def _place_buy_order(
        self,
        code: str,
        quantity: int,
        price: float,
        is_fractional: bool,
        shares: int,
    ) -> None:
        """透過 Shioaji 下市價買單。失敗只 log，不 raise（已更新本地狀態）。"""
        try:
            import shioaji as sj
            contract = self._api.Contracts.Stocks[code]
            action_qty = shares if is_fractional else quantity

            order = self._api.Order(
                action=sj.constant.Action.Buy,
                price=price,
                quantity=action_qty,
                order_type=sj.constant.OrderType.LMT,
                price_type=sj.constant.StockPriceType.LMT,
                order_lot=(
                    sj.constant.StockOrderLot.IntradayOdd
                    if is_fractional
                    else sj.constant.StockOrderLot.Common
                ),
                account=self._api.stock_account,
            )
            trade = self._api.place_order(contract, order)
            log.info("place_buy_order %s qty=%s trade=%s", code, action_qty, trade)
        except Exception as e:
            log.error("place_buy_order %s failed: %s", code, e)

    def _place_sell_order(
        self,
        code: str,
        quantity: int,
        price: float,
        is_fractional: bool,
        shares: int,
    ) -> None:
        """透過 Shioaji 下市價賣單。失敗只 log。"""
        try:
            import shioaji as sj
            contract = self._api.Contracts.Stocks[code]
            action_qty = shares if is_fractional else quantity

            order = self._api.Order(
                action=sj.constant.Action.Sell,
                price=price,
                quantity=action_qty,
                order_type=sj.constant.OrderType.LMT,
                price_type=sj.constant.StockPriceType.LMT,
                order_lot=(
                    sj.constant.StockOrderLot.IntradayOdd
                    if is_fractional
                    else sj.constant.StockOrderLot.Common
                ),
                account=self._api.stock_account,
            )
            trade = self._api.place_order(contract, order)
            log.info("place_sell_order %s qty=%s trade=%s", code, action_qty, trade)
        except Exception as e:
            log.error("place_sell_order %s failed: %s", code, e)

    # ── Persistence (override to save mode metadata) ──────────────────────────

    def save(self, path: str) -> None:
        """存檔（加入 mode 資訊）。"""
        super().save(path)  # reuse parent logic

    @classmethod
    def load(
        cls,
        path: str,
        default_capital: float = _DEFAULT_CAPITAL,
        api=None,
        dry_run: bool = False,
    ) -> "ShioajiPortfolio":
        """從 JSON 載入，重建 ShioajiPortfolio 而非 SimulatedPortfolio。"""
        import json
        from pathlib import Path

        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return cls(initial_capital=default_capital, api=api, dry_run=dry_run)

        portfolio = cls(
            initial_capital=data.get("initial_capital", default_capital),
            api=api,
            dry_run=dry_run,
        )
        portfolio._cash = data.get("cash", default_capital)

        from datetime import date
        from portfolio import Position

        for d in data.get("positions", []):
            portfolio._positions[d["code"]] = Position(
                code=d["code"],
                name=d["name"],
                quantity=d["quantity"],
                is_fractional=d["is_fractional"],
                shares=d["shares"],
                avg_cost=d["avg_cost"],
                entry_date=date.fromisoformat(d["entry_date"]),
                entry_reason=d["entry_reason"],
                current_price=d.get("current_price", 0.0),
            )

        return portfolio
