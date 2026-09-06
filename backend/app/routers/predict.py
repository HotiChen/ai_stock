"""
predict.py — AI 預測 Router (Wave 2-C)

每個 endpoint 嘗試呼叫真實 ai_stock 模組；若 import 失敗（缺 shioaji 等依賴），
沒有資料時回 404、依賴不可用時回 503——不以假資料充數。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response

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
        # daily_plan 沒有記掃描總數。原本寫 len(picks) * 10，是憑空乘出來的
        # ——0 代表「不知道」，前端會顯示 "—"。
        scanned=0,
        analyzed=len(picks),
        buy_signals=buy_count,
        hold_signals=hold_count,
        approved=approved_count,
        rejected_by_risk=0,
        picks=picks,
        # 原本連真實路徑都塞 _MOCK_RISK_CHECKS，於是「風控紀要」那張卡
        # 永遠顯示六項全部通過——那六項根本沒有被執行過。
        risk_checks=[],
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
            raise HTTPException(
                status_code=404,
                detail=f"{plan_date.isoformat()} 尚無選股計畫。"
                       "08:30 PremarketJob 產生後才會有資料；"
                       "main.py 沒在跑的話今天不會產生。",
            )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("today() DB failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"讀取選股計畫失敗：{e}",
        ) from e

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
        raise HTTPException(status_code=404, detail=f"找不到 run {run_id} 的選股計畫。")
    except HTTPException:
        raise
    except Exception as e:
        log.warning("get_run(%s) failed: %s", run_id, e)
        raise HTTPException(status_code=503, detail=f"讀取 run {run_id} 失敗：{e}") from e


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

        # PremarketJob 需要 shioaji handle 與數十秒執行時間，不能在 request
        # 週期內跑完。原本這裡回一份 mock，看起來像「重新分析完成了」。
        raise HTTPException(
            status_code=501,
            detail="從 UI 觸發重新選股尚未實作。"
                   "目前請等 08:30 排程，或在終端機執行 PremarketJob。",
        )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("trigger_run() failed: %s", e)
        raise HTTPException(status_code=503, detail=f"觸發選股失敗：{e}") from e


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
        raise HTTPException(
            status_code=404,
            detail=f"{code} 沒有推理紀錄。只有經過 AI 深度分析的候選才會留下"
                   "（每日技術評分前 8 名）。",
        )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("reasoning(%s) failed: %s", code, e)
        raise HTTPException(status_code=503, detail=f"讀取 {code} 推理紀錄失敗：{e}") from e


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
        raise HTTPException(
            status_code=404,
            detail=f"{code} 沒有深度分析紀錄。只有 08:30 技術評分前 8 名"
                   "會做 AI 深度分析。",
        )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("deep_analysis(%s) failed: %s", code, e)
        raise HTTPException(status_code=503, detail=f"讀取 {code} 深度分析失敗：{e}") from e


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

        raise HTTPException(
            status_code=501,
            detail=f"從 UI 重新分析 {code} 尚未實作（需要 shioaji handle）。",
        )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("rerun(%s) failed: %s", code, e)
        raise HTTPException(status_code=503, detail=f"重新分析 {code} 失敗：{e}") from e


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
        # 原本寫入失敗仍回 ok=True，畫面顯示「已批准」但資料庫沒這回事。
        log.warning("approve(%s, %s) failed: %s", run_id, code, e)
        raise HTTPException(status_code=503, detail=f"批准 {code} 失敗：{e}") from e


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
        log.warning("reject(%s, %s) failed: %s", run_id, code, e)
        raise HTTPException(status_code=503, detail=f"排除 {code} 失敗：{e}") from e


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
        log.warning("approve_all(%s) failed: %s", run_id, e)
        raise HTTPException(status_code=503, detail=f"批准全部失敗：{e}") from e


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
                    # 沒有紀錄的個股就不放進來，不用假的補齊
            if traces:
                return traces
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id} 沒有任何推理紀錄。",
        )
    except HTTPException:
        raise
    except Exception as e:
        log.warning("run_reasoning(%s) failed: %s", run_id, e)
        raise HTTPException(status_code=503, detail=f"讀取推理紀錄失敗：{e}") from e
