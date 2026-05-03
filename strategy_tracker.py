from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import config
from news_agent import call_ollama, extract_json


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class StrategyGoal:
    target_multiplier: float
    start_date:        date
    end_date:          date
    initial_capital:   float
    approach:          str
    # Extended fields (optional, backward-compatible)
    stop_loss_pct:    Optional[float] = None   # e.g. 5.0 means stop at -5%
    max_positions:    Optional[int]   = None   # max number of concurrent positions
    allow_fractional: bool            = False  # allow fractional (零股) trades

    @property
    def total_days(self) -> int:
        return (self.end_date - self.start_date).days

    @property
    def target_value(self) -> float:
        return self.initial_capital * self.target_multiplier


@dataclass
class DailyEntry:
    date:          date
    pnl:           float
    trades_count:  int
    review:        str
    tomorrow_plan: str
    target_met:    bool


@dataclass
class StrategyProgress:
    days_elapsed:       int
    days_remaining:     int
    total_pnl:          float
    total_pnl_pct:      float
    on_track:           bool
    required_daily_pnl: float
    daily_target_pnl:   float
    completion_pct:     float


# ── Persistence ───────────────────────────────────────────────────────────────

def save_goal(goal: StrategyGoal, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "target_multiplier": goal.target_multiplier,
        "start_date":        goal.start_date.isoformat(),
        "end_date":          goal.end_date.isoformat(),
        "initial_capital":   goal.initial_capital,
        "approach":          goal.approach,
        "stop_loss_pct":     goal.stop_loss_pct,
        "max_positions":     goal.max_positions,
        "allow_fractional":  goal.allow_fractional,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_goal(path: str) -> Optional[StrategyGoal]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return StrategyGoal(
            target_multiplier=data["target_multiplier"],
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
            initial_capital=data["initial_capital"],
            approach=data["approach"],
            stop_loss_pct=data.get("stop_loss_pct"),
            max_positions=data.get("max_positions"),
            allow_fractional=bool(data.get("allow_fractional", False)),
        )
    except Exception:
        return None


def save_daily_entry(entry: DailyEntry, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    row = {
        "date":          entry.date.isoformat(),
        "pnl":           entry.pnl,
        "trades_count":  entry.trades_count,
        "review":        entry.review,
        "tomorrow_plan": entry.tomorrow_plan,
        "target_met":    entry.target_met,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_daily_entries(path: str) -> list[DailyEntry]:
    try:
        entries = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                entries.append(DailyEntry(
                    date=date.fromisoformat(d["date"]),
                    pnl=d["pnl"],
                    trades_count=d["trades_count"],
                    review=d["review"],
                    tomorrow_plan=d["tomorrow_plan"],
                    target_met=d["target_met"],
                ))
        return entries
    except Exception:
        return []


# ── Calculations ──────────────────────────────────────────────────────────────

def calc_daily_target(goal: StrategyGoal) -> float:
    if goal.total_days == 0:
        return 0.0
    target_pnl = goal.target_value - goal.initial_capital
    return target_pnl / goal.total_days


def calc_progress(goal: StrategyGoal, current_value: float, today: date) -> StrategyProgress:
    days_elapsed   = (today - goal.start_date).days
    days_remaining = (goal.end_date - today).days

    total_pnl     = current_value - goal.initial_capital
    total_pnl_pct = (total_pnl / goal.initial_capital) * 100 if goal.initial_capital else 0.0

    target_pnl       = goal.target_value - goal.initial_capital
    completion_pct   = (total_pnl / target_pnl * 100) if target_pnl else 0.0
    daily_target_pnl = calc_daily_target(goal)

    # on_track: current value >= linearly interpolated expected value at today
    if goal.total_days > 0:
        expected_value = goal.initial_capital + target_pnl * (days_elapsed / goal.total_days)
    else:
        expected_value = goal.target_value
    on_track = current_value >= expected_value

    # required daily pnl from today to reach target
    pnl_remaining = goal.target_value - current_value
    if days_remaining > 0:
        required_daily_pnl = pnl_remaining / days_remaining
    else:
        required_daily_pnl = pnl_remaining

    return StrategyProgress(
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
        on_track=on_track,
        required_daily_pnl=required_daily_pnl,
        daily_target_pnl=daily_target_pnl,
        completion_pct=completion_pct,
    )


def check_daily_target(daily_pnl: float, daily_target: float) -> bool:
    return daily_pnl >= daily_target


# ── AI Review ─────────────────────────────────────────────────────────────────

def _build_review_prompt(
    goal: StrategyGoal,
    today_pnl: float,
    daily_target: float,
    trades: list[str],
    yesterday_plan: str,
) -> str:
    trades_text = "\n".join(f"- {t}" for t in trades) if trades else "（今日無交易記錄）"
    met = "✅ 達標" if today_pnl >= daily_target else "❌ 未達標"
    return f"""你是一位台股投資顧問，請根據以下資訊給出今日交易檢討和明日計畫。

=== 策略目標 ===
目標：{goal.target_multiplier}x 報酬（{goal.initial_capital:,.0f} 元 → {goal.target_value:,.0f} 元）
做法：{goal.approach}
期間：{goal.start_date} 至 {goal.end_date}

=== 今日績效 ===
今日損益：{today_pnl:+,.0f} 元
每日目標：{daily_target:,.0f} 元
達標狀況：{met}

=== 昨日計畫 ===
{yesterday_plan or '（無昨日計畫）'}

=== 今日交易 ===
{trades_text}

請只回答 JSON，不要其他文字：
{{"review": "一到三句話的今日檢討", "tomorrow_plan": "明日具體計畫"}}"""


def generate_daily_review(
    goal: StrategyGoal,
    today_pnl: float,
    daily_target: float,
    trades: list[str],
    yesterday_plan: str,
) -> dict:
    prompt = _build_review_prompt(goal, today_pnl, daily_target, trades, yesterday_plan)
    try:
        raw = call_ollama(config.DECISION_MODEL, prompt, timeout=60)
        result = extract_json(raw)
        if "review" in result and "tomorrow_plan" in result:
            return result
    except Exception:
        pass
    return {
        "review":        "今日檢討無法生成，請手動填寫。",
        "tomorrow_plan": "明日計畫無法生成，請手動填寫。",
    }
