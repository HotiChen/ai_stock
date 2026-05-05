"""
weekly_report.py — AI weekly strategy review

每週五盤後自動統計本週 AI 選股成效，
整理成結構化資料後送給 Claude 進行分析，
最終產出每週策略複盤報告並推播到 Telegram。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from learning_db import LearningDB, DailyStrategyLog, StockPredictionLog


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class WeeklyReportData:
    start_date: date
    end_date: date
    n_trading_days: int
    total_pnl: float
    stock_win_rate: float
    best_pick: dict          # {code, name, actual_return_pct, date, ...}
    worst_pick: dict         # {code, name, actual_return_pct, date, ...}
    high_conf_win_rate: float
    daily_logs: list[DailyStrategyLog] = field(default_factory=list)
    predictions: list[StockPredictionLog] = field(default_factory=list)


# ── Core Functions ─────────────────────────────────────────────────────────────

def collect_weekly_data(db: LearningDB, end_date: date) -> WeeklyReportData:
    """
    從 learning_db 收集最近 7 天（含 end_date）的資料，
    計算本週各項關鍵指標。
    """
    start_date = end_date - timedelta(days=6)

    # ── 每日彙總 ──────────────────────────────────────────────
    daily_logs = db.get_logs_range(start_date, end_date)
    n_trading_days = len(daily_logs)
    total_pnl = sum(l.total_pnl for l in daily_logs)

    # ── 個股預測（H6: 改用公開 API，不再直接存取 _conn）────────
    predictions = db.get_predictions_by_date_range(
        start_date, end_date, only_completed=True
    )

    total = len(predictions)
    wins = sum(1 for p in predictions if p.was_correct)
    stock_win_rate = wins / total if total > 0 else 0.0

    # ── best / worst pick ─────────────────────────────────────
    completed = [p for p in predictions if p.actual_return_pct is not None]

    if completed:
        best = max(completed, key=lambda p: p.actual_return_pct)
        worst = min(completed, key=lambda p: p.actual_return_pct)
        best_pick = {
            "code": best.code,
            "name": best.name,
            "date": best.date,
            "action": best.action,
            "actual_return_pct": best.actual_return_pct,
            "entry_price": best.entry_price,
            "closing_price": best.closing_price,
        }
        worst_pick = {
            "code": worst.code,
            "name": worst.name,
            "date": worst.date,
            "action": worst.action,
            "actual_return_pct": worst.actual_return_pct,
            "entry_price": worst.entry_price,
            "closing_price": worst.closing_price,
        }
    else:
        best_pick = {}
        worst_pick = {}

    # ── 高信心勝率 ────────────────────────────────────────────
    high_conf = [p for p in predictions if p.confidence >= 8]
    high_conf_wins = sum(1 for p in high_conf if p.was_correct)
    high_conf_win_rate = high_conf_wins / len(high_conf) if high_conf else 0.0

    return WeeklyReportData(
        start_date=start_date,
        end_date=end_date,
        n_trading_days=n_trading_days,
        total_pnl=total_pnl,
        stock_win_rate=stock_win_rate,
        best_pick=best_pick,
        worst_pick=worst_pick,
        high_conf_win_rate=high_conf_win_rate,
        daily_logs=daily_logs,
        predictions=predictions,
    )


def format_data_for_ai(data: WeeklyReportData) -> str:
    """
    將 WeeklyReportData 轉成 AI prompt 文字。
    讓 Claude 分析本週策略得失並提出下週建議。
    """
    lines: list[str] = []
    lines.append(f"# 本週 AI 選股績效報告（{data.start_date} ~ {data.end_date}）")
    lines.append("")
    lines.append("## 整體損益")
    lines.append(f"- 交易日數：{data.n_trading_days} 天")
    lines.append(f"- 本週累計損益（元）：{data.total_pnl:+,.0f}")
    lines.append("")
    lines.append("## 選股勝率")
    lines.append(f"- 整體勝率：{data.stock_win_rate:.1%}（共 {len(data.predictions)} 筆預測）")
    lines.append(f"- 高信心（≥8 分）勝率：{data.high_conf_win_rate:.1%}")
    lines.append("")

    if data.best_pick:
        lines.append("## 本週最佳選股")
        lines.append(
            f"- {data.best_pick['code']} {data.best_pick.get('name', '')} "
            f"實際報酬 {data.best_pick['actual_return_pct']:+.2f}%"
            f"（{data.best_pick.get('date', '')}）"
        )

    if data.worst_pick:
        lines.append("## 本週最差選股")
        lines.append(
            f"- {data.worst_pick['code']} {data.worst_pick.get('name', '')} "
            f"實際報酬 {data.worst_pick['actual_return_pct']:+.2f}%"
            f"（{data.worst_pick.get('date', '')}）"
        )
    lines.append("")

    if data.daily_logs:
        lines.append("## 每日彙總")
        for log in data.daily_logs:
            sign = "✅" if log.total_pnl >= 0 else "❌"
            lines.append(
                f"- {log.date} [{log.plan_type}] "
                f"{sign} 損益 {log.total_pnl:+,.0f} ({log.pnl_pct:+.2f}%) "
                f"大盤 {log.market_index_change:+.2f}%  "
                f"勝{log.n_win} 敗{log.n_loss}"
            )
    lines.append("")

    lines.append("## 個股預測明細")
    for p in data.predictions:
        tag = "✅" if p.was_correct else "❌"
        lines.append(
            f"- {tag} {p.date} {p.code} {p.name} [{p.action}] "
            f"信心{p.confidence} 實際{p.actual_return_pct:+.2f}%"
        )
    lines.append("")

    lines.append("---")
    lines.append("請根據以上資料，以 JSON 格式回覆週報，格式如下：")
    lines.append("```json")
    lines.append("{")
    lines.append('  "summary": "本週整體評語（2~4句）",')
    lines.append('  "wins": ["成功案例分析1", "成功案例分析2"],')
    lines.append('  "losses": ["失敗案例分析1", "失敗案例分析2"],')
    lines.append('  "patterns": ["觀察到的規律1", "觀察到的規律2"],')
    lines.append('  "next_week": ["下週策略建議1", "下週策略建議2"]')
    lines.append("}")
    lines.append("```")

    return "\n".join(lines)


def parse_ai_report(raw: str) -> dict:
    """
    解析 AI 回傳的週報 JSON。
    支援 markdown code block 包裹；解析失敗回傳 fallback dict。
    """
    _REQUIRED_KEYS = ("summary", "wins", "losses", "patterns", "next_week")

    # 去掉 markdown code block
    cleaned = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if match:
        cleaned = match.group(1).strip()

    try:
        result = json.loads(cleaned)
        # 確保所有 required keys 都存在
        for key in _REQUIRED_KEYS:
            if key not in result:
                result[key] = [] if key != "summary" else ""
        return result
    except (json.JSONDecodeError, ValueError):
        # fallback：把原始文字放進 summary
        fallback_summary = raw.strip() if raw.strip() else "AI 回覆解析失敗，請檢查原始輸出。"
        return {
            "summary": fallback_summary,
            "wins": [],
            "losses": [],
            "patterns": [],
            "next_week": [],
        }
