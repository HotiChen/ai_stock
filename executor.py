from __future__ import annotations

"""
Executor: Shioaji order placement with safety guards.

SIMULATION MODE ONLY until SIMULATION=false in .env.
Every order goes through:
  1. Duplicate check  (same code+action today)
  2. Hard dollar limit check
  3. Lot type selection (common vs intraday_odd)
  4. Quantity calculation
  5. api.place_order()

IMPORTANT: In production mode every order must be confirmed by the user
           via user_confirm.py before this module is called.
"""

import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

import shioaji.constant as sc
from dataclasses import dataclass as _dc

from logger import get_logger

log = get_logger(__name__)

SHARES_PER_LOT = 1000


@_dc
class _OrderSpec:
    """Plain order struct so tests can inspect real attribute values."""
    price:      float
    quantity:   int
    action:     sc.Action
    price_type: sc.StockPriceType
    order_type: sc.OrderType
    order_lot:  sc.StockOrderLot
DEFAULT_HARD_LIMIT = float(os.getenv("ORDER_HARD_LIMIT", "150000"))


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    code:     str
    name:     str
    action:   str          # "buy" | "sell"
    quantity: int          # lots (common) or shares (intraday_odd)
    price:    float
    amount:   float        # quantity * price * (1000 if common else 1)
    lot_type: str          # "common" | "intraday_odd"
    success:  bool
    order_id: Optional[str]
    reason:   str          # empty on success; error description on failure


# ── Pure helpers ──────────────────────────────────────────────────────────────

def calc_lot_type(budget: float, price: float) -> str:
    """Return 'common' if budget covers at least 1 full lot, else 'intraday_odd'."""
    if price <= 0:
        return "intraday_odd"
    one_lot_cost = price * SHARES_PER_LOT
    return "common" if budget >= one_lot_cost else "intraday_odd"


def calc_quantity(budget: float, price: float, lot_type: str) -> int:
    """
    Return how many units to buy.
    common       → number of lots (整張), each lot = 1000 shares
    intraday_odd → number of shares (零股)
    """
    if price <= 0:
        return 0
    if lot_type == "common":
        return int(budget // (price * SHARES_PER_LOT))
    else:
        return int(budget // price)


def check_hard_limit(amount: float, limit: float) -> bool:
    """Return True if amount does not exceed the hard dollar limit."""
    return amount <= limit


def is_duplicate_order(code: str, action: str, prior_orders: list[dict]) -> bool:
    """
    Return True if there's already an order for the same code+action today.
    prior_orders: list of dicts with keys {code, action, date (ISO string)}.
    """
    today = date.today().isoformat()
    return any(
        o.get("code") == code
        and o.get("action") == action
        and o.get("date") == today
        for o in prior_orders
    )


# ── Order placement ───────────────────────────────────────────────────────────

def place_stock_order(
    api,
    code: str,
    name: str,
    action: str,            # "buy" | "sell"
    budget: float,
    price: float,
    hard_limit: float = DEFAULT_HARD_LIMIT,
    prior_orders: list[dict] | None = None,
) -> ExecutionResult:
    """
    Place a stock order through Shioaji with all safety guards applied.
    Returns ExecutionResult with success=True/False.
    """
    prior_orders = prior_orders or []
    sj_action = sc.Action.Buy if action == "buy" else sc.Action.Sell

    # Guard 1: duplicate
    if is_duplicate_order(code, action, prior_orders):
        return ExecutionResult(
            code=code, name=name, action=action,
            quantity=0, price=price, amount=0.0,
            lot_type="common", success=False,
            order_id=None, reason=f"重複委託：{code} {action} 今日已下單",
        )

    # Guard 2: lot type + quantity
    lot_type = calc_lot_type(budget, price)
    quantity = calc_quantity(budget, price, lot_type)

    if quantity <= 0:
        return ExecutionResult(
            code=code, name=name, action=action,
            quantity=0, price=price, amount=0.0,
            lot_type=lot_type, success=False,
            order_id=None, reason="預算不足，無法下單任何數量",
        )

    # Guard 3: hard limit
    multiplier = SHARES_PER_LOT if lot_type == "common" else 1
    amount = quantity * price * multiplier
    if not check_hard_limit(amount, hard_limit):
        return ExecutionResult(
            code=code, name=name, action=action,
            quantity=quantity, price=price, amount=amount,
            lot_type=lot_type, success=False,
            order_id=None, reason=f"超過硬性金額上限 {hard_limit:,.0f}，委託金額 {amount:,.0f}",
        )

    # Place order
    try:
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            return ExecutionResult(
                code=code, name=name, action=action,
                quantity=quantity, price=price, amount=amount,
                lot_type=lot_type, success=False,
                order_id=None, reason=f"找不到合約：{code}",
            )

        sj_lot = (
            sc.StockOrderLot.Common
            if lot_type == "common"
            else sc.StockOrderLot.IntradayOdd
        )

        order = _OrderSpec(
            price=price,
            quantity=quantity,
            action=sj_action,
            price_type=sc.StockPriceType.LMT,
            order_type=sc.OrderType.ROD,
            order_lot=sj_lot,
        )
        trade = api.place_order(contract, order)
        order_id = trade.order.id
        log.info("委託成功：%s %s %s %d%s @%s id=%s",
                 code, name, action, quantity,
                 "張" if lot_type == "common" else "股",
                 price, order_id)
        return ExecutionResult(
            code=code, name=name, action=action,
            quantity=quantity, price=price, amount=amount,
            lot_type=lot_type, success=True,
            order_id=order_id, reason="",
        )

    except Exception as e:
        log.error("委託失敗 %s: %s", code, e)
        return ExecutionResult(
            code=code, name=name, action=action,
            quantity=quantity, price=price, amount=amount,
            lot_type=lot_type, success=False,
            order_id=None, reason=str(e),
        )


# ── Force stop-loss ───────────────────────────────────────────────────────────

def force_stop_loss(
    api,
    code: str,
    name: str,
    quantity: int,
    lot_type: str = "common",
) -> bool:
    """
    Send an immediate market-price sell order for stop-loss.
    Returns True on success, False on error.
    """
    sj_lot = (
        sc.StockOrderLot.Common
        if lot_type == "common"
        else sc.StockOrderLot.IntradayOdd
    )
    try:
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            log.error("force_stop_loss: 找不到合約 %s", code)
            return False
        order = _OrderSpec(
            price=0,
            quantity=quantity,
            action=sc.Action.Sell,
            price_type=sc.StockPriceType.MKT,
            order_type=sc.OrderType.ROD,
            order_lot=sj_lot,
        )
        trade = api.place_order(contract, order)
        log.warning("強制停損：%s %s %d%s 市價賣出 id=%s",
                    code, name, quantity,
                    "張" if lot_type == "common" else "股",
                    trade.order.id)
        return True
    except Exception as e:
        log.error("force_stop_loss 失敗 %s: %s", code, e)
        return False
