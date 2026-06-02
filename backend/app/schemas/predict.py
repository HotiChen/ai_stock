from typing import Literal
from pydantic import BaseModel

from .base import Signal


class Pick(BaseModel):
    code: str
    name: str
    sector: str
    signal: Signal
    confidence: float
    target_price: float
    stop_loss_price: float
    last_price: float
    change_pct: float
    spark: list[float]
    reason: str
    tags: list[str]
    action: Literal["approved", "pending", "rejected", "observe"]
    budget: int
    budget_ratio: float
    run_id: str
    created_at: str


class RiskCheck(BaseModel):
    key: str
    sub: str
    status: Literal["pass", "warn", "fail"]
    detail: str


class SectorAllocation(BaseModel):
    name: str
    ratio: float
    limit: float
    value: int


class TopNRun(BaseModel):
    run_id: str
    date: str
    scanned: int
    analyzed: int
    buy_signals: int
    hold_signals: int
    approved: int
    rejected_by_risk: int
    picks: list[Pick]
    risk_checks: list[RiskCheck]
    sector_allocation: list[SectorAllocation]
    blacklist: list[str]
    cost: dict
    telegram_sent_at: str | None


class Indicator(BaseModel):
    key: str
    value: float | str
    hint: str
    weight: float
    signal: Literal["bull", "bear", "neutral"]


class Scenario(BaseModel):
    name: str
    probability: float
    target_price: float
    return_pct: float
    description: str


class Tick(BaseModel):
    t: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class DeepAnalysis(BaseModel):
    code: str
    name: str
    sector: str
    last: float
    change: float
    change_pct: float
    open: float
    high: float
    low: float
    prev_close: float
    volume_lots: float
    signal: Signal
    confidence: float
    target_price: float
    stop_loss_price: float
    expected_return: float
    max_loss: float
    risk_reward: str
    budget: int
    indicators: list[Indicator]
    total_score: float
    recommendation: str
    scenarios: list[Scenario]
    ai_conclusion: str
    ticks: list[Tick]
    model: str
    generated_at: str


TracePhase = Literal["INPUT", "FETCH", "EVAL", "PROMPT", "LLM", "PARSE", "GUARD", "OUTPUT"]


class TraceStep(BaseModel):
    phase: TracePhase
    t: str
    label: str
    body: str
    cost_ms: float | None
    cost_usd: float | None = None


class Contribution(BaseModel):
    key: str
    detail: str
    delta: float
    kind: Literal["positive", "negative", "base"]


class SelfCheck(BaseModel):
    question: str
    answer: str
    passed: bool


class ReasoningTrace(BaseModel):
    run_id: str
    code: str
    total_duration_ms: float
    steps: list[TraceStep]
    prompt: dict
    response: dict
    contributions: list[Contribution]
    final_confidence: float
    self_check: list[SelfCheck]
    decision_hash: str
