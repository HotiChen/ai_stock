from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from ai_client import call_gemini
from daily_tracker import DailyTrackRecord, list_day_records

_PLAN_TYPES    = ("aggressive", "balanced", "conservative")
_VALID_PERIODS = (7, 14, 28)
_DEFAULT_DIR   = "data/daily_tracking"
_DEFAULT_BRIEFING_DIR = "data/morning_briefings"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class PlanPerformanceSummary:
    plan_type: str          # "aggressive" | "balanced" | "conservative" | "actual"
    total_pnl: float        # 累積損益 (TWD)
    avg_pnl_pct: float      # 平均日報酬率 %
    win_rate: float         # 勝率 0.0 ~ 1.0
    days_tracked: int       # 有結算資料的天數


@dataclass
class BriefingDayAccuracy:
    date:               date
    predicted_outlook:  str            # "bullish" | "bearish" | "neutral"
    actual_pnl_pct:     float          # actual PnL% from chosen plan result
    correct:            bool           # True if prediction matched outcome
    vix:                Optional[float] = None


@dataclass
class BriefingAccuracyReport:
    per_day: list[BriefingDayAccuracy] = field(default_factory=list)

    @property
    def total_days(self) -> int:
        return len(self.per_day)

    @property
    def accuracy_rate(self) -> float:
        if not self.per_day:
            return 0.0
        return sum(1 for d in self.per_day if d.correct) / len(self.per_day)

    @property
    def bullish_accuracy(self) -> float:
        bullish = [d for d in self.per_day if d.predicted_outlook == "bullish"]
        if not bullish:
            return 0.0
        return sum(1 for d in bullish if d.correct) / len(bullish)

    @property
    def bearish_accuracy(self) -> float:
        bearish = [d for d in self.per_day if d.predicted_outlook == "bearish"]
        if not bearish:
            return 0.0
        return sum(1 for d in bearish if d.correct) / len(bearish)


@dataclass
class LearningReport:
    generated_at: date
    period_days: int                            # 7 / 14 / 28
    start_date: date
    end_date: date
    actual_path: PlanPerformanceSummary         # 使用者實際選擇路徑的混合績效
    shadow_paths: list[PlanPerformanceSummary]  # 三條假設路徑（各自每天一致選同一套）
    system_recommendation: str                  # "aggressive" | "balanced" | "conservative"
    ai_analysis: str                            # Gemini 全文分析
    key_insights: list[str] = field(default_factory=list)
    briefing_accuracy: Optional[BriefingAccuracyReport] = None


# ── Core functions ────────────────────────────────────────────────────────────

def collect_report_data(
    days: int,
    track_dir: str = _DEFAULT_DIR,
) -> list[DailyTrackRecord]:
    """取最近 N 天的日記錄，按日期升序（oldest first）。"""
    all_records = list_day_records(track_dir)   # newest-first
    recent = all_records[:days]
    recent.sort(key=lambda r: r.date)
    return recent


def compute_plan_stats(
    records: list[DailyTrackRecord],
    plan_type: str,
) -> PlanPerformanceSummary:
    """
    聚合某套計劃在 N 天的假設績效（不論使用者當天是否選它，
    影子追蹤資料都存在）。
    """
    total_pnl = 0.0
    pnl_pcts: list[float] = []
    wins = 0
    losses = 0
    days_tracked = 0

    for rec in records:
        for result in rec.results:
            if result.plan_type == plan_type and result.pnl is not None:
                total_pnl += result.pnl
                if result.pnl_pct is not None:
                    pnl_pcts.append(result.pnl_pct)
                wins += result.win_trades
                losses += result.loss_trades
                days_tracked += 1

    total_trades = wins + losses
    win_rate     = wins / total_trades if total_trades > 0 else 0.0
    avg_pnl_pct  = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0.0

    return PlanPerformanceSummary(
        plan_type=plan_type,
        total_pnl=total_pnl,
        avg_pnl_pct=avg_pnl_pct,
        win_rate=win_rate,
        days_tracked=days_tracked,
    )


