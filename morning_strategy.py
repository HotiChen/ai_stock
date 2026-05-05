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

import json
import os
import random
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# ── 確保 CWD 是專案根目錄 ─────────────────────────────────────────────────────
_PROJECT_DIR = Path(__file__).parent.resolve()
os.chdir(_PROJECT_DIR)
sys.path.insert(0, str(_PROJECT_DIR))

from logger import get_logger

log = get_logger("morning_strategy")


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


def _get_name_map(api) -> dict[str, str]:
    """從 Shioaji 取得股票名稱對照表。"""
    import config
    result = dict(config.STOCK_NAMES)
    if api is None:
        return result
    for exchange in ("TSE", "OTC", "OES"):
        try:
            for c in getattr(api.Contracts.Stocks, exchange, []):
                result[c.code] = c.name
        except Exception:
            pass
    return result


def build_candidates(api=None) -> list[dict]:
    """建立候選股清單，邏輯與 app.py 的 _build_candidates 相同。"""
    from themes import THEME_GROUPS
    from chip_data import (
        fetch_institutional_investors,
        fetch_margin_trading,
        get_continuous_buy_days,
    )

    raw: list[dict] = []

    # ── 1. 從 ai_log 抓最近紀錄 ──────────────────────────────
    log_path = Path("data/ai_log.jsonl")
    if log_path.exists():
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    d = e.get("decision", {})
                    if d.get("stock_code"):
                        raw.append({
                            "code":        d["stock_code"],
                            "name":        d.get("stock_code"),
                            "close":       0.0,
                            "change_rate": 0.0,
                            "analysis":    d.get("reason", ""),
                        })
                except Exception:
                    pass

    seen: set[str] = set()
    deduped: list[dict] = []
    for c in reversed(raw):
        if c["code"] not in seen:
            seen.add(c["code"])
            deduped.append(c)
        if len(deduped) >= 10:
            break

    # ── 2. 從所有主題池隨機補 20 支 ──────────────────────────
    all_theme_codes = list({
        code
        for codes in THEME_GROUPS.values()
        for code in codes
    })
    random.shuffle(all_theme_codes)
    for code in all_theme_codes:
        if code not in seen and len(deduped) < 30:
            seen.add(code)
            deduped.append({
                "code":        code,
                "name":        code,
                "close":       0.0,
                "change_rate": 0.0,
                "analysis":    "主題池補充（無個股分析，請依技術面自行評估）",
            })

    # ── 3. 補齊名稱與即時股價 ────────────────────────────────
    nm = _get_name_map(api)
    for c in deduped:
        c["name"] = nm.get(c["code"], c["code"])
        if api:
            try:
                import threading
                result_holder = {}

                def _fetch(code=c["code"]):
                    try:
                        contract = api.Contracts.Stocks.get(code)
                        if contract:
                            snaps = api.snapshots([contract])
                            if snaps:
                                result_holder["close"]       = float(snaps[0].close)
                                result_holder["change_rate"] = float(snaps[0].change_rate)
                    except Exception:
                        pass

                t = threading.Thread(target=_fetch, daemon=True)
                t.start()
                t.join(timeout=6)
                c["close"]       = result_holder.get("close", 0.0)
                c["change_rate"] = result_holder.get("change_rate", 0.0)
            except Exception:
                pass

    # ── 4. 抓籌碼資料 ────────────────────────────────────────
    today_str = date.today().strftime("%Y%m%d")
    try:
        inst_data   = fetch_institutional_investors(today_str)
        margin_data = fetch_margin_trading(today_str)
        log.info("籌碼資料：三大法人 %d 筆，融資融券 %d 筆", len(inst_data), len(margin_data))
    except Exception as e:
        log.warning("籌碼資料取得失敗：%s", e)
        inst_data   = {}
        margin_data = {}

    # ── 5. 標注籌碼 + 計算排序分數 ───────────────────────────
    for c in deduped:
        code = c["code"]
        chip_parts: list[str] = []
        chip_score = 0

        inst = inst_data.get(code)
        if inst:
            foreign_net = inst.get("foreign_net", 0)
            trust_net   = inst.get("investment_trust_net", 0)
            if foreign_net != 0:
                chip_parts.append(f"外資{'+' if foreign_net > 0 else ''}{foreign_net:.0f}張")
                chip_score += min(foreign_net / 500, 5)
            if trust_net != 0:
                chip_parts.append(f"投信{'+' if trust_net > 0 else ''}{trust_net:.0f}張")
                chip_score += min(trust_net / 100, 3)
            if inst.get("total_net", 0) > 0:
                chip_score += 1

        try:
            cont = get_continuous_buy_days(code, today_str, days=5)
            foreign_days = cont.get("foreign_continuous_buy", 0)
            trust_days   = cont.get("investment_trust_continuous_buy", 0)
            if foreign_days >= 2:
                chip_parts.append(f"外資連買{foreign_days}天")
                chip_score += foreign_days * 1.5
            if trust_days >= 2:
                chip_parts.append(f"投信連買{trust_days}天")
                chip_score += trust_days * 1.0
        except Exception:
            pass

        margin = margin_data.get(code)
        if margin:
            margin_prev   = margin.get("margin_prev", 0)
            margin_change = margin.get("margin_change", 0)
            if margin_prev > 0:
                margin_pct = margin_change / margin_prev * 100
                if abs(margin_pct) >= 3:
                    chip_parts.append(f"融資{'↑' if margin_pct > 0 else '↓'}{abs(margin_pct):.1f}%")
                if margin_pct > 10:
                    chip_score -= 2

        if chip_parts:
            chip_summary = "｜".join(chip_parts)
            orig = c.get("analysis", "")
            c["analysis"]   = f"【籌碼】{chip_summary}　{orig}".strip()
        c["chip_score"] = chip_score

    # ── 6. 依籌碼分數排序 ────────────────────────────────────
    deduped.sort(key=lambda x: x.get("chip_score", 0), reverse=True)

    n_chip  = len([c for c in deduped if c.get("chip_score", 0) > 0])
    n_theme = len([c for c in deduped if "主題池補充" in c.get("analysis", "")])
    log.info("候選股建立完成：共 %d 支｜有籌碼=%d｜主題補充=%d", len(deduped), n_chip, n_theme)
    return deduped


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

    # ── 建立候選股 + 生成策略 ─────────────────────────────────
    api = _get_api()
    try:
        candidates = build_candidates(api)
        log.info("開始生成三套策略計劃...")
        plan_set = generate_strategy_plans(goal, current_value, candidates, portfolio=portfolio)
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
