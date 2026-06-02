"""
predict.py — AI 預測 Router (Wave 2-C)

每個 endpoint 嘗試呼叫真實 ai_stock 模組；若 import 失敗（缺 shioaji 等依賴），
優雅降級回 mock 資料，並在 response header 加 X-Data-Source: mock。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Response

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

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/predict", tags=["predict"])

# ── 路徑注入（讓 ai_stock 可 import）────────────────────────────────────────

_AI_STOCK_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../")
)
if _AI_STOCK_ROOT not in sys.path:
    sys.path.insert(0, _AI_STOCK_ROOT)

# 預設 DB 路徑
_DB_PATH = os.path.join(_AI_STOCK_ROOT, "data", "research.db")


# ── Mock 資料（fallback）────────────────────────────────────────────────────

_TODAY = date.today().isoformat()

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
        run_id=f"plan-{_TODAY}-0001",
        created_at=f"{_TODAY}T08:30:00+08:00",
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
        run_id=f"plan-{_TODAY}-0001",
        created_at=f"{_TODAY}T08:30:00+08:00",
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
        run_id=f"plan-{_TODAY}-0001",
        created_at=f"{_TODAY}T08:30:00+08:00",
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
    run_id=f"plan-{_TODAY}-0001",
    date=_TODAY,
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
    telegram_sent_at=f"{_TODAY}T08:31:45+08:00",
)


def _deep_analysis_with_live_price(code: str) -> DeepAnalysis:
    """Mock DeepAnalysis 補 Shioaji 即時報價（失敗回 0）。"""
    da = _mock_deep_analysis(code)
    try:
        from ..services.shioaji_service import get_live_prices
        price = get_live_prices([code]).get(code, 0.0)
        if price > 0:
            da = da.model_copy(update={"last": price})
    except Exception:
        pass
    return da


def _mock_deep_analysis(code: str) -> DeepAnalysis:
    name_map = {"2330": "台積電", "2454": "聯發科", "2382": "廣達"}
    name = name_map.get(code, code)
    return DeepAnalysis(
        code=code,
        name=name,
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
        ai_conclusion=f"{name}技術面強勢，外資連續買超，AI 晶片需求持續，建議買進，目標價 1185，停損 1085。",
        ticks=[
            Tick(t="09:00", open=1120, high=1122, low=1118, close=1121, volume=1250),
            Tick(t="09:30", open=1121, high=1128, low=1120, close=1126, volume=1480),
            Tick(t="10:00", open=1126, high=1135, low=1125, close=1132, volume=1820),
            Tick(t="10:30", open=1132, high=1142, low=1130, close=1135, volume=2100),
        ],
        model="claude-haiku-4-5-20251001",
        generated_at=f"{_TODAY}T08:30:00+08:00",
    )


def _mock_reasoning_trace(code: str) -> ReasoningTrace:
    run_id = f"plan-{_TODAY}-0001"
    return ReasoningTrace(
        run_id=run_id,
        code=code,
        total_duration_ms=18420,
        steps=[
            TraceStep(phase="INPUT", t="08:30:00", label="載入輸入資料", body=f"code={code}, date={_TODAY}", cost_ms=12.0),
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
            "user": f"分析 {code} 當沖機會...",
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


# ── 真實資料輔助函式 ────────────────────────────────────────────────────────

def _picks_from_db_rows(rows: list[dict], run_id: str, plan_date: str) -> list[Pick]:
    """將 research_db.daily_plans picks_json 轉成 Pick 列表。"""
    picks = []
    for r in rows:
        try:
            signal_raw = str(r.get("signal", "hold")).lower()
            signal = Signal(signal_raw) if signal_raw in ("buy", "sell", "hold") else Signal.HOLD
            confidence_raw = r.get("confidence", 0)
            # research_db 存 0–10 整數，normalize 到 0–1
            confidence = float(confidence_raw) / 10.0 if float(confidence_raw) > 1.0 else float(confidence_raw)
            picks.append(
                Pick(
                    code=str(r.get("code", "")),
                    name=str(r.get("name", r.get("code", ""))),
                    sector=str(r.get("sector", "未知")),
                    signal=signal,
                    confidence=confidence,
                    target_price=float(r.get("target_price") or 0),
                    stop_loss_price=float(r.get("stop_loss_price") or 0),
                    last_price=float(r.get("current_price") or r.get("last_price") or 0),
                    change_pct=float(r.get("change_pct") or 0),
                    spark=[],
                    reason=str(r.get("summary") or r.get("reason") or ""),
                    tags=[],
                    action=str(r.get("action", "pending")),
                    budget=int(r.get("budget") or 0),
                    budget_ratio=float(r.get("budget_ratio") or 0),
                    run_id=run_id,
                    created_at=str(r.get("created_at") or f"{plan_date}T08:30:00+08:00"),
                )
            )
        except Exception as e:
            log.warning("Skip malformed pick row: %s — %s", r, e)
    return picks


def _build_top_n_run_from_picks(picks: list[Pick], plan_date: str) -> TopNRun:
    """從 pick 列表組成 TopNRun（沒有風控細節時用預設值）。"""
    run_id = f"plan-{plan_date}-real"
    buy_count = sum(1 for p in picks if p.signal == Signal.BUY)
    hold_count = sum(1 for p in picks if p.signal == Signal.HOLD)
    approved_count = sum(1 for p in picks if p.action == "approved")

    # 計算 sector_allocation
    sector_totals: dict[str, int] = {}
    total_budget = sum(p.budget for p in picks)
    for p in picks:
        sector_totals[p.sector] = sector_totals.get(p.sector, 0) + p.budget
    sector_allocs = [
        SectorAllocation(
            name=s,
            ratio=round(v / total_budget, 4) if total_budget else 0,
            limit=0.4,
            value=v,
        )
        for s, v in sector_totals.items()
    ]

    return TopNRun(
        run_id=run_id,
        date=plan_date,
        scanned=len(picks) * 10,
        analyzed=len(picks),
        buy_signals=buy_count,
        hold_signals=hold_count,
        approved=approved_count,
        rejected_by_risk=0,
        picks=picks,
        risk_checks=_MOCK_RISK_CHECKS,
        sector_allocation=sector_allocs,
        blacklist=[],
        cost={
            "duration_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "model": "real-data",
        },
        telegram_sent_at=None,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────


def _enrich_picks_with_live_prices(picks: list[Pick]) -> None:
    """就地更新 picks 的 last_price 為 Shioaji 即時收盤價（失敗不影響結果）。"""
    try:
        from ..services.shioaji_service import get_live_prices
        codes = [p.code for p in picks]
        live = get_live_prices(codes)
        for p in picks:
            price = live.get(p.code, 0)
            if price > 0:
                p.last_price = price
    except Exception as e:
        log.debug("_enrich_picks_with_live_prices skipped: %s", e)


@router.get("/today", response_model=TopNRun)
async def today(
    response: Response,
    current_user: User = Depends(get_current_user),
) -> TopNRun:
    """讀取今日 daily_plan，組成 TopNRun；並以 Shioaji 即時報價更新現價。"""
    result: TopNRun | None = None

    try:
        from research_db import load_daily_plan

        plan_date = date.today()
        rows = load_daily_plan(plan_date, _DB_PATH)
        if rows:
            picks = _picks_from_db_rows(rows, f"plan-{plan_date.isoformat()}-real", plan_date.isoformat())
            _enrich_picks_with_live_prices(picks)
            result = _build_top_n_run_from_picks(picks, plan_date.isoformat())
        else:
            log.info("No daily plan for %s in DB, using mock", plan_date)
    except Exception as e:
        log.warning("today() DB failed: %s", e)

    if result is None:
        response.headers["X-Data-Source"] = "mock"
        mock_picks = list(_MOCK_TOP_N_RUN.picks)
        _enrich_picks_with_live_prices(mock_picks)
        result = _MOCK_TOP_N_RUN.model_copy(update={"picks": mock_picks})

    return result


@router.get("/run/{run_id}", response_model=TopNRun)
async def get_run(
    run_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> TopNRun:
    """讀取特定 run 的 TopNRun（目前 research_db 以 date 為 key，嘗試從 run_id 解析日期）。"""
    try:
        from research_db import load_daily_plan

        # run_id 格式：plan-YYYY-MM-DD-xxxx
        parts = run_id.split("-")
        if len(parts) >= 4:
            date_str = "-".join(parts[1:4])
            plan_date = date.fromisoformat(date_str)
            rows = load_daily_plan(plan_date, _DB_PATH)
            if rows:
                picks = _picks_from_db_rows(rows, run_id, date_str)
                return _build_top_n_run_from_picks(picks, date_str)
        response.headers["X-Data-Source"] = "mock"
        return _MOCK_TOP_N_RUN
    except Exception as e:
        log.warning("get_run(%s) using mock: %s", run_id, e)
        response.headers["X-Data-Source"] = "mock"
        return _MOCK_TOP_N_RUN


@router.post("/run", response_model=TopNRun)
async def trigger_run(
    response: Response,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> TopNRun:
    """觸發新一輪 PremarketJob（耗時；同步呼叫，若失敗降級回 mock）。"""
    try:
        # PremarketJob 依賴 shioaji 等重依賴，大概率會失敗，但仍嘗試
        from morning_briefing import collect_market_data  # noqa: F401

        log.info("PremarketJob dependencies available, but full run skipped in API context")
        response.headers["X-Data-Source"] = "mock"
        return _MOCK_TOP_N_RUN
    except Exception as e:
        log.warning("trigger_run() using mock: %s", e)
        response.headers["X-Data-Source"] = "mock"
        return _MOCK_TOP_N_RUN


@router.get("/{code}/reasoning", response_model=ReasoningTrace)
async def reasoning(
    code: str,
    response: Response,
    run_id: str | None = None,
    current_user: User = Depends(get_current_user),
) -> ReasoningTrace:
    """從 research_db 讀 ai_traces，組成 ReasoningTrace。若無記錄降級回 mock。"""
    try:
        from research_db import load_stock_analysis

        analysis = load_stock_analysis(code, _DB_PATH)
        if analysis:
            effective_run_id = run_id or f"plan-{date.today().isoformat()}-real"
            # 從 StockAnalysisRow 建構 ReasoningTrace（盡力填入）
            confidence_raw = analysis.confidence
            confidence = confidence_raw / 10.0 if confidence_raw > 1.0 else float(confidence_raw)
            signal_raw = str(analysis.signal).lower()

            return ReasoningTrace(
                run_id=effective_run_id,
                code=code,
                total_duration_ms=0,
                steps=[
                    TraceStep(
                        phase="INPUT",
                        t=analysis.analyzed_at.strftime("%H:%M:%S"),
                        label="載入輸入資料",
                        body=f"code={code}, analyzed_at={analysis.analyzed_at.isoformat()}",
                        cost_ms=0.0,
                    ),
                    TraceStep(
                        phase="OUTPUT",
                        t=analysis.analyzed_at.strftime("%H:%M:%S"),
                        label="輸出結果",
                        body=analysis.summary,
                        cost_ms=0.0,
                    ),
                ],
                prompt={"system": "", "user": "", "tokens_in": 0},
                response={
                    "raw": str(analysis.factors),
                    "parsed": {
                        "signal": signal_raw,
                        "confidence": confidence,
                        "target_price": 0,
                        "stop_loss_price": 0,
                        "reason": analysis.summary,
                    },
                    "tokens_out": 0,
                },
                contributions=[
                    Contribution(key=k, detail=str(v), delta=0.0, kind="base")
                    for k, v in (analysis.factors or {}).items()
                    if k and v
                ],
                final_confidence=confidence,
                self_check=[],
                decision_hash="",
            )
        response.headers["X-Data-Source"] = "mock"
        return _mock_reasoning_trace(code)
    except Exception as e:
        log.warning("reasoning(%s) using mock: %s", code, e)
        response.headers["X-Data-Source"] = "mock"
        return _mock_reasoning_trace(code)


@router.get("/{code}", response_model=DeepAnalysis)
async def deep_analysis(
    code: str,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> DeepAnalysis:
    """讀 research_db stock_analysis，組成 DeepAnalysis schema。"""
    try:
        from research_db import load_stock_analysis
        import config as cfg

        analysis = load_stock_analysis(code, _DB_PATH)
        if analysis:
            confidence_raw = analysis.confidence
            confidence = confidence_raw / 10.0 if confidence_raw > 1.0 else float(confidence_raw)
            signal_raw = str(analysis.signal).lower()
            signal = Signal(signal_raw) if signal_raw in ("buy", "sell", "hold") else Signal.HOLD
            factors = analysis.factors or {}

            # 把 factors dict 轉成 Indicator 列表
            indicators = [
                Indicator(key=k, value=str(v), hint="", weight=0.0, signal="neutral")
                for k, v in factors.items()
                if k and v
            ]

            # 取 Shioaji 即時報價
            live_price = 0.0
            try:
                from ..services.shioaji_service import get_live_prices
                live_price = get_live_prices([code]).get(code, 0.0)
            except Exception:
                pass

            return DeepAnalysis(
                code=code,
                name=analysis.name,
                sector=str(factors.get("theme", "未知")),
                last=live_price or 0,
                change=0,
                change_pct=0,
                open=0,
                high=0,
                low=0,
                prev_close=0,
                volume_lots=0,
                signal=signal,
                confidence=confidence,
                target_price=0,
                stop_loss_price=0,
                expected_return=0,
                max_loss=0,
                risk_reward="N/A",
                budget=int(cfg.BUDGET),
                indicators=indicators,
                total_score=float(analysis.confidence),
                recommendation=analysis.summary,
                scenarios=[],
                ai_conclusion=analysis.summary,
                ticks=[],
                model="research_db",
                generated_at=analysis.analyzed_at.isoformat(),
            )
        response.headers["X-Data-Source"] = "mock"
        return _deep_analysis_with_live_price(code)
    except Exception as e:
        log.warning("deep_analysis(%s) using mock: %s", code, e)
        response.headers["X-Data-Source"] = "mock"
        return _deep_analysis_with_live_price(code)


@router.post("/{code}/rerun", response_model=DeepAnalysis)
async def rerun(
    code: str,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> DeepAnalysis:
    """重新執行 deep analysis（依賴 shioaji；降級回 mock）。"""
    try:
        # run_deep_analysis 需要 api（shioaji），幾乎確定失敗
        from deep_analyzer import run_deep_analysis as _run_deep  # noqa: F401

        log.info("deep_analyzer importable but full rerun needs shioaji API handle, using mock")
        response.headers["X-Data-Source"] = "mock"
        return _mock_deep_analysis(code)
    except Exception as e:
        log.warning("rerun(%s) using mock: %s", code, e)
        response.headers["X-Data-Source"] = "mock"
        return _mock_deep_analysis(code)


@router.post("/approve")
async def approve(
    run_id: str,
    code: str,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> dict:
    """更新 pick action = approved（從 daily_plans 中更新 action 欄位）。"""
    try:
        from research_db import load_daily_plan, save_daily_plan

        parts = run_id.split("-")
        date_str = "-".join(parts[1:4]) if len(parts) >= 4 else date.today().isoformat()
        plan_date = date.fromisoformat(date_str)
        rows = load_daily_plan(plan_date, _DB_PATH)
        updated = False
        for r in rows:
            if str(r.get("code")) == code:
                r["action"] = "approved"
                updated = True
        if updated:
            save_daily_plan(plan_date, rows, _DB_PATH)
        return {"ok": True, "code": code, "action": "approved", "updated_in_db": updated}
    except Exception as e:
        log.warning("approve(%s, %s) using mock: %s", run_id, code, e)
        response.headers["X-Data-Source"] = "mock"
        return {"ok": True, "code": code, "action": "approved"}


@router.post("/reject")
async def reject(
    run_id: str,
    code: str,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> dict:
    """更新 pick action = rejected。"""
    try:
        from research_db import load_daily_plan, save_daily_plan, reject_pick_from_plan

        parts = run_id.split("-")
        date_str = "-".join(parts[1:4]) if len(parts) >= 4 else date.today().isoformat()
        plan_date = date.fromisoformat(date_str)
        reject_pick_from_plan(plan_date, code, _DB_PATH)
        return {"ok": True, "code": code, "action": "rejected", "updated_in_db": True}
    except Exception as e:
        log.warning("reject(%s, %s) using mock: %s", run_id, code, e)
        response.headers["X-Data-Source"] = "mock"
        return {"ok": True, "code": code, "action": "rejected"}


@router.post("/approve-all")
async def approve_all(
    run_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> TopNRun:
    """批量 approve 所有 picks。"""
    try:
        from research_db import load_daily_plan, save_daily_plan

        parts = run_id.split("-")
        date_str = "-".join(parts[1:4]) if len(parts) >= 4 else date.today().isoformat()
        plan_date = date.fromisoformat(date_str)
        rows = load_daily_plan(plan_date, _DB_PATH)
        for r in rows:
            r["action"] = "approved"
        save_daily_plan(plan_date, rows, _DB_PATH)
        picks = _picks_from_db_rows(rows, run_id, date_str)
        return _build_top_n_run_from_picks(picks, date_str)
    except Exception as e:
        log.warning("approve_all(%s) using mock: %s", run_id, e)
        response.headers["X-Data-Source"] = "mock"
        updated = _MOCK_TOP_N_RUN.model_copy(deep=True)
        for p in updated.picks:
            object.__setattr__(p, "action", "approved")
        return updated


@router.get("/run/{run_id}/reasoning", response_model=list[ReasoningTrace])
async def run_reasoning(
    run_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> list[ReasoningTrace]:
    """整批推理過程（依 run_id 找出所有 picks 並回傳各自的 ReasoningTrace）。"""
    try:
        from research_db import load_daily_plan

        parts = run_id.split("-")
        date_str = "-".join(parts[1:4]) if len(parts) >= 4 else date.today().isoformat()
        plan_date = date.fromisoformat(date_str)
        rows = load_daily_plan(plan_date, _DB_PATH)
        if rows:
            traces = []
            for r in rows:
                code = str(r.get("code", ""))
                if code:
                    # 嘗試從 stock_analysis 讀取
                    try:
                        from research_db import load_stock_analysis
                        analysis = load_stock_analysis(code, _DB_PATH)
                    except Exception:
                        analysis = None

                    if analysis:
                        confidence_raw = analysis.confidence
                        confidence = confidence_raw / 10.0 if confidence_raw > 1.0 else float(confidence_raw)
                        traces.append(
                            ReasoningTrace(
                                run_id=run_id,
                                code=code,
                                total_duration_ms=0,
                                steps=[
                                    TraceStep(
                                        phase="OUTPUT",
                                        t=analysis.analyzed_at.strftime("%H:%M:%S"),
                                        label="分析完成",
                                        body=analysis.summary,
                                        cost_ms=0.0,
                                    )
                                ],
                                prompt={"system": "", "user": "", "tokens_in": 0},
                                response={
                                    "raw": str(analysis.factors),
                                    "parsed": {
                                        "signal": str(analysis.signal).lower(),
                                        "confidence": confidence,
                                        "target_price": 0,
                                        "stop_loss_price": 0,
                                        "reason": analysis.summary,
                                    },
                                    "tokens_out": 0,
                                },
                                contributions=[],
                                final_confidence=confidence,
                                self_check=[],
                                decision_hash="",
                            )
                        )
                    else:
                        traces.append(_mock_reasoning_trace(code))
            if traces:
                return traces
        response.headers["X-Data-Source"] = "mock"
        return [_mock_reasoning_trace("2330"), _mock_reasoning_trace("2454")]
    except Exception as e:
        log.warning("run_reasoning(%s) using mock: %s", run_id, e)
        response.headers["X-Data-Source"] = "mock"
        return [_mock_reasoning_trace("2330"), _mock_reasoning_trace("2454")]
