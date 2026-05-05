#!/usr/bin/env python3
"""
weekly_report_runner.py — 每週五 14:00 自動執行的週報腳本

功能：
  1. 從 learning_db 收集本週資料
  2. 呼叫 Claude API 生成策略複盤報告
  3. 透過 Telegram 推播週報

排程方式（macOS launchd）：
  ~/Library/LaunchAgents/com.aistock.weekly_report.plist
  每週五 14:00 自動執行
  Log: /tmp/weekly_report.log
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

_PROJECT_DIR = Path(__file__).parent.resolve()
os.chdir(_PROJECT_DIR)
sys.path.insert(0, str(_PROJECT_DIR))

from logger import get_logger

log = get_logger("weekly_report_runner")


def _call_claude(prompt: str) -> str:
    """呼叫 Claude API，回傳原始文字。H7: 改走 ai_client 統一入口（含 retry）。"""
    from ai_client import call_sonnet
    full_prompt = (
        "你是一位專業的台股 AI 量化交易分析師。"
        "以下是本週 AI 選股系統的績效數據，"
        "請根據數據進行深入分析，找出策略優缺點，並給出下週改進建議。\n\n"
        + prompt
    )
    return call_sonnet(full_prompt, max_tokens=2048)


def _format_telegram_report(parsed: dict, data) -> str:
    """將解析後的 AI 報告格式化為 Telegram 推播訊息。"""
    lines = [
        f"📊 <b>本週策略複盤報告</b>",
        f"<i>{data.start_date} ~ {data.end_date}</i>",
        "",
        f"💰 本週損益：<b>{data.total_pnl:+,.0f} 元</b>",
        f"🎯 選股勝率：<b>{data.stock_win_rate:.1%}</b>（{len(data.predictions)} 筆）",
        f"🔥 高信心勝率：<b>{data.high_conf_win_rate:.1%}</b>",
        "",
    ]

    summary = parsed.get("summary", "")
    if summary:
        lines += [f"📝 <b>總評</b>", summary, ""]

    wins = parsed.get("wins", [])
    if wins:
        lines.append("✅ <b>本週亮點</b>")
        for w in wins[:3]:
            lines.append(f"• {w}")
        lines.append("")

    losses = parsed.get("losses", [])
    if losses:
        lines.append("❌ <b>本週失誤</b>")
        for lo in losses[:3]:
            lines.append(f"• {lo}")
        lines.append("")

    patterns = parsed.get("patterns", [])
    if patterns:
        lines.append("🔍 <b>觀察到的規律</b>")
        for p in patterns[:3]:
            lines.append(f"• {p}")
        lines.append("")

    next_week = parsed.get("next_week", [])
    if next_week:
        lines.append("🚀 <b>下週策略建議</b>")
        for nw in next_week[:3]:
            lines.append(f"• {nw}")

    return "\n".join(lines)


def run(end_date: date | None = None) -> None:
    today = end_date or date.today()
    log.info("=== weekly_report_runner 開始 %s ===", today)

    from learning_db import LearningDB
    from weekly_report import collect_weekly_data, format_data_for_ai, parse_ai_report

    db = LearningDB()
    data = collect_weekly_data(db, end_date=today)

    if data.n_trading_days == 0:
        log.warning("本週無交易資料，略過週報。")
        return

    log.info(
        "本週資料：%d 交易日，損益 %.0f，勝率 %.1f%%",
        data.n_trading_days, data.total_pnl, data.stock_win_rate * 100,
    )

    # ── 組 prompt，呼叫 Claude ─────────────────────────────────
    prompt = format_data_for_ai(data)
    log.info("送出週報 prompt（%d 字）...", len(prompt))

    try:
        raw_response = _call_claude(prompt)
        log.info("Claude 回覆（%d 字）", len(raw_response))
    except Exception as e:
        log.error("Claude API 呼叫失敗：%s", e)
        raw_response = ""

    parsed = parse_ai_report(raw_response)

    # ── 存檔 ──────────────────────────────────────────────────
    import json
    report_path = Path(f"data/weekly_report_{today}.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "start_date": str(data.start_date),
                "end_date": str(data.end_date),
                "n_trading_days": data.n_trading_days,
                "total_pnl": data.total_pnl,
                "stock_win_rate": data.stock_win_rate,
                "high_conf_win_rate": data.high_conf_win_rate,
                "ai_report": parsed,
            },
            f, ensure_ascii=False, indent=2,
        )
    log.info("週報存檔：%s", report_path)

    # ── Telegram 推播 ─────────────────────────────────────────
    try:
        from notifier import _send
        msg = _format_telegram_report(parsed, data)
        _send(msg)
        log.info("週報已推播到 Telegram")
    except Exception as e:
        log.error("Telegram 推播失敗：%s", e)

    log.info("=== weekly_report_runner 完成 ===")


if __name__ == "__main__":
    run()
