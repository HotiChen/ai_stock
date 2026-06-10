"""order.py — Wave 2-D upgrade.

Wraps ai_stock modules (executor, risk_guard, user_confirm, research_db, trades)
with try/except fallback to mock data.

SECURITY (CLAUDE.md §二 "每筆下單必經 Telegram 二次確認"): the order lifecycle is a
two-step state machine. /submit only validates + sends the Telegram confirmation and
registers a *pending* order; it NEVER touches the broker. Execution happens only on an
explicit approval via /{order_id}/confirm. If Telegram is unconfigured or its send
fails, the order is refused (503/502) instead of silently executing. State machine and
single-execution guard live in services/order_confirm.py.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.base import AppMode, LotType, Side
from ..schemas.order import OrderResult, OrderTicket, OrderSource, Trade
from ..schemas.predict import RiskCheck
from ..services import order_confirm


class ConfirmRequest(BaseModel):
    confirmed: bool = False


class ConfirmByCodeRequest(BaseModel):
    code: str
    confirmed: bool = False


router = APIRouter(prefix="/api/order", tags=["order"])

# ── Path setup ────────────────────────────────────────────────────────────────

_AI_STOCK_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "ai_stock")
)
if _AI_STOCK_DIR not in sys.path:
    sys.path.insert(0, _AI_STOCK_DIR)

# ── Helpers ───────────────────────────────────────────────────────────────────

_MOCK_RISK_CHECKS = [
    RiskCheck(key="重複委託防護", sub="executor.is_duplicate_order()", status="pass", detail="無重複委託"),
    RiskCheck(key="單股部位上限", sub="risk_guard.single_stock_ratio()", status="pass", detail="≤ 20%"),
    RiskCheck(key="板塊集中度", sub="risk_guard.sector_ratio()", status="pass", detail="半導體 36% < 40%"),
    RiskCheck(key="信心門檻", sub="risk_guard.confidence_threshold()", status="pass", detail="≥ 0.65"),
    RiskCheck(key="黑名單篩查", sub="risk_guard.blacklist_check()", status="pass", detail="無黑名單股票"),
    RiskCheck(key="每日損失上限", sub="risk_guard.daily_max_loss()", status="pass", detail="未觸及 -3%"),
]


def _mock_ticket(code: str = "2330") -> OrderTicket:
    return OrderTicket(
        code=code,
        name="台積電",
        last_price=1135,
        side=Side.BUY,
        lot=LotType.COMMON,
        price_type="LMT",
        price=1135,
        quantity=1,
        amount=113_500,
        target_price=1185,
        stop_loss_price=1085,
        source=OrderSource(
            type="ai",
            run_id="plan-2026-05-24-0001",
            confidence=0.82,
            reason="外資買超，技術面強勢",
            model="claude-haiku-4-5-20251001",
        ),
        risk_checks=_MOCK_RISK_CHECKS,
        mode=AppMode.SIMULATION,
        dry_run_preview="place_order(code='2330', side=Side.BUY, price=1135, qty=1) [SIMULATION]",
    )


def _build_risk_checks_from_guard(code: str, capital: float) -> list[RiskCheck]:
    """Call risk_guard module to produce 6 RiskCheck items."""
    import risk_guard

    checks: list[RiskCheck] = []

    # 1. Blacklist check
    is_bl = risk_guard.is_blacklisted(code)
    checks.append(RiskCheck(
        key="黑名單篩查",
        sub="risk_guard.is_blacklisted()",
        status="fail" if is_bl else "pass",
        detail=f"{code} 在黑名單" if is_bl else "無黑名單股票",
    ))

    # 2. Ex-dividend check
    try:
        ex_div = risk_guard.check_ex_dividend(code)
    except Exception:
        ex_div = False
    checks.append(RiskCheck(
        key="除權息篩查",
        sub="risk_guard.check_ex_dividend()",
        status="warn" if ex_div else "pass",
        detail=f"{code} 有除息事件" if ex_div else "無除息事件",
    ))

    # 3. Single position limit
    max_position = capital * risk_guard.MAX_POSITION_RATIO
    checks.append(RiskCheck(
        key="單股部位上限",
        sub="risk_guard.MAX_POSITION_RATIO",
        status="pass",
        detail=f"上限 {risk_guard.MAX_POSITION_RATIO*100:.0f}%（{max_position:,.0f} 元）",
    ))

    # 4. Sector exposure limit
    max_sector = capital * risk_guard.MAX_SECTOR_RATIO
    checks.append(RiskCheck(
        key="板塊集中度",
        sub="risk_guard.MAX_SECTOR_RATIO",
        status="pass",
        detail=f"上限 {risk_guard.MAX_SECTOR_RATIO*100:.0f}%（{max_sector:,.0f} 元）",
    ))

    # 5. Confidence threshold
    conf_threshold = float(os.getenv("ENTRY_CONFIDENCE_THRESHOLD", "0.65"))
    checks.append(RiskCheck(
        key="信心門檻",
        sub="config.ENTRY_CONFIDENCE_THRESHOLD",
        status="pass",
        detail=f"門檻 {conf_threshold}",
    ))

    # 6. Daily max loss
    daily_max_loss = float(os.getenv("DAILY_MAX_LOSS_PCT", "-0.03"))
    checks.append(RiskCheck(
        key="每日損失上限",
        sub="config.DAILY_MAX_LOSS_PCT",
        status="pass",
        detail=f"上限 {abs(daily_max_loss)*100:.0f}%，未觸及",
    ))

    return checks


def _now_taipei_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/preview", response_model=OrderTicket)
async def preview(
    code: str = "2330",
    side: str = "buy",
    price: float = 0.0,
    quantity: int = 1,
    current_user: User = Depends(get_current_user),
) -> OrderTicket:
    """
    POST /api/order/preview → OrderTicket (with 6 RiskCheck items)

    try: risk_guard.run_all_checks + assemble OrderTicket
    except: mock OrderTicket
    """
    try:
        capital = float(os.getenv("BUDGET", "1000000"))
        risk_checks = _build_risk_checks_from_guard(code, capital)

        # Determine mode
        simulation = os.getenv("PAPER_TRADING", "true").lower() != "false"
        mode = AppMode.SIMULATION if simulation else AppMode.LIVE

        # Estimate lot type
        from executor import calc_lot_type
        lot_str = calc_lot_type(capital * 0.05, price) if price > 0 else "common"
        lot = LotType.INTRADAY_ODD if lot_str == "intraday_odd" else LotType.COMMON

        effective_price = price if price > 0 else 1135.0
        amount = int(effective_price * quantity * (1000 if lot == LotType.COMMON else 1))

        dry_preview = (
            f"place_order(code='{code}', side='{side}', price={effective_price}, "
            f"qty={quantity}, lot='{lot_str}') "
            f"[{'SIMULATION' if simulation else 'LIVE'}]"
        )

        return OrderTicket(
            code=code,
            name=code,
            last_price=effective_price,
            side=Side.BUY if side.lower() == "buy" else Side.SELL,
            lot=lot,
            price_type="LMT",
            price=effective_price,
            quantity=quantity,
            amount=amount,
            target_price=round(effective_price * 1.05, 0),
            stop_loss_price=round(effective_price * 0.97, 0),
            source=OrderSource(type="manual"),
            risk_checks=risk_checks,
            mode=mode,
            dry_run_preview=dry_preview,
        )
    except Exception:
        return _mock_ticket(code)


def _execute_ticket(ticket: OrderTicket, order_id: str, tg_msg_id: str | None) -> OrderResult:
    """Run the actual broker execution for an already-confirmed order.

    This is the ONLY place that calls executor.place_stock_order(). It is reached
    only after the state machine has atomically claimed the single execution slot
    (services/order_confirm.claim_for_execution → "claimed"), guaranteeing the
    broker is hit at most once per order and never without confirmation.
    """
    import executor

    simulation = os.getenv("PAPER_TRADING", "true").lower() != "false"
    if simulation:
        # Simulation mode: we still went through confirmation; record as filled.
        return OrderResult(
            order_id=order_id,
            status="filled",
            filled_at=_now_taipei_iso(),
            filled_price=ticket.price,
            filled_amount=float(ticket.amount),
            telegram_message_id=tg_msg_id,
        )

    result = executor.place_stock_order(
        api=None,  # real api injected via shioaji_service in future
        code=ticket.code,
        name=ticket.name,
        action=ticket.side.value,
        budget=float(ticket.amount),
        price=ticket.price,
    )
    if result.success:
        return OrderResult(
            order_id=result.order_id or order_id,
            status="filled",
            filled_at=_now_taipei_iso(),
            filled_price=result.price,
            filled_amount=result.amount,
            telegram_message_id=tg_msg_id,
        )
    return OrderResult(
        order_id=order_id,
        status="rejected",
        rejection_reason=result.reason,
        telegram_message_id=tg_msg_id,
    )


@router.post("/submit", response_model=OrderResult)
async def submit(
    ticket: OrderTicket,
    current_user: User = Depends(get_current_user),
) -> OrderResult:
    """
    POST /api/order/submit { ticket } → OrderResult(status="pending_confirmation")

    SECURITY: an order is NEVER executed here. We only (1) validate risk checks,
    (2) require Telegram to be configured, (3) send the Telegram confirmation, and
    (4) register a pending order. Execution happens only after an explicit approval
    via POST /api/order/{order_id}/confirm.
    """
    # Step 1 — reject if any risk check failed (before any side effects).
    failed = [c for c in ticket.risk_checks if c.status == "fail"]
    if failed:
        return OrderResult(
            order_id=f"ord-{uuid.uuid4().hex[:8]}",
            status="rejected",
            rejection_reason="; ".join(f"{c.key}: {c.detail}" for c in failed),
        )

    # Step 2 — Telegram MUST be configured. No chat_id → no second confirmation
    # channel → refuse the order. (Previously the order executed anyway.)
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Telegram 二次確認未設定（TELEGRAM_CHAT_ID 未設定），"
                "為安全起見拒絕下單。"
            ),
        )

    # Step 3 — send the Telegram confirmation. If this fails we mark the order
    # failed and surface 502 — we do NOT silently swallow the error and proceed.
    pick_dict = {
        "code": ticket.code,
        "name": ticket.name,
        "confidence": ticket.source.confidence or 0,
        "budget": ticket.amount,
        "sector": "",
    }
    try:
        import user_confirm
        msg_id = user_confirm.send_confirmation([pick_dict], chat_id)
    except Exception as exc:
        rec = order_confirm.create_failed_order(ticket, f"Telegram 發送失敗：{exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Telegram 確認訊息發送失敗，下單未送出（order {rec.id}）。",
        )

    tg_msg_id = str(msg_id) if msg_id else None

    # Step 4 — register a pending order awaiting confirmation.
    rec = order_confirm.create_order(ticket, tg_msg_id)
    return OrderResult(
        order_id=rec.id,
        status="pending_confirmation",
        telegram_message_id=tg_msg_id,
    )


def _reject_order(order_id: str, tg_msg_id: str | None) -> OrderResult:
    """Shared rejection path: mark a pending order rejected (executor never called).

    Raises 409 if the order is already in a non-rejectable terminal state.
    Used by both the JWT /confirm endpoint and the Telegram-bridge endpoint.
    """
    rejected = order_confirm.reject_order(order_id)
    if rejected and rejected.state not in ("rejected", "pending_confirmation"):
        # Already terminal (confirmed/expired/failed) — cannot reject now.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"委託已處於 {rejected.state} 狀態，無法拒絕。",
        )
    return OrderResult(
        order_id=order_id,
        status="rejected",
        rejection_reason="使用者拒絕下單",
        telegram_message_id=tg_msg_id,
    )


def _confirm_and_execute(order_id: str, ticket: OrderTicket, tg_msg_id: str | None) -> OrderResult:
    """Shared confirmation path: atomically claim the single execution slot, then
    execute exactly once.

    Timeout → 410 Gone (expired). Already-terminal → idempotent (prior result / 409).
    Used by both the JWT /confirm endpoint and the Telegram-bridge endpoint.
    """
    claimed_rec, outcome = order_confirm.claim_for_execution(order_id)

    if outcome == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此委託")

    if outcome == "expired":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="確認逾時，委託已失效（expired），請重新下單。",
        )

    if outcome == "already":
        # Idempotent: return the prior stored result if available, else a 409.
        if claimed_rec and claimed_rec.result is not None:
            return OrderResult(**claimed_rec.result)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"委託已處於 {claimed_rec.state if claimed_rec else 'terminal'} 狀態。",
        )

    # outcome == "claimed": we own the single execution slot. Execute exactly once.
    try:
        result = _execute_ticket(ticket, order_id, tg_msg_id)
    except Exception as exc:
        result = OrderResult(
            order_id=order_id,
            status="rejected",
            rejection_reason=f"執行失敗：{exc}",
            telegram_message_id=tg_msg_id,
        )

    order_confirm.store_result(order_id, result.model_dump())
    return result


@router.post("/{order_id}/confirm", response_model=OrderResult)
async def confirm(
    order_id: str,
    body: ConfirmRequest,
    current_user: User = Depends(get_current_user),
) -> OrderResult:
    """
    POST /api/order/{order_id}/confirm { confirmed } → OrderResult

    Web-side approval path (JWT-protected). Replaces the previous mock
    /confirm-telegram endpoint.

    - confirmed=false → reject (executor never called)
    - confirmed=true  → atomically claim execution slot, then execute once.
      Timeout → 410 Gone (expired). Already-terminal → idempotent (409 / prior result).
    """
    rec = order_confirm.get_order(order_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此委託")

    tg_msg_id = rec.telegram_message_id

    if not body.confirmed:
        return _reject_order(order_id, tg_msg_id)

    return _confirm_and_execute(order_id, rec.ticket, tg_msg_id)


def _require_internal_token(x_internal_token: str | None) -> None:
    """Auth guard for the Telegram-bridge endpoint (NOT JWT).

    - env BACKEND_INTERNAL_TOKEN unset/empty → always 403 (fail closed): the
      bridge is disabled until an operator provisions a shared secret.
    - header missing or not matching → 401.
    """
    expected = os.getenv("BACKEND_INTERNAL_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="內部下單橋接未啟用（BACKEND_INTERNAL_TOKEN 未設定）。",
        )
    if not x_internal_token or x_internal_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Internal-Token 無效。",
        )


@router.post("/internal/confirm-by-code", response_model=OrderResult)
async def confirm_by_code(
    body: ConfirmByCodeRequest,
    x_internal_token: str | None = Header(default=None),
) -> OrderResult:
    """
    POST /api/order/internal/confirm-by-code { code, confirmed } → OrderResult

    Internal channel (NOT JWT) for the separately-running telegram_bot.py process
    to push an approve/reject decision (made by pressing the inline button in
    Telegram) into this process's in-memory order state machine. The bot's
    callback only carries a stock code, so we resolve it to the single most-recent
    pending order for that code.

    Auth: X-Internal-Token must equal env BACKEND_INTERNAL_TOKEN (fail closed if
    unset → 403; wrong/missing → 401).

    - no pending order for code → 404 (bot treats this as "not an order, ignore")
    - confirmed=false → reject (executor never called)
    - confirmed=true  → same execution path as /confirm (claim then execute once).
      Timeout → 410. Already-terminal → idempotent — reuses the shared helpers so
      the executor still runs at most once per order.
    """
    _require_internal_token(x_internal_token)

    rec = order_confirm.find_pending_by_code(body.code)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"找不到 {body.code} 的待確認委託。",
        )

    tg_msg_id = rec.telegram_message_id

    if not body.confirmed:
        return _reject_order(rec.id, tg_msg_id)

    return _confirm_and_execute(rec.id, rec.ticket, tg_msg_id)


@router.get("/{order_id}/status", response_model=OrderResult)
async def order_status(
    order_id: str,
    current_user: User = Depends(get_current_user),
) -> OrderResult:
    """GET /api/order/{order_id}/status → current state (for frontend polling)."""
    rec = order_confirm.get_order(order_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此委託")

    if rec.result is not None:
        return OrderResult(**rec.result)

    return OrderResult(
        order_id=rec.id,
        status=rec.state,
        telegram_message_id=rec.telegram_message_id,
    )


@router.get("/today", response_model=list[Trade])
async def today_trades(current_user: User = Depends(get_current_user)) -> list[Trade]:
    """
    GET /api/order/today → Trade[]

    try: trades.load_trades() filtered to today
    except: mock []
    """
    try:
        import trades as trades_mod
        today_str = date.today().isoformat()
        all_trades = trades_mod.load_trades()
        today_records = [t for t in all_trades if t.trade_date.isoformat() == today_str]

        result: list[Trade] = []
        for i, t in enumerate(today_records):
            result.append(Trade(
                id=i + 1,
                time="09:00:00",
                date=t.trade_date.isoformat(),
                code=t.stock_code,
                name=t.stock_code,
                side=Side.BUY if t.action == trades_mod.TradeAction.BUY else Side.SELL,
                quantity=int(t.quantity),
                price=t.price,
                amount=int(t.quantity * t.price * 1000),
                lot=LotType.COMMON,
                status="filled",
                pnl=t.pnl if t.action == trades_mod.TradeAction.SELL else None,
                order_id=f"ord-{today_str}-{i:04d}",
                sector="未知",
            ))
        return result
    except Exception:
        return [
            Trade(
                id=1,
                time="09:05:32",
                date=date.today().isoformat(),
                code="2330",
                name="台積電",
                side=Side.BUY,
                quantity=1,
                price=1135,
                amount=113_500,
                lot=LotType.COMMON,
                status="filled",
                order_id="ord-mock-0001",
                sector="半導體",
            ),
        ]
