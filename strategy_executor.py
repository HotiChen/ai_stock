from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from deep_analyzer import DeepAnalysis, run_deep_analysis
from market_context import (
    USMarketSnapshot,
    build_market_summary,
    classify_market_sentiment,
    fetch_macro_news,
    fetch_us_market,
)
from research_db import (
    DailySummaryRow,
    ExecutionRecordRow,
    PlanExecutionRow,
    StockAnalysisRow,
    init_db,
    load_active_execution,
    load_daily_summaries,
    load_execution_records,
    save_daily_summary,
    save_execution_record,
    save_plan_execution,
    save_stock_analysis,
    stop_execution,
)
from stock_research import (
    fetch_stock_fundamentals,
    fetch_stock_news,
    format_fundamentals_text,
)
from strategy_planner import PlanSet, StockPick

_MIN_CONFIDENCE = 6   # 低於此不行動


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ExecutionStatus:
    execution_id:        str
    plan_type:           str
    status:              str
    started_at:          datetime
    total_simulated_pnl: float
    cycles_run:          int


@dataclass
class CycleResult:
    ran_at:           datetime
    actions_taken:    list[dict]
    skipped_codes:    list[str]
    market_sentiment: str


# ── Price helper ──────────────────────────────────────────────────────────────

def get_current_price(code: str, api=None) -> Optional[float]:
    """最新成交價（Shioaji）。取不到回 None。

    原本走 yfinance download，已移除。改走 snapshot，並由
    shioaji_quotes.latest_price 過濾 close == 0（報價 session 未暖機的情況）。
    """
    import shioaji_quotes

    if api is None:
        import shioaji_session
        api = shioaji_session.get_api(connect=False)
    return shioaji_quotes.latest_price(api, code)


# ── Core logic ────────────────────────────────────────────────────────────────

def build_cycle_decision(
    pick: StockPick,
    analysis: DeepAnalysis,
    current_price: Optional[float],
) -> dict:
    if analysis.confidence < _MIN_CONFIDENCE:
        return {"action": "hold", "code": pick.code, "name": pick.name,
                "price": current_price, "reason": f"信心分數 {analysis.confidence}/10 不足，略過"}
    return {
        "action":   analysis.signal,
        "code":     pick.code,
        "name":     pick.name,
        "quantity": pick.quantity,
        "price":    current_price,
        "reason":   analysis.summary,
        "confidence": analysis.confidence,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def start_execution(plan_set: PlanSet, plan_type: str, db_path: str) -> str:
    init_db(db_path)
    # 先停掉現有 active（只允許一個）
    existing = load_active_execution(db_path)
    if existing:
        stop_execution(existing.execution_id, db_path)

    import dataclasses, json as _json

    def _serial(obj):
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        raise TypeError(f"{type(obj)}")

    exec_id = str(uuid.uuid4())[:8]
    plan_map = {
        "aggressive":   plan_set.aggressive,
        "balanced":     plan_set.balanced,
        "conservative": plan_set.conservative,
    }
    plan_json = _json.loads(_json.dumps(dataclasses.asdict(plan_map[plan_type]), default=str))

    row = PlanExecutionRow(
        execution_id=exec_id,
        created_at=datetime.now(),
        plan_type=plan_type,
        plan_json=plan_json,
        status="active",
    )
    save_plan_execution(row, db_path)
    return exec_id


def run_cycle(execution_id: str, db_path: str) -> CycleResult:
    from research_db import _conn
    # load execution
    with _conn(db_path) as con:
        r = con.execute(
            "SELECT plan_type, plan_json FROM plan_executions WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
    if not r:
        return CycleResult(datetime.now(), [], [], "unknown")

    import json as _json
    plan_data = _json.loads(r[1]) if isinstance(r[1], str) else r[1]
    picks_raw = plan_data.get("picks", [])
    picks = [StockPick(**p) for p in picks_raw]

    # collect market context
    us_snapshot = fetch_us_market()
    macro_news  = fetch_macro_news(max_per_query=2)
    sentiment   = classify_market_sentiment(us_snapshot) if us_snapshot else "neutral"
    market_summary = build_market_summary(
        us_snapshot or USMarketSnapshot(0, 0, 0, 20),
        macro_news,
    )

    actions_taken: list[dict] = []
    skipped_codes: list[str]  = []
    now = datetime.now()

    for pick in picks:
        current_price = get_current_price(pick.code)
        if current_price is None:
            skipped_codes.append(pick.code)
            continue

        # deep analysis
        stock_news   = fetch_stock_news(pick.code, pick.name)
        fundamentals = fetch_stock_fundamentals(pick.code)
        fund_text    = format_fundamentals_text(fundamentals) if fundamentals else ""
        news_titles  = [n.get("title", "") for n in stock_news] if stock_news else []

        analysis = run_deep_analysis(
            code=pick.code, name=pick.name,
            news=news_titles,
            fundamentals_text=fund_text,
            market_summary=market_summary,
            theme_info="",
        )

        # persist analysis to DB
        factors_dict = {
            "historical_trend": analysis.factors.historical_trend,
            "news":             analysis.factors.news,
            "theme":            analysis.factors.theme,
            "us_market":        analysis.factors.us_market,
            "tw_policy":        analysis.factors.tw_policy,
            "us_policy":        analysis.factors.us_policy,
        }
        save_stock_analysis(StockAnalysisRow(
            analyzed_at=now, code=pick.code, name=pick.name,
            signal=analysis.signal, confidence=analysis.confidence,
            summary=analysis.summary, factors=factors_dict,
        ), db_path)

        decision = build_cycle_decision(pick, analysis, current_price)

        # persist record
        action = decision["action"]
        if action != "hold":
            record = ExecutionRecordRow(
                execution_id=execution_id,
                recorded_at=now,
                code=pick.code, name=pick.name,
                action=action,
                quantity=decision.get("quantity", 1),
                price=current_price,
                simulated_pnl=0.0,
                reason=decision.get("reason", ""),
            )
            save_execution_record(record, db_path)
            actions_taken.append(decision)
        else:
            skipped_codes.append(pick.code)

    return CycleResult(
        ran_at=now,
        actions_taken=actions_taken,
        skipped_codes=skipped_codes,
        market_sentiment=sentiment,
    )


def get_execution_status(execution_id: str, db_path: str) -> Optional[ExecutionStatus]:
    from research_db import _conn
    with _conn(db_path) as con:
        r = con.execute(
            "SELECT execution_id,plan_type,status,created_at FROM plan_executions WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
    if not r:
        return None
    records = load_execution_records(execution_id, db_path)
    total_pnl = sum(rec.simulated_pnl for rec in records)
    return ExecutionStatus(
        execution_id=r[0], plan_type=r[1], status=r[2],
        started_at=datetime.fromisoformat(r[3]),
        total_simulated_pnl=total_pnl,
        cycles_run=len(records),
    )
