from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.base import AlertKind, AlertLevel, LotType, Side, ThreadState
from ..schemas.daytrade import (
    Alert,
    ChartView,
    DaytradeLive,
    Position,
    RiskCockpit,
    SingleMax,
    StrategyThread,
    AIMark,
)
from ..schemas.predict import SectorAllocation, Tick

router = APIRouter(prefix="/api/daytrade", tags=["daytrade"])


def _mock_positions() -> list[Position]:
    return [
        Position(
            code="2330",
            name="台積電",
            sector="半導體",
            side=Side.BUY,
            entry_price=1120,
            last_price=1135,
            quantity=1,
            lot=LotType.COMMON,
            cost=112_000,
            market_value=113_500,
            pnl=1500,
            pnl_pct=0.0134,
            target_price=1185,
            stop_loss_price=1085,
            distance_to_tp_pct=0.044,
            distance_to_sl_pct=0.044,
            thread_state=ThreadState.MONITORING,
            confidence=0.82,
            opened_at="2026-05-24T09:05:32+08:00",
        ),
        Position(
            code="2454",
            name="聯發科",
            sector="半導體",
            side=Side.BUY,
            entry_price=1295,
            last_price=1315,
            quantity=1,
            lot=LotType.COMMON,
            cost=129_500,
            market_value=131_500,
            pnl=2000,
            pnl_pct=0.0154,
            target_price=1380,
            stop_loss_price=1260,
            distance_to_tp_pct=0.049,
            distance_to_sl_pct=0.042,
            thread_state=ThreadState.MONITORING,
            confidence=0.76,
            opened_at="2026-05-24T09:12:18+08:00",
        ),
    ]


def _mock_alerts() -> list[Alert]:
    return [
        Alert(
            id="alert-001",
            time="10:41:55",
            level=AlertLevel.HIGH,
            code="2330",
            name="台積電",
            text="台積電達到目標價 1185，建議評估獲利了結",
            kind=AlertKind.TARGET_HIT,
            resolved=False,
            source="MonitorAgent",
            telegram_sent=True,
        ),
        Alert(
            id="alert-002",
            time="10:25:12",
            level=AlertLevel.MED,
            code="2454",
            name="聯發科",
            text="聯發科接近停損價 1260，目前距離 4.2%",
            kind=AlertKind.STOP_WARN,
            resolved=False,
            source="MonitorAgent",
            telegram_sent=False,
        ),
    ]


def _mock_threads() -> list[StrategyThread]:
    return [
        StrategyThread(
            code="2330",
            name="台積電",
            state=ThreadState.MONITORING,
            last_tick_at="2026-05-24T10:45:00+08:00",
            age_seconds=5988,
            target_price=1185,
            stop_loss_price=1085,
            distance_label="+4.4% / -4.4%",
            poll_count=200,
            alert_count=1,
        ),
        StrategyThread(
            code="2454",
            name="聯發科",
            state=ThreadState.MONITORING,
            last_tick_at="2026-05-24T10:45:00+08:00",
            age_seconds=5562,
            target_price=1380,
            stop_loss_price=1260,
            distance_label="+4.9% / -4.2%",
            poll_count=186,
            alert_count=1,
        ),
    ]


def _mock_risk() -> RiskCockpit:
    return RiskCockpit(
        budget=1_000_000,
        used=245_000,
        free=755_000,
        utilization=0.245,
        intraday_pnl=3500,
        intraday_pnl_pct=0.0035,
        daily_max_dd_limit=-0.03,
        sector_allocation=[
            SectorAllocation(name="半導體", ratio=0.245, limit=0.4, value=245_000),
        ],
        blacklist=["1234"],
        single_max=SingleMax(value=131_500, ratio=0.1315, limit=0.2, ok=True),
    )


def _mock_daytrade_live() -> DaytradeLive:
    return DaytradeLive(
        countdown_seconds=9900,
        force_close_at="13:25:00",
        monitoring_count=2,
        closed_count=0,
        unrealized_pnl=3500,
        realized_pnl=0,
        net_pnl=3500,
        net_value=1_003_500,
        positions=_mock_positions(),
        alerts=_mock_alerts(),
        threads=_mock_threads(),
        risk=_mock_risk(),
        next_poll_in_seconds=30,
    )


def _mock_chart_view(code: str) -> ChartView:
    ticks = [
        Tick(t="09:00", open=1120, high=1122, low=1118, close=1121, volume=1250),
        Tick(t="09:30", open=1121, high=1128, low=1120, close=1126, volume=1480),
        Tick(t="10:00", open=1126, high=1135, low=1125, close=1132, volume=1820),
        Tick(t="10:30", open=1132, high=1142, low=1130, close=1135, volume=2100),
    ]
    closes = [t.close for t in ticks]
    return ChartView(
        code=code,
        ticks=ticks,
        ma20=[1098.0, 1100.0, 1105.0, 1110.0],
        ma5=[1120.0, 1122.5, 1127.0, 1131.0],
        bollinger={
            "upper": [1150.0, 1152.0, 1155.0, 1158.0],
            "mid": [1110.0, 1112.0, 1115.0, 1118.0],
            "lower": [1070.0, 1072.0, 1075.0, 1078.0],
        },
        rsi=[52.0, 55.0, 58.0, 60.0],
        ai_marks=[
            AIMark(
                index=0,
                time="09:05",
                kind="buy",
                label="AI 買進信號",
                confidence=0.82,
                reasoning="突破前高，量能放大",
            )
        ],
        next_action_suggestion={
            "kind": "hold",
            "text": "持續持有，目標 1185 仍有 4.4% 空間",
            "confidence": 0.75,
        },
    )


@router.get("/live", response_model=DaytradeLive)
async def live(current_user: User = Depends(get_current_user)) -> DaytradeLive:
    return _mock_daytrade_live()


@router.get("/{code}/chart", response_model=ChartView)
async def chart(code: str, current_user: User = Depends(get_current_user)) -> ChartView:
    return _mock_chart_view(code)


@router.post("/close-all")
async def close_all(current_user: User = Depends(get_current_user)) -> dict:
    return {"ok": True, "message": "平倉指令已送出（mock）"}


@router.post("/close")
async def close(code: str, current_user: User = Depends(get_current_user)) -> dict:
    return {"ok": True, "code": code, "message": f"{code} 平倉指令已送出（mock）"}


@router.post("/adjust-tp")
async def adjust_tp(code: str, value: float, current_user: User = Depends(get_current_user)) -> dict:
    return {"ok": True, "code": code, "target_price": value}


@router.post("/adjust-sl")
async def adjust_sl(code: str, value: float, current_user: User = Depends(get_current_user)) -> dict:
    return {"ok": True, "code": code, "stop_loss_price": value}


@router.post("/mute")
async def mute(minutes: int, current_user: User = Depends(get_current_user)) -> dict:
    return {"ok": True, "muted_minutes": minutes}
