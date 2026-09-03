from __future__ import annotations

"""
Executor: Shioaji order placement with safety guards.

兩個獨立開關：
  SHIOAJI_SIMULATION — Shioaji 連線模式（true=模擬報價, false=真實報價）
  PAPER_TRADING      — 下單模式（true=模擬下單不送券商, false=真實下單）

預設兩者皆為 true（安全模式）。
要看真實報價但不真實下單：SHIOAJI_SIMULATION=false, PAPER_TRADING=true
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


def _normalize_action(raw: str) -> str:
    """Normalize a Shioaji action value to lowercase 'buy' or 'sell'.

    Shioaji's ``str(trade.order.action)`` returns ``'Action.Buy'`` /
    ``'Action.Sell'``.  Executor internals use ``'buy'`` / ``'sell'``.
    This function bridges the two so ``is_duplicate_order`` works with
    both in-process strings and raw API values.

    Examples::

        _normalize_action("Action.Buy")  -> "buy"
        _normalize_action("Action.Sell") -> "sell"
        _normalize_action("buy")         -> "buy"
        _normalize_action("SELL")        -> "sell"
    """
    tail = str(raw).lower().split(".")[-1]
    if tail == "buy":
        return "buy"
    if tail == "sell":
        return "sell"
    return tail  # passthrough for unexpected values


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


def calc_risk_quantity(
    total_budget: float,
    risk_pct: float,
    entry_price: Optional[float],
    stop_loss_price: Optional[float],
    budget_cap: float,
    hard_limit: float,
) -> tuple[int, str]:
    """風險額倉位法：依「單筆最大虧損」反推股數，而非固定預算。

    風險額 = total_budget × risk_pct / 100
    每股風險 = entry_price - stop_loss_price
    股數 = 風險額 ÷ 每股風險，向下取整（股為單位，非張）

    再套用金額上限（budget_cap 與 hard_limit 取更嚴格者）：若風險額算出的股數
    金額超過上限，改依上限金額用 calc_lot_type/calc_quantity 的既有邏輯
    （買得起整張優先整張）重算，並把結果換算回「股」為單位回傳，讓呼叫端可
    一致地用 ``budget = quantity * entry_price`` 交給 place_stock_order（其
    guard 會再依此 budget 自行決定整張/零股與最終張數/股數）。

    entry_price 或 stop_loss_price 缺失、或每股風險 <= 0（停損價未低於進場價）、
    或風險額不足買入最小單位時，回傳 (0, reason)：呼叫端應退回固定預算法。
    """
    if entry_price is None or entry_price <= 0:
        return 0, "缺少進場價，改用固定預算法"
    if stop_loss_price is None:
        return 0, "缺少停損價，改用固定預算法"

    per_share_risk = entry_price - stop_loss_price
    if per_share_risk <= 0:
        return 0, "停損價未低於進場價（每股風險 <= 0），改用固定預算法"

    risk_amount = total_budget * (risk_pct / 100.0)
    shares = int(risk_amount // per_share_risk)
    if shares <= 0:
        return 0, "風險額不足買入最小單位，改用固定預算法"

    amount_cap = min(budget_cap, hard_limit)
    if shares * entry_price > amount_cap:
        capped_lot_type = calc_lot_type(amount_cap, entry_price)
        capped_qty = calc_quantity(amount_cap, entry_price, capped_lot_type)
        shares = capped_qty * SHARES_PER_LOT if capped_lot_type == "common" else capped_qty
        return shares, f"風險股數金額超過上限 {amount_cap:,.0f}，已依上限調整為 {shares} 股"

    # 未觸上限路徑同樣做整張正規化：回傳值必須等於 place_stock_order 以
    # budget = shares × entry_price 實際會成交的股數，否則 Telegram 顯示的
    # 風險額股數與實際曝險不一致（例：2500 股 → common 2 張 = 2000 股）。
    budget = shares * entry_price
    lot_type = calc_lot_type(budget, entry_price)
    qty = calc_quantity(budget, entry_price, lot_type)
    normalized = qty * SHARES_PER_LOT if lot_type == "common" else qty
    if normalized <= 0:
        return 0, "正規化後不足最小單位，改用固定預算法"
    if normalized != shares:
        return normalized, f"已正規化為整張單位（{shares} → {normalized} 股）"
    return shares, ""


def check_hard_limit(amount: float, limit: float) -> bool:
    """Return True if amount does not exceed the hard dollar limit."""
    return amount <= limit


def is_duplicate_order(code: str, action: str, prior_orders: list[dict]) -> bool:
    """
    Return True if there's already an order for the same code+action today.
    prior_orders: list of dicts with keys {code, action, date (ISO string)}.

    Both the ``action`` argument and the stored action values are normalized via
    ``_normalize_action`` before comparison, so Shioaji raw values like
    ``'Action.Buy'`` are treated the same as the internal ``'buy'``.
    """
    today = date.today().isoformat()
    norm_action = _normalize_action(action)
    return any(
        o.get("code") == code
        and _normalize_action(o.get("action", "")) == norm_action
        and o.get("date") == today
        for o in prior_orders
    )


def guard_new_order(code: str, action: str, paper_trading: bool,
                    ledger_db_path: str | None = None,
                    name: str = "") -> Optional[str]:
    """新委託的共用守衛。通過回 None，否則回拒絕原因。

    為什麼要抽出來：app.py 的 Streamlit 手動下單直接呼叫 api.place_order，
    繞過了 HALT、重複防護，以及**最危險的 PAPER_TRADING**——紙上模式下按
    那個按鈕會送出真實委託。

    抽成共用函式而非在 app.py 複製一份，是因為複製的那份遲早會跟本體長歪。
    這正是本專案反覆出現的失敗模式（同一個判斷散在多處，改了一處忘了另一處）。

    副作用：買進通過時會**佔用** order_ledger 的宣告。呼叫端若最終沒有下單，
    必須自行呼叫 order_ledger.release()。
    """
    act = _normalize_action(action)

    if paper_trading and act in ("buy", "sell"):
        return "紙上模式（PAPER_TRADING=true）不送出真實委託"

    if act == "buy":
        try:
            from halt import is_halted
            if is_halted():
                return "系統緊急暫停中，不執行買進（賣出與強制平倉不受影響）"
        except Exception as e:
            log.warning("HALT 狀態讀取失敗（放行買單）: %s", e)

        try:
            import order_ledger
            kw = {"db_path": ledger_db_path} if ledger_db_path else {}
            if not order_ledger.claim(code, act, note=name, **kw):
                return f"重複委託：{code} 今日買單已由其他流程送出"
        except Exception as e:
            log.warning("下單帳本不可用（放行）: %s", e)

    return None


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
    paper_trading: bool | None = None,  # None = 讀 PAPER_TRADING env
    ledger_db_path: str | None = None,  # None = order_ledger 預設路徑
) -> ExecutionResult:
    """
    Place a stock order through Shioaji with all safety guards applied.
    Returns ExecutionResult with success=True/False.
    """
    prior_orders = prior_orders or []
    if paper_trading is None:
        paper_trading = os.getenv("PAPER_TRADING", "true").lower() != "false"
    sj_action = sc.Action.Buy if action == "buy" else sc.Action.Sell

    def _skip(reason: str, qty: int = 0, amt: float = 0.0, lt: str = "common") -> ExecutionResult:
        try:
            from notifier import notify_order_skipped
            notify_order_skipped(code, name, reason)
        except Exception:
            pass
        return ExecutionResult(
            code=code, name=name, action=action,
            quantity=qty, price=price, amount=amt,
            lot_type=lt, success=False, order_id=None, reason=reason,
        )

    # Guard 0: HALT — **只擋買進，賣出永遠放行**
    #
    # HALT 的原始設計意圖是「停止開新倉」（halt.py docstring：三段式緊急煞車
    # 的最輕一段）。但它原本被實作成 main() 主迴圈頂端的全域擋，連停損、
    # 13:15 強制平倉、13:35 複盤一起關掉——抱著當沖部位按下緊急暫停，系統
    # 會停止保護那個部位，沒平掉的當沖隔天變成交割義務。
    #
    # 閘門放在這裡而不是各呼叫端：三個買進入口（MarketOpenJob、DT auto-buy、
    # Telegram 快速下單）全部經過本函式，這是唯一的咽喉點。散在呼叫端遲早
    # 會漏一個——那正是這次事故的模式。
    if action == "buy":
        try:
            from halt import is_halted
            if is_halted():
                return _skip("系統緊急暫停中，不執行買進（賣出與強制平倉不受影響）")
        except Exception as e:
            # 讀不到旗標時**放行**：擋住正常買單的代價高於漏擋一次，
            # 且緊急暫停另有 Telegram 告警與人工介入路徑。
            log.warning("HALT 狀態讀取失敗（放行買單）: %s", e)

    # Guard 1: duplicate（記憶體內，僅在呼叫端有傳 prior_orders 時生效）
    if is_duplicate_order(code, action, prior_orders):
        return _skip(f"重複委託：{code} {action} 今日已下單")

    # Guard 1b: 跨行程原子宣告——**只擋買進**
    #
    # main.py、telegram_bot.py、FastAPI backend 是三個獨立行程，彼此看不到
    # 對方送出的委託。上面那個記憶體判斷靠呼叫端自己傳 prior_orders，實際上
    # 只有波段路徑有傳，其餘等於沒開。
    #
    # 賣出不擋：重複賣出券商會拒（現股不能賣超過庫存），但**被擋住的賣出**
    # 會讓部位留到隔天變成交割義務——與 halt.py 同一個道理，緊急機制只能
    # 擋「進」不能擋「出」。
    _claimed = False
    if action == "buy":
        try:
            import order_ledger
            kw = {"db_path": ledger_db_path} if ledger_db_path else {}
            if not order_ledger.claim(code, action, note=name, **kw):
                return _skip(f"重複委託：{code} 今日買單已由其他流程送出")
            _claimed = True
        except Exception as e:
            # 帳本壞掉（磁碟滿、權限）不得癱瘓交易：去重是輔助機制，
            # 擋住所有下單的代價高於漏擋一次重複。
            log.warning("下單帳本不可用（放行）: %s", e)

    # Guard 2: lot type + quantity
    lot_type = calc_lot_type(budget, price)
    quantity = calc_quantity(budget, price, lot_type)

    if quantity <= 0:
        return _skip("預算不足，無法下單任何數量", lt=lot_type)

    # Guard 3: hard limit
    multiplier = SHARES_PER_LOT if lot_type == "common" else 1
    amount = quantity * price * multiplier
    if not check_hard_limit(amount, hard_limit):
        return _skip(f"超過硬性金額上限 {hard_limit:,.0f}，委託金額 {amount:,.0f}", qty=quantity, amt=amount, lt=lot_type)

    # Paper trading：記錄但不送券商
    if paper_trading:
        log.info("[PAPER] %s %s %s %d%s @%.2f 金額%.0f（模擬下單，未送券商）",
                 code, name, action, quantity,
                 "張" if lot_type == "common" else "股",
                 price, amount)
        # 紙上模式也要 confirm：去重本來就有效（宣告已存在），但不 confirm
        # 的話帳本會停在 claimed，看起來像「宣告了卻沒下單」，事後追查誤導人。
        if _claimed:
            try:
                import order_ledger
                kw = {"db_path": ledger_db_path} if ledger_db_path else {}
                order_ledger.confirm(code, action, order_id="PAPER-ORDER", **kw)
            except Exception as e:
                log.warning("下單帳本 confirm 失敗（紙上模式）: %s", e)

        try:
            from notifier import notify_order_success
            notify_order_success(code, name, action, quantity, price, amount, lot_type, "PAPER-ORDER")
        except Exception:
            pass
        return ExecutionResult(
            code=code, name=name, action=action,
            quantity=quantity, price=price, amount=amount,
            lot_type=lot_type, success=True,
            order_id=f"PAPER-{date.today().strftime('%Y%m%d')}-{code}",
            reason="[PAPER TRADING] 模擬下單",
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
        if _claimed:
            try:
                import order_ledger
                kw = {"db_path": ledger_db_path} if ledger_db_path else {}
                order_ledger.confirm(code, action, order_id=order_id, **kw)
            except Exception as e:
                log.warning("下單帳本 confirm 失敗（不影響委託）: %s", e)

        result = ExecutionResult(
            code=code, name=name, action=action,
            quantity=quantity, price=price, amount=amount,
            lot_type=lot_type, success=True,
            order_id=order_id, reason="",
        )
        try:
            from notifier import notify_order_success
            notify_order_success(code, name, action, quantity, price, amount, lot_type, order_id)
        except Exception:
            pass
        return result

    except Exception as e:
        # 釋放宣告，讓之後可以重試。不釋放的話，一次網路錯誤就會讓這一檔
        # 今天再也不能下單——而使用者只會看到「重複委託」這個錯誤訊息。
        if _claimed:
            try:
                import order_ledger
                kw = {"db_path": ledger_db_path} if ledger_db_path else {}
                order_ledger.release(code, action, **kw)
            except Exception as re:
                log.warning("下單帳本 release 失敗: %s", re)

        log.error("委託失敗 %s: %s", code, e)
        result = ExecutionResult(
            code=code, name=name, action=action,
            quantity=quantity, price=price, amount=amount,
            lot_type=lot_type, success=False,
            order_id=None, reason=str(e),
        )
        try:
            from notifier import notify_order_skipped
            notify_order_skipped(code, name, str(e))
        except Exception:
            pass
        return result


# ── Force stop-loss ───────────────────────────────────────────────────────────

def force_stop_loss(
    api,
    code: str,
    name: str,
    quantity: int,
    lot_type: str = "common",
    paper_trading: bool | None = None,
) -> bool:
    """Send a market-price sell order for stop-loss / force-close.

    Returns
    -------
    True   — api.place_order() accepted the request.
             This means the **order was submitted to the broker**, nothing more.
             It does NOT mean the order was filled, partially filled, or filled
             at any particular price.  Fill confirmation requires a separate
             mechanism (order-status callbacks, polling, or end-of-day settlement).
    False  — an exception occurred before the order reached the broker.

    Callers MUST NOT treat a True return as evidence of a confirmed trade.
    Write a ``force_close_requested`` record (not a ``sell`` trade) to reflect
    the actual semantic: order submitted, fill unknown.
    """
    if paper_trading is None:
        paper_trading = os.getenv("PAPER_TRADING", "true").lower() != "false"
    if paper_trading:
        log.info("[PAPER] force_stop_loss: %s %s %d%s 市價賣出（模擬，未送券商）",
                 code, name, quantity, "張" if lot_type == "common" else "股")
        return True

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
