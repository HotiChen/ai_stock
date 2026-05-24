from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.backtest import BacktestParams, BacktestResult
from ..schemas.base import LotType, Side
from ..schemas.order import Trade

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

_MOCK_TRADES_SAMPLE = [
    Trade(
        id=1,
        time="09:10:00",
        date="2026-01-03",
        code="2330",
        name="台積電",
        side=Side.BUY,
        quantity=1,
        price=850,
        amount=85_000,
        lot=LotType.COMMON,
        status="filled",
        pnl=4200,
        reason="目標價達成",
        order_id="sim-001",
        sector="半導體",
    ),
    Trade(
        id=2,
        time="10:30:00",
        date="2026-01-05",
        code="2454",
        name="聯發科",
        side=Side.SELL,
        quantity=1,
        price=1050,
        amount=105_000,
        lot=LotType.COMMON,
        status="filled",
        pnl=-3200,
        reason="停損觸發",
        order_id="sim-002",
        sector="半導體",
    ),
]


def _mock_backtest_result(params: BacktestParams) -> BacktestResult:
    return BacktestResult(
        id="bt-2026-05-24-0001",
        range={"start": params.start, "end": params.end},
        initial_capital=params.initial_capital,
        slippage=params.slippage,
        strategy=params.strategy,
        trades=48,
        wins=33,
        losses=15,
        winrate=0.6875,
        total_pnl=124_500,
        total_pnl_pct=0.1245,
        avg_win=6800,
        avg_loss=-3200,
        sharpe=1.42,
        max_dd=-0.087,
        beat_index_pct=0.042,
        monthly_returns=[0.03, -0.01, 0.04, 0.02, 0.05, 0.01, -0.02, 0.03, 0.04, 0.02, 0.01, 0.03],
        equity_curve=[
            {"date": "2026-01-03", "me": 1.0, "index": 1.0},
            {"date": "2026-02-03", "me": 1.03, "index": 1.02},
            {"date": "2026-03-03", "me": 1.02, "index": 1.01},
            {"date": "2026-04-03", "me": 1.06, "index": 1.03},
            {"date": "2026-05-03", "me": 1.08, "index": 1.04},
            {"date": "2026-05-24", "me": 1.1245, "index": 1.05},
        ],
        trades_sample=_MOCK_TRADES_SAMPLE,
        ai_conclusion="回測期間績效良好，勝率 68.75%，夏普值 1.42，超越大盤指數 4.2%。建議持續執行此策略。",
    )


@router.post("/", response_model=BacktestResult)
async def run_backtest(
    params: BacktestParams,
    current_user: User = Depends(get_current_user),
) -> BacktestResult:
    return _mock_backtest_result(params)


@router.get("/{backtest_id}", response_model=BacktestResult)
async def get_backtest(
    backtest_id: str,
    current_user: User = Depends(get_current_user),
) -> BacktestResult:
    dummy_params = BacktestParams(
        start="2026-01-01",
        end="2026-05-24",
        strategy="ai_momentum",
    )
    return _mock_backtest_result(dummy_params)
