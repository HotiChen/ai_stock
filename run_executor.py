#!/usr/bin/env python3
"""
背景執行腳本 - 每 10 分鐘跑一次策略循環

使用方式：
    python3 run_executor.py

會自動從 data/research.db 讀取目前 active 的計劃，執行分析與模擬買賣。
"""
from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

from research_db import init_db, load_active_execution
from strategy_executor import get_execution_status, run_cycle
from strategy_tracker import (
    calc_daily_target,
    check_daily_target,
    generate_daily_review,
    load_goal,
    save_daily_entry,
    DailyEntry,
)
from research_db import load_execution_records, load_daily_summaries, save_daily_summary, DailySummaryRow

DB_PATH   = "data/research.db"
GOAL_PATH = "data/strategy_goal.json"
ENTRIES_PATH = "data/strategy_entries.jsonl"
INTERVAL  = 600   # 10 分鐘

# 台灣交易時間 09:00–13:30（模擬模式仍可在盤外執行分析）
_MARKET_OPEN  = dtime(9, 0)
_MARKET_CLOSE = dtime(13, 30)

_running = True


def _handle_signal(sig, frame):
    global _running
    print("\n[executor] 收到停止訊號，正在結束...")
    _running = False


def _is_market_hours() -> bool:
    now = datetime.now().time()
    return _MARKET_OPEN <= now <= _MARKET_CLOSE


def _do_daily_summary(execution_id: str) -> None:
    """收盤後執行每日總結並存入 strategy_entries.jsonl。"""
    goal = load_goal(GOAL_PATH)
    if goal is None:
        return

    records = load_execution_records(execution_id, DB_PATH)
    today = datetime.now().date()
    today_records = [r for r in records if r.recorded_at.date() == today]
    total_pnl = sum(r.simulated_pnl for r in today_records)
    daily_target = calc_daily_target(goal)
    target_met = check_daily_target(total_pnl, daily_target)
    trades = [f"{r.action.upper()} {r.code} {r.name} {r.quantity}張 @{r.price:.0f}" for r in today_records]

    print(f"[executor] 收盤總結　今日損益={total_pnl:+,.0f}　達標={target_met}")

    review_result = generate_daily_review(
        goal=goal, today_pnl=total_pnl,
        daily_target=daily_target, trades=trades, yesterday_plan="",
    )

    entry = DailyEntry(
        date=today, pnl=total_pnl,
        trades_count=len(today_records),
        review=review_result["review"],
        tomorrow_plan=review_result["tomorrow_plan"],
        target_met=target_met,
    )
    save_daily_entry(entry, ENTRIES_PATH)

    save_daily_summary(DailySummaryRow(
        execution_id=execution_id, date=today,
        total_pnl=total_pnl, target_met=target_met,
        review=review_result["review"],
        next_day_plan=review_result["tomorrow_plan"],
    ), DB_PATH)
    print(f"[executor] 檢討：{review_result['review']}")
    print(f"[executor] 明日計畫：{review_result['tomorrow_plan']}")


def main():
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    Path("data").mkdir(exist_ok=True)
    init_db(DB_PATH)

    print(f"[executor] 啟動，每 {INTERVAL//60} 分鐘循環一次。Ctrl+C 停止。")

    last_daily_summary_date = None

    while _running:
        execution = load_active_execution(DB_PATH)
        if execution is None:
            print(f"[executor] {datetime.now().strftime('%H:%M:%S')} 沒有 active 計劃，等待...")
            time.sleep(INTERVAL)
            continue

        exec_id = execution.execution_id
        now = datetime.now()
        print(f"\n[executor] {now.strftime('%H:%M:%S')} 執行循環　計劃={execution.plan_type}　ID={exec_id}")

        try:
            result = run_cycle(exec_id, DB_PATH)
            print(f"[executor] 情緒={result.market_sentiment}　"
                  f"動作={len(result.actions_taken)}筆　"
                  f"略過={result.skipped_codes}")
            for act in result.actions_taken:
                print(f"  → {act['action'].upper()} {act['code']} {act.get('name','')} "
                      f"@{act.get('price',0):.0f}　{act.get('reason','')[:40]}")
        except Exception as e:
            print(f"[executor] 循環失敗：{e}")

        # 收盤後做一次每日總結（每天只做一次）
        today = now.date()
        if now.time() >= _MARKET_CLOSE and last_daily_summary_date != today:
            try:
                _do_daily_summary(exec_id)
                last_daily_summary_date = today
            except Exception as e:
                print(f"[executor] 每日總結失敗：{e}")

        for _ in range(INTERVAL):
            if not _running:
                break
            time.sleep(1)

    print("[executor] 已停止。")


if __name__ == "__main__":
    main()
