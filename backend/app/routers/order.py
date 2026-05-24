from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.base import AppMode, LotType, Side
from ..schemas.order import OrderResult, OrderTicket, OrderSource, Trade
from ..schemas.predict import RiskCheck

router = APIRouter(prefix="/api/order", tags=["order"])

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


@router.post("/preview", response_model=OrderTicket)
async def preview(
    code: str = "2330",
    current_user: User = Depends(get_current_user),
) -> OrderTicket:
    return _mock_ticket(code)


@router.post("/submit", response_model=OrderResult)
async def submit(
    ticket: OrderTicket,
    current_user: User = Depends(get_current_user),
) -> OrderResult:
    return OrderResult(
        order_id="ord-2026-05-24-0001",
        status="submitted",
        filled_at="2026-05-24T09:05:32+08:00",
        filled_price=1135.0,
        filled_amount=113_500.0,
        telegram_message_id="tg-12345",
    )


@router.post("/confirm-telegram", response_model=OrderResult)
async def confirm_telegram(
    order_id: str,
    confirmed: bool = True,
    current_user: User = Depends(get_current_user),
) -> OrderResult:
    status = "filled" if confirmed else "cancelled"
    return OrderResult(
        order_id=order_id,
        status=status,
        filled_at="2026-05-24T09:05:45+08:00" if confirmed else None,
        filled_price=1135.0 if confirmed else None,
        filled_amount=113_500.0 if confirmed else None,
    )


@router.get("/today", response_model=list[Trade])
async def today_trades(current_user: User = Depends(get_current_user)) -> list[Trade]:
    return [
        Trade(
            id=1,
            time="09:05:32",
            date="2026-05-24",
            code="2330",
            name="台積電",
            side=Side.BUY,
            quantity=1,
            price=1135,
            amount=113_500,
            lot=LotType.COMMON,
            status="filled",
            order_id="ord-2026-05-24-0001",
            sector="半導體",
        ),
    ]