def compute_actual_path_stats(
    records: list[DailyTrackRecord],
) -> PlanPerformanceSummary:
    """
    聚合使用者每天實際選擇的計劃績效（is_actual=True）。
    plan_type 標記為 "actual"，可能是不同計劃的混合路徑。
    """
    total_pnl = 0.0
    pnl_pcts: list[float] = []
    wins = 0
    losses = 0
    days_tracked = 0

    for rec in records:
        for result in rec.results:
            if result.is_actual and result.pnl is not None:
                total_pnl += result.pnl
                if result.pnl_pct is not None:
                    pnl_pcts.append(result.pnl_pct)
                wins += result.win_trades
                losses += result.loss_trades
                days_tracked += 1

    total_trades = wins + losses
    win_rate     = wins / total_trades if total_trades > 0 else 0.0
    avg_pnl_pct  = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0.0

    return PlanPerformanceSummary(
        plan_type="actual",
        total_pnl=total_pnl,
        avg_pnl_pct=avg_pnl_pct,
        win_rate=win_rate,
        days_tracked=days_tracked,
    )


def build_report_prompt(records: list[DailyTrackRecord], days: int) -> str:
    """為 Gemini 建立學習報告 prompt，包含實際路徑 + 三條影子路徑比較。"""
    if not records:
        return ""

    start = records[0].date
    end   = records[-1].date

    actual_stats = compute_actual_path_stats(records)
    shadow_stats = {pt: compute_plan_stats(records, pt) for pt in _PLAN_TYPES}

    lines = [
        f"# {days} 天策略學習報告",
        f"分析期間：{start} ~ {end}（共 {len(records)} 天）",
        "",
        "## 實際路徑績效（使用者每天實際選的計劃）",
        f"- 累積損益：{actual_stats.total_pnl:+.0f} TWD",
        f"- 平均日報酬率：{actual_stats.avg_pnl_pct:+.2f}%",
        f"- 勝率：{actual_stats.win_rate:.1%}",
        f"- 已結算天數：{actual_stats.days_tracked}/{len(records)}",
        "",
        "## 影子追蹤 — 反事實比較（假設每天都選同一套）",
        "",
    ]

    for pt in _PLAN_TYPES:
        s = shadow_stats[pt]
        lines += [
            f"### {pt}（每天一致選此套）",
            f"- 累積損益：{s.total_pnl:+.0f} TWD",
            f"- 平均日報酬率：{s.avg_pnl_pct:+.2f}%",
            f"- 勝率：{s.win_rate:.1%}",
            f"- 已結算天數：{s.days_tracked}/{len(records)}",
            "",
        ]

    lines += [
        "## 每日操作摘要",
        "",
    ]
    for rec in records:
        actual_res = next((r for r in rec.results if r.is_actual), None)
        pnl_str = f"{actual_res.pnl:+.0f}" if (actual_res and actual_res.pnl is not None) else "未結算"
        lines.append(f"- {rec.date}：選 {rec.chosen_plan_type}，損益 {pnl_str} TWD")

    lines += [
        "",
        "## 分析任務",
        "",
        "根據上方的績效數據，請：",
        "1. 指出三條影子路徑中哪條累積報酬最高，並說明原因",
        "2. 分析使用者實際選擇路徑的優缺點（哪幾天決策正確？哪幾天錯了？）",
        "3. 找出使用者的決策偏誤模式",
        "4. 給出下一期的具體操作建議",
        '5. 最後一行輸出 JSON（不要換行）：',
        '   {"system_recommendation": "aggressive"|"balanced"|"conservative", '
        '"key_insights": ["洞察1", "洞察2", "洞察3"]}',
        "",
        "請用繁體中文回答，分析要具體，不要泛泛而談。",
    ]

    return "\n".join(lines)


