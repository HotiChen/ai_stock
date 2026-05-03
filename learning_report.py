from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ai_client import call_gemini
from daily_tracker import DailyTrackRecord, list_day_records

_PLAN_TYPES   = ("aggressive", "balanced", "conservative")
_VALID_PERIODS = (7, 14, 28)
_DEFAULT_DIR   = "data/daily_tracking"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class PlanPerformanceSummary:
    plan_type: str          # "aggressive" | "balanced" | "conservative" | "actual"
    total_pnl: float        # 累積損益 (TWD)
    avg_pnl_pct: float      # 平均日報酬率 %
    win_rate: float         # 勝率 0.0 ~ 1.0
    days_tracked: int       # 有結算資料的天數


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


def generate_learning_report(
    days: int = 7,
    track_dir: str = _DEFAULT_DIR,
) -> Optional[LearningReport]:
    """
    生成學習報告。
    - days 必須是 7 / 14 / 28
    - 若資料筆數不足 days，回傳 None
    - 若 Gemini 失敗，回傳 None
    """
    if days not in _VALID_PERIODS:
        raise ValueError(f"days must be one of {_VALID_PERIODS}, got {days}")

    records = collect_report_data(days, track_dir)
    if len(records) < days:
        return None

    prompt   = build_report_prompt(records, days)
    ai_text  = call_gemini(prompt, max_tokens=16_384)
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
    )
