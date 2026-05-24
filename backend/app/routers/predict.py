from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User
from ..schemas.base import Signal
from ..schemas.predict import (
    Contribution,
    DeepAnalysis,
    Indicator,
    Pick,
    ReasoningTrace,
    RiskCheck,
    Scenario,
    SectorAllocation,
    SelfCheck,
    Tick,
    TopNRun,
    TraceStep,
)

router = APIRouter(prefix="/api/predict", tags=["predict"])

_MOCK_PICKS = [
    Pick(
        code="2330",
        name="台積電",
        sector="半導體",
        signal=Signal.BUY,
        confidence=0.82,
        target_price=1185,
        stop_loss_price=1085,
        last_price=1135,
        change_pct=1.34,
        spark=[1080, 1090, 1095, 1100, 1110, 1120, 1125, 1130, 1132, 1135],
        reason="外資連 5 日買超，技術面突破季線，AI 信心高",
        tags=["黃金交叉", "量比 1.8x", "外資買超"],
        action="approved",
        budget=200_000,
        budget_ratio=0.2,
        run_id="plan-2026-05-24-0001",
        created_at="2026-05-24T08:30:00+08:00",
    ),
    Pick(
        code="2454",
        name="聯發科",
        sector="半導體",
        signal=Signal.BUY,
        confidence=0.76,
        target_price=1380,
        stop_loss_price=1260,
        last_price=1315,
        change_pct=0.84,
        spark=[1250, 1265, 1280, 1290, 1295, 1300, 1305, 1308, 1312, 1315],
        reason="AI 晶片需求強勁，法人持續買超，RSI 回測支撐",
        tags=["AI 題材", "法人買超", "RSI 回測"],
        action="approved",
        budget=160_000,
        budget_ratio=0.16,
        run_id="plan-2026-05-24-0001",
        created_at="2026-05-24T08:30:00+08:00",
    ),
    Pick(
        code="2382",
        name="廣達",
        sector="電腦硬體",
        signal=Signal.BUY,
        confidence=0.71,
        target_price=295,
        stop_loss_price=268,
        last_price=280,
        change_pct=2.19,
        spark=[255, 260, 265, 268, 272, 275, 277, 279, 280, 280],
        reason="AI 伺服器訂單能見度高，量能放大突破",
        tags=["AI 伺服器", "量能放大"],
        action="pending",
        budget=140_000,
        budget_ratio=0.14,
        run_id="plan-2026-05-24-0001",
        created_at="2026-05-24T08:30:00+08:00",
    ),
]

_MOCK_RISK_CHECKS = [
    RiskCheck(key="重複委託防護", sub="executor.is_duplicate_order()", status="pass", detail="無重複委託"),
    RiskCheck(key="單股部位上限", sub="risk_guard.single_stock_ratio()", status="pass", detail="≤ 20%"),
    RiskCheck(key="板塊集中度", sub="risk_guard.sector_ratio()", status="pass", detail="半導體 36% < 40%"),
    RiskCheck(key="信心門檻", sub="risk_guard.confidence_threshold()", status="pass", detail="≥ 0.65"),
    RiskCheck(key="黑名單篩查", sub="risk_guard.blacklist_check()", status="pass", detail="無黑名單股票"),
    RiskCheck(key="每日損失上限", sub="risk_guard.daily_max_loss()", status="pass", detail="未觸及 -3%"),
]

_MOCK_TOP_N_RUN = TopNRun(
    run_id="plan-2026-05-24-0001",
    date="2026-05-24",
    scanned=450,
    analyzed=12,
    buy_signals=8,
    hold_signals=4,
    approved=3,
    rejected_by_risk=1,
    picks=_MOCK_PICKS,
    risk_checks=_MOCK_RISK_CHECKS,
    sector_allocation=[
        SectorAllocation(name="半導體", ratio=0.36, limit=0.4, value=360_000),
        SectorAllocation(name="電腦硬體", ratio=0.14, limit=0.4, value=140_000),
    ],
    blacklist=["1234"],
    cost={
        "duration_ms": 18420,
        "input_tokens": 12480,
        "output_tokens": 3820,
        "cost_usd": 0.048,
        "model": "claude-haiku-4-5-20251001",
    },
    telegram_sent_at="2026-05-24T08:31:45+08:00",
)