def _parse_recommendation(ai_text: str) -> tuple[str, list[str]]:
    """從 AI 回應中解析 system_recommendation 和 key_insights。"""
    try:
        match = re.search(r'\{[^{}]*"system_recommendation"[^{}]*\}', ai_text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            rec = data.get("system_recommendation", "balanced")
            if rec not in _PLAN_TYPES:
                rec = "balanced"
            insights = data.get("key_insights", [])
            return rec, insights
    except Exception:
        pass
    return "balanced", []


# ── Briefing accuracy functions ───────────────────────────────────────────────

def load_briefings_for_period(
    start: date,
    end: date,
    briefing_dir: str = _DEFAULT_BRIEFING_DIR,
) -> list:
    """
    讀取 briefing_dir 中 start ~ end 範圍內的所有 MorningBriefing。
    檔名格式：YYYY-MM-DD.json
    """
    from morning_briefing import load_briefing

    briefings = []
    current = start
    while current <= end:
        b = load_briefing(current, briefing_dir=briefing_dir)
        if b is not None:
            briefings.append(b)
        current = date.fromordinal(current.toordinal() + 1)
    return briefings


def compute_briefing_accuracy(
    records:   list[DailyTrackRecord],
    briefings: list,
) -> BriefingAccuracyReport:
    """
    交叉比對每天的 briefing 預測 vs 實際 PnL，
    計算簡報預測的正確率。
    """
    briefing_map = {b.date: b for b in briefings}
    record_map   = {r.date: r for r in records}

    per_day: list[BriefingDayAccuracy] = []

    for d, briefing in briefing_map.items():
        rec = record_map.get(d)
        if rec is None:
            continue

        # 取 is_actual=True 的結果拿 pnl_pct
        actual_result = next((r for r in rec.results if r.is_actual), None)
        if actual_result is None or actual_result.pnl_pct is None:
            continue

        outlook = (briefing.market_outlook or "").lower()
        pnl_pct = actual_result.pnl_pct

        # 判定是否正確：bullish→正獲利, bearish→負獲利, neutral→不算
        if outlook == "bullish":
            correct = pnl_pct > 0
        elif outlook == "bearish":
            correct = pnl_pct < 0
        else:
            correct = False  # neutral 不算對

        vix = briefing.market_data.vix if briefing.market_data else None

        per_day.append(BriefingDayAccuracy(
            date=d,
            predicted_outlook=outlook,
            actual_pnl_pct=pnl_pct,
            correct=correct,
            vix=vix,
        ))

    per_day.sort(key=lambda x: x.date)
    return BriefingAccuracyReport(per_day=per_day)


def build_briefing_section(accuracy: BriefingAccuracyReport) -> str:
    """生成 briefing 準確率摘要，用於注入 Gemini 分析 prompt。"""
    if not accuracy.per_day:
        return "## 早晨簡報預測準確度\n\n（無資料）\n"

    lines = [
        "## 早晨簡報預測準確度（Gemini 市場展望 vs 實際損益）",
        "",
        f"- 分析天數：{accuracy.total_days} 天",
        f"- 整體準確率：{accuracy.accuracy_rate:.1%}",
        f"- 看多（bullish）準確率：{accuracy.bullish_accuracy:.1%}",
        f"- 看空（bearish）準確率：{accuracy.bearish_accuracy:.1%}",
        "",
        "| 日期 | 預測 | 實際 PnL% | 是否正確 |",
        "|------|------|-----------|--------|",
    ]
    for d in accuracy.per_day:
        ok_str = "✅" if d.correct else "❌"
        lines.append(
            f"| {d.date} | {d.predicted_outlook} | {d.actual_pnl_pct:+.2f}% | {ok_str} |"
        )
    lines.append("")
    return "\n".join(lines)


def generate_learning_report(
    days: int = 7,
    track_dir: str = _DEFAULT_DIR,
    briefing_dir: Optional[str] = None,
) -> Optional[LearningReport]:
    """
    生成學習報告。
    - days 必須是 7 / 14 / 28
    - 若資料筆數不足 days，回傳 None
    - 若 Gemini 失敗，回傳 None
    - briefing_dir: 若提供，加入簡報準確率分析
    """
    if days not in _VALID_PERIODS:
        raise ValueError(f"days must be one of {_VALID_PERIODS}, got {days}")

    records = collect_report_data(days, track_dir)
    if len(records) < days:
        return None

    # Compute briefing accuracy if dir given
    briefing_accuracy: Optional[BriefingAccuracyReport] = None
    if briefing_dir and records:
        briefings = load_briefings_for_period(
            records[0].date, records[-1].date, briefing_dir
        )
        briefing_accuracy = compute_briefing_accuracy(records, briefings)

    # Build prompt, optionally inject briefing section
    prompt = build_report_prompt(records, days)
    if briefing_accuracy and briefing_accuracy.total_days > 0:
        briefing_section = build_briefing_section(briefing_accuracy)
        prompt = prompt + "\n\n" + briefing_section

    ai_text = call_gemini(prompt, max_tokens=16_384)
    if not ai_text:
        return None

    recommendation, insights = _parse_recommendation(ai_text)

    actual_stats  = compute_actual_path_stats(records)
    shadow_stats  = [compute_plan_stats(records, pt) for pt in _PLAN_TYPES]

    return LearningReport(
        generated_at=date.today(),
        period_days=days,
        start_date=records[0].date,
        end_date=records[-1].date,
        actual_path=actual_stats,
        shadow_paths=shadow_stats,
        system_recommendation=recommendation,
        ai_analysis=ai_text,
        key_insights=insights,
        briefing_accuracy=briefing_accuracy,
    )
