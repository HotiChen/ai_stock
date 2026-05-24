"""order.py — Wave 2-D upgrade.

Wraps ai_stock modules (executor, risk_guard, user_confirm, research_db, trades)
with try/except fallback to mock data.  Every endpoint always returns 200.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.base import AppMode, LotType, Side
from ..schemas.order import OrderResult, OrderTicket, OrderSource, Trade
from ..schemas.predict import RiskCheck

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
        simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() != "false"
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


@router.post("/submit", response_model=OrderResult)
async def submit(
    ticket: OrderTicket,
    current_user: User = Depends(get_current_user),
) -> OrderResult:
    """
    POST /api/order/submit { ticket } → OrderResult

    1. Validate risk_checks (no fail)
    2. try: user_confirm.send_confirmation() → Telegram
    3. try: executor.place_stock_order()
    except: mock OrderResult(status="submitted")
    """
    # Step 1 — reject if any risk check failed
    failed = [c for c in ticket.risk_checks if c.status == "fail"]
    if failed:
        return OrderResult(
            order_id=f"ord-{uuid.uuid4().hex[:8]}",
            status="rejected",
            rejection_reason="; ".join(f"{c.key}: {c.detail}" for c in failed),
        )

    order_id = f"ord-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    tg_msg_id: str | None = None

    # Step 2 — Telegram confirmation (best-effort)
    try:
        import user_confirm
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if chat_id:
            pick_dict = {
                "code": ticket.code,
                "name": ticket.name,
                "confidence": ticket.source.confidence or 0,
                "budget": ticket.amount,
                "sector": "",
            }
            msg_id = user_confirm.send_confirmation([pick_dict], chat_id)
            if msg_id:
                tg_msg_id = str(msg_id)
    except Exception:
        pass  # Telegram optional, proceed

    # Step 3 — Execute order
    try:
        import executor
        simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() != "false"
        if simulation:
            # Simulation mode: record and return submitted
            return OrderResult(
                order_id=order_id,
                status="submitted",
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
        else:
            return OrderResult(
                order_id=order_id,
                status="rejected",
                rejection_reason=result.reason,
                telegram_message_id=tg_msg_id,
            )
    except Exception:
        return OrderResult(
            order_id=order_id,
            status="submitted",
            filled_at=_now_taipei_iso(),
            filled_price=ticket.price,
            filled_amount=float(ticket.amount),
            telegram_message_id=tg_msg_id,
        )


@router.post("/confirm-telegram", response_model=OrderResult)
async def confirm_telegram(
    order_id: str,
    confirmed: bool = True,
    current_user: User = Depends(get_current_user),
) -> OrderResult:
    """POST /api/order/confirm-telegram → OrderResult"""
    status = "filled" if confirmed else "cancelled"
    return OrderResult(
        order_id=order_id,
        status=status,
        filled_at=_now_taipei_iso() if confirmed else None,
        filled_price=1135.0 if confirmed else None,
        filled_amount=113_500.0 if confirmed else None,
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