def _mock_deep_analysis(code: str) -> DeepAnalysis:
    return DeepAnalysis(
        code=code,
        name="台積電" if code == "2330" else code,
        sector="半導體",
        last=1135,
        change=15,
        change_pct=0.0134,
        open=1120,
        high=1142,
        low=1118,
        prev_close=1120,
        volume_lots=28450,
        signal=Signal.BUY,
        confidence=0.82,
        target_price=1185,
        stop_loss_price=1085,
        expected_return=0.044,
        max_loss=-0.044,
        risk_reward="1 : 1.33",
        budget=200_000,
        indicators=[
            Indicator(key="MA5", value=1126.4, hint="上彎", weight=2.0, signal="bull"),
            Indicator(key="MA20", value=1098.2, hint="多頭排列", weight=2.0, signal="bull"),
            Indicator(key="RSI(14)", value=58.3, hint="強勢區", weight=1.0, signal="bull"),
            Indicator(key="MACD", value=12.4, hint="正乖離擴大", weight=2.0, signal="bull"),
            Indicator(key="布林上軌", value="上方", hint="強勢突破", weight=1.0, signal="bull"),
            Indicator(key="KD", value="中軌", hint="鈍化整理", weight=0.0, signal="neutral"),
        ],
        total_score=8.0,
        recommendation="強力買進",
        scenarios=[
            Scenario(name="基準情境", probability=0.55, target_price=1185, return_pct=0.044, description="維持強勢上漲趨勢"),
            Scenario(name="樂觀情境", probability=0.25, target_price=1230, return_pct=0.083, description="外資加碼推升"),
            Scenario(name="保守情境", probability=0.20, target_price=1095, return_pct=-0.035, description="獲利了結賣壓"),
        ],
        ai_conclusion="台積電技術面強勢，外資連續買超，AI 晶片需求持續，建議買進，目標價 1185，停損 1085。",
        ticks=[
            Tick(t="09:00", open=1120, high=1122, low=1118, close=1121, volume=1250),
            Tick(t="09:30", open=1121, high=1128, low=1120, close=1126, volume=1480),
            Tick(t="10:00", open=1126, high=1135, low=1125, close=1132, volume=1820),
            Tick(t="10:30", open=1132, high=1142, low=1130, close=1135, volume=2100),
        ],
        model="claude-haiku-4-5-20251001",
        generated_at="2026-05-24T08:30:00+08:00",
    )


