#!/usr/bin/env python3
"""
morning_strategy.py — 每日 08:30 自動執行的策略生成腳本

功能：
  1. 建立候選股清單（ai_log + 主題池 + 籌碼排序）
  2. 呼叫 generate_strategy_plans 產生三套策略
  3. 透過 send_morning_push 推播到 Telegram

排程方式（macOS launchd）：
  已設定 ~/Library/LaunchAgents/com.aistock.morning_strategy.plist
  每日 08:30 週一至週五自動執行
  Log: /tmp/morning_strategy.log
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# ── 確保 CWD 是專案根目錄 ─────────────────────────────────────────────────────
_PROJECT_DIR = Path(__file__).parent.resolve()
os.chdir(_PROJECT_DIR)
sys.path.insert(0, str(_PROJECT_DIR))

from logger import get_logger

log = get_logger("morning_strategy")


def _load_last_weekly_hint() -> list[str]:
    """讀取最新一份週報的 next_week 建議（僅週一使用）。"""
    import json
    data_dir = Path("data")
    reports = sorted(data_dir.glob("weekly_report_*.json"), reverse=True)
    if not reports:
        return []
    try:
        with reports[0].open(encoding="utf-8") as f:
            report = json.load(f)
        hints = report.get("ai_report", {}).get("next_week", [])
        log.info("載入週報建議來源：%s，共 %d 條", reports[0].name, len(hints))
        return hints if isinstance(hints, list) else []
    except Exception as e:
        log.warning("讀取週報建議失敗：%s", e)
        return []


def _is_trading_day() -> bool:
    """週一到週五才執行（不含假日，未做完整節假日判斷）。"""
    return datetime.now().weekday() < 5


def _get_api():
    """登入 Shioaji，回傳 api 物件；失敗回傳 None。"""
    try:
        import shioaji as sj
        simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"
        api = sj.Shioaji(simulation=simulation)
        api.login(
            api_key=os.getenv("SHIOAJI_API_KEY"),
            secret_key=os.getenv("SHIOAJI_SECRET_KEY"),
            fetch_contract=True,
        )
        log.info("Shioaji 登入成功 simulation=%s", simulation)
        return api
    except Exception as e:
        log.warning("Shioaji 登入失敗，股價將為 0：%s", e)
        return None


def build_candidates(api=None) -> list[dict]:
    """建立候選股清單。H11: 委派到 candidate_builder 統一入口。"""
    from candidate_builder import build_candidates as _build
    return _build(api=api)


def run() -> None:
    log.info("=== morning_strategy 開始 %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))

    if not _is_trading_day():
        log.info("今日非交易日，略過。")
        return

    from strategy_tracker import load_goal
    from strategy_planner import generate_strategy_plans
    from telegram_bot import send_morning_push

    _GOAL_PATH = Path("data/strategy_goal.json")

    # ── 讀取目標 ──────────────────────────────────────────────
    goal = None
    if _GOAL_PATH.exists():
        try:
            goal = load_goal(str(_GOAL_PATH))
        except Exception as e:
            log.warning("load_goal 失敗：%s", e)

    if goal is None:
        log.error("找不到策略目標檔 %s，無法生成策略。", _GOAL_PATH)
        return

    # ── 計算目前資本 ──────────────────────────────────────────
    from strategy_tracker import load_daily_entries, calc_progress
    try:
        entries = load_daily_entries(str(_GOAL_PATH).replace("strategy_goal", "strategy_entries"))
        total_pnl = sum(e.pnl for e in entries if e.pnl is not None)
        current_value = goal.initial_capital + total_pnl
    except Exception:
        current_value = goal.initial_capital
    log.info("current_value=%.0f", current_value)

    # ── 載入現有持倉 ─────────────────────────────────────────
    from portfolio import SimulatedPortfolio
    _portfolio_path = Path("data/portfolio.json")
    portfolio = SimulatedPortfolio.load(str(_portfolio_path)) if _portfolio_path.exists() else None
    if portfolio and portfolio.get_positions():
        log.info("傳入持倉 %d 檔，AI 將考慮 hold/add/reduce/close/switch", len(portfolio.get_positions()))

    # ── 週一才載入上週 AI 複盤建議 ────────────────────────────
    weekly_hint: list[str] | None = None
    if datetime.now().weekday() == 0:   # 0 = 週一
        weekly_hint = _load_last_weekly_hint()
        if weekly_hint:
            log.info("週一模式：載入上週複盤建議 %d 條", len(weekly_hint))

    # ── 建立候選股 + 生成策略 ─────────────────────────────────
    api = _get_api()
    try:
        candidates = build_candidates(api)
        log.info("開始生成三套策略計劃...（候選股 %d 檔）", len(candidates))
        plan_set = generate_strategy_plans(
            goal, current_value, candidates,
            portfolio=portfolio, weekly_hint=weekly_hint,
        )

        # generate_strategy_plans 有多條 return None 的路徑（LLM 回應解析失敗、
        # 候選股為空、券商登入失敗導致股價全 0 等）。推播端的 save_pending_planset
        # 會直接取 plan_set.aggressive，傳 None 進去會炸 AttributeError，
        # 而且是在「把待執行計畫存檔」這一步炸，可能留下壞掉的 pending 狀態。
        # 沒有計畫就不推播，但一定要留下紀錄，否則排程會靜默失敗。
        if plan_set is None:
            # 不臆測原因：候選股是 0 或不是 0，指向的問題完全不同。
            # 給出實際數字並指向上游日誌，比猜一個常見原因更有用。
            hint = (
                "候選股為 0，通常是券商登入失敗或盤前無報價——請檢查上方的 Shioaji 日誌"
                if not candidates
                else "候選股正常，問題多半出在 LLM 呼叫（金鑰失效、逾時、回應無法解析）"
                     "——請檢查上方 call_sonnet / ANTHROPIC_API_KEY 相關訊息"
            )
            log.error("策略生成失敗（回傳 None），略過推播。候選股 %d 檔；%s。",
                      len(candidates), hint)
            return

        log.info("策略生成完成，推播到 Telegram...")
        send_morning_push(briefing=None, plan_set=plan_set, starting_capital=current_value)
        log.info("=== morning_strategy 完成 ===")
    except Exception as e:
        log.error("morning_strategy 執行失敗：%s", e, exc_info=True)
        # 即使失敗也推播錯誤通知
        try:
            from notifier import _send
            _send(f"⚠️ <b>08:30 策略生成失敗</b>\n錯誤：{e}")
        except Exception:
            pass


if __name__ == "__main__":
    run()