def _mock_reasoning_trace(code: str) -> ReasoningTrace:
    return ReasoningTrace(
        run_id="plan-2026-05-24-0001",
        code=code,
        total_duration_ms=18420,
        steps=[
            TraceStep(phase="INPUT", t="08:30:00", label="載入輸入資料", body="code=2330, date=2026-05-24", cost_ms=12.0),
            TraceStep(phase="FETCH", t="08:30:01", label="抓取技術指標", body="MA5=1126.4, MA20=1098.2, RSI=58.3", cost_ms=340.0),
            TraceStep(phase="EVAL", t="08:30:02", label="計算技術評分", body="total_score=8.0 (6 indicators)", cost_ms=28.0),
            TraceStep(phase="PROMPT", t="08:30:02", label="組建 AI Prompt", body="system+user prompt 建構完成 (12480 tokens)", cost_ms=15.0),
            TraceStep(phase="LLM", t="08:30:03", label="LLM 推理", body="claude-haiku-4-5-20251001 呼叫中...", cost_ms=14200.0, cost_usd=0.048),
            TraceStep(phase="PARSE", t="08:30:17", label="解析回應", body="signal=buy, confidence=0.82", cost_ms=8.0),
            TraceStep(phase="GUARD", t="08:30:17", label="風控驗證", body="6 checks all passed", cost_ms=45.0),
            TraceStep(phase="OUTPUT", t="08:30:18", label="輸出結果", body="Pick 寫入 research_db", cost_ms=12.0),
        ],
        prompt={
            "system": "你是台股 AI 量化分析師...",
            "user": "分析台積電(2330)當沖機會...",
            "tokens_in": 12480,
        },
        response={
            "raw": '{"signal":"buy","confidence":0.82,"target_price":1185,...}',
            "parsed": {
                "signal": "buy",
                "confidence": 0.82,
                "target_price": 1185,
                "stop_loss_price": 1085,
                "reason": "技術面強勢，外資買超，AI 晶片需求持續",
            },
            "tokens_out": 3820,
        },
        contributions=[
            Contribution(key="技術指標總分", detail="+8 分", delta=0.35, kind="positive"),
            Contribution(key="外資買超", detail="+5 日連買", delta=0.25, kind="positive"),
            Contribution(key="AI 題材", detail="AI 晶片訂單強勁", delta=0.15, kind="positive"),
            Contribution(key="基礎信心", detail="底線信心", delta=0.07, kind="base"),
        ],
        final_confidence=0.82,
        self_check=[
            SelfCheck(question="技術面是否支持買入?", answer="是，MA多頭排列，RSI強勢區", passed=True),
            SelfCheck(question="風控是否通過?", answer="6項均通過", passed=True),
            SelfCheck(question="信心是否達門檻?", answer="0.82 > 0.65", passed=True),
        ],
        decision_hash="sha256:a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5",
    )


@router.get("/today", response_model=TopNRun)
async def today(current_user: User = Depends(get_current_user)) -> TopNRun:
    return _MOCK_TOP_N_RUN


@router.get("/run/{run_id}", response_model=TopNRun)
async def get_run(run_id: str, current_user: User = Depends(get_current_user)) -> TopNRun:
    return _MOCK_TOP_N_RUN


@router.post("/run", response_model=TopNRun)
async def trigger_run(current_user: User = Depends(get_current_user)) -> TopNRun:
    return _MOCK_TOP_N_RUN


@router.post("/approve")
async def approve(run_id: str, code: str, current_user: User = Depends(get_current_user)) -> dict:
    return {"ok": True, "code": code, "action": "approved"}


@router.post("/reject")
async def reject(run_id: str, code: str, current_user: User = Depends(get_current_user)) -> dict:
    return {"ok": True, "code": code, "action": "rejected"}


@router.post("/approve-all")
async def approve_all(run_id: str, current_user: User = Depends(get_current_user)) -> TopNRun:
    return _MOCK_TOP_N_RUN


@router.get("/{code}", response_model=DeepAnalysis)
async def deep_analysis(code: str, current_user: User = Depends(get_current_user)) -> DeepAnalysis:
    return _mock_deep_analysis(code)


@router.post("/{code}/rerun", response_model=DeepAnalysis)
async def rerun(code: str, current_user: User = Depends(get_current_user)) -> DeepAnalysis:
    return _mock_deep_analysis(code)


@router.get("/{code}/reasoning", response_model=ReasoningTrace)
async def reasoning(
    code: str,
    run_id: str | None = None,
    current_user: User = Depends(get_current_user),
) -> ReasoningTrace:
    return _mock_reasoning_trace(code)


@router.get("/run/{run_id}/reasoning", response_model=list[ReasoningTrace])
async def run_reasoning(run_id: str, current_user: User = Depends(get_current_user)) -> list[ReasoningTrace]:
    return [_mock_reasoning_trace("2330"), _mock_reasoning_trace("2454")]
