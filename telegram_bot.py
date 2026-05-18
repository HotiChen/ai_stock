from __future__ import annotations

"""
Telegram Bot — long polling, Reply Keyboard handler.

Buttons:
  📊 今日狀態   → account summary + today's P&L
  💼 持倉       → open positions from daily_trades
  📈 選股計劃   → today's approved picks from daily_plans
  ⚡ 快速下單   → inline confirm before place_stock_order
  🛡️ 停損設定  → prompt for stop-loss price input
  ❓ 說明       → usage help

Run standalone: python3 telegram_bot.py
Or via start.sh as a background process.
"""

import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

from logger import get_logger
from research_db import (
    init_db, load_daily_plan, load_daily_trades,
    reject_pick_from_plan, reject_all_picks_from_plan,
)
from daily_tracker import DailyTrackRecord, PlanResult, save_day_record
from portfolio import SimulatedPortfolio
from sim_plan_store import planset_to_dict, planset_from_dict
from sim_position_store import SimPosition, save_sim_positions
import json

from atomic_json import atomic_write_json
from market_index import fetch_market_index_change

log = get_logger(__name__)

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")
# H9: 雙重驗證 — TELEGRAM_USER_ID 設定後，訊息發送者的 user_id 也必須吻合
USER_ID     = os.getenv("TELEGRAM_USER_ID", "")
DB_PATH     = os.getenv("DB_PATH", "data/research.db")
_POLL_TIMEOUT = 30  # long polling seconds

# ── Shioaji singleton（避免在 callback 裡重複 login）─────────────────────────
_sj_api = None

def _get_sj_api():
    """取得（或建立）Shioaji singleton，避免每次 callback 都重新 login。"""
    global _sj_api
    if _sj_api is not None:
        return _sj_api
    try:
        import shioaji as _sj
        _sj_api = _sj.Shioaji(simulation=True)
        _sj_api.login(os.getenv("SHIOAJI_API_KEY", ""), os.getenv("SHIOAJI_SECRET_KEY", ""))
        log.info("Shioaji singleton initialized")
    except Exception as e:
        log.warning("Shioaji singleton init failed: %s", e)
        _sj_api = None
    return _sj_api


# ── Telegram API helpers ───────────────────────────────────────────────────────

def _post(method: str, payload: dict) -> dict:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload,
            timeout=15,
        )
        return resp.json()
    except Exception as e:
        log.error("Telegram %s failed: %s", method, e)
        return {}


def send_text(chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _post("sendMessage", payload)


def send_main_menu(chat_id: str, text: str = "請選擇功能：") -> None:
    keyboard = {
        "keyboard": [
            ["📊 今日狀態", "💼 持倉"],
            ["📈 選股計劃", "⚡ 快速下單"],
            ["🎯 今日當沖預測", "🛡️ 停損設定"],
            ["❓ 說明", "🚨 緊急暫停"],
            ["🔄 撤銷所有委託", "💥 一鍵全平倉"],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
    }
    send_text(chat_id, text, reply_markup=keyboard)


# ── Handlers ───────────────────────────────────────────────────────────────────

def handle_status(chat_id: str) -> None:
    from sim_position_store import load_sim_positions
    from daily_tracker import load_day_record

    now = datetime.now()
    positions = load_sim_positions()
    record    = load_day_record(date.today())
    trades    = load_daily_trades()

    plan_zh   = {"aggressive": "🚀 衝刺版", "balanced": "⚖️ 均衡版", "conservative": "🛡️ 保守版"}
    plan_str  = plan_zh.get(record.chosen_plan_type, "—") if record else "尚未選擇策略"

    total_cost = 0.0
    for p in positions:
        total_cost += (p.shares * (p.entry_price or 0)) if p.is_fractional else (p.quantity * 1000 * (p.entry_price or 0))

    net_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

    lines = [
        f"📊 <b>今日狀態</b>",
        f"━━━━━━━━━━━━━━━━",
        f"🗓 {now.strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"📋 今日策略：<b>{plan_str}</b>",
        f"💼 模擬持倉：{len(positions)} 檔",
        f"💸 合計投入：<b>{total_cost:,.0f}</b> 元",
        f"📈 今日損益：<b>{net_pnl:+,.0f}</b> 元",
        f"",
        f"（詳細損益請按 <b>收盤結算</b> 計算）",
    ]
    send_text(chat_id, "\n".join(lines))


def handle_holdings(chat_id: str) -> None:
    trades = load_daily_trades()

    if not trades:
        send_text(chat_id, "💼 今日尚無交易紀錄。\n\n請先選擇今日策略（等 08:30 推播或點「選股計劃」）。")
        return

    lines = ["💼 <b>今日交易紀錄</b>", "━━━━━━━━━━━━━━━━"]
    for t in trades:
        code     = t.get("code", "")
        name     = t.get("name", "")
        action   = t.get("action", "")
        quantity = t.get("quantity", 0)
        price    = t.get("price", 0.0)
        pnl      = t.get("pnl", 0) or 0
        action_zh = {"buy": "買入", "sell": "賣出"}.get(action, action)
        lines.append(
            f"• <b>{code}</b> {name}｜{action_zh}｜"
            f"{quantity} 股｜@{price:.1f}｜損益 {pnl:+.0f}"
        )
    send_text(chat_id, "\n".join(lines))


def handle_plan(chat_id: str) -> None:
    # 優先顯示今日三套策略（新流程）
    plan_set = load_pending_planset()

    if plan_set is not None:
        # 顯示三套計劃摘要 + 選擇按鈕
        agg = plan_set.aggressive
        bal = plan_set.balanced
        con = plan_set.conservative

        lines = ["📈 <b>今日三套策略計劃</b>", "━━━━━━━━━━━━━━━━"]
        for plan, emoji, label in [
            (agg, "🚀", "衝刺版"),
            (bal, "⚖️", "均衡版"),
            (con, "🛡️", "保守版"),
        ]:
            picks_str = "、".join(f"{p.code}" for p in plan.picks[:3]) if plan.picks else "空手觀望"
            if len(plan.picks) > 3:
                picks_str += f"…等 {len(plan.picks)} 檔"
            lines.append(
                f"\n{emoji} <b>{label}</b>　預期 +{plan.expected_return_pct:.1f}%\n"
                f"  選股：{picks_str}"
            )

        keyboard = {
            "inline_keyboard": [[
                {"text": f"🚀 衝刺 +{agg.expected_return_pct:.0f}%", "callback_data": "select_plan:aggressive"},
                {"text": f"⚖️ 均衡 +{bal.expected_return_pct:.0f}%", "callback_data": "select_plan:balanced"},
                {"text": f"🛡️ 保守 +{con.expected_return_pct:.0f}%", "callback_data": "select_plan:conservative"},
            ]]
        }
        send_text(chat_id, "\n".join(lines), reply_markup=keyboard)
        return

    # Fallback: 使用 legacy daily_plan
    picks = load_daily_plan()

    if not picks:
        send_text(chat_id, "📈 今日尚無選股計劃。\n\n策略將於 08:30 自動推播，或在 Streamlit 手動生成。")
        return

    lines = ["📈 <b>今日選股計劃</b>", "━━━━━━━━━━━━━━━━"]
    for p in picks:
        code = p.get("code", "")
        name = p.get("name", "")
        conf = p.get("confidence", "")
        lines.append(f"• <b>{code}</b> {name}　信心度 {conf}")
    send_text(chat_id, "\n".join(lines))


def handle_quick_order(chat_id: str) -> None:
    """快速下單 → 引導使用者透過「選股計劃」選擇策略。"""
    from sim_position_store import load_sim_positions
    positions = load_sim_positions()

    if positions:
        # 已有持倉，提示去結算
        send_text(
            chat_id,
            "⚡ <b>今日策略已執行</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"目前有 {len(positions)} 檔模擬持倉。\n\n"
            "收盤後請按「📊 收盤結算」計算損益。",
            reply_markup={"inline_keyboard": [[
                {"text": "📊 收盤結算", "callback_data": "settle_now"},
            ]]}
        )
    else:
        # 尚未選策略，引導去選股計劃
        plan_set = load_pending_planset()
        if plan_set is None:
            send_text(chat_id, "⚡ 今日尚無策略計劃。\n\n策略將於 08:30 自動推播。")
            return
        agg = plan_set.aggressive
        bal = plan_set.balanced
        con = plan_set.conservative
        send_text(
            chat_id,
            "⚡ <b>請選擇今日策略</b>\n━━━━━━━━━━━━━━━━\n選擇後系統將記錄模擬持倉：",
            reply_markup={"inline_keyboard": [[
                {"text": f"🚀 衝刺 +{agg.expected_return_pct:.0f}%", "callback_data": "select_plan:aggressive"},
                {"text": f"⚖️ 均衡 +{bal.expected_return_pct:.0f}%", "callback_data": "select_plan:balanced"},
                {"text": f"🛡️ 保守 +{con.expected_return_pct:.0f}%", "callback_data": "select_plan:conservative"},
            ]]}
        )


def handle_stop_loss(chat_id: str) -> None:
    send_text(
        chat_id,
        "🛡️ <b>停損設定</b>\n━━━━━━━━━━━━━━━━\n"
        "請輸入停損指令，格式：\n\n"
        "<code>停損 2330 540</code>\n\n"
        "（代號 + 停損價格）\n\n"
        "⚠️ <b>重要提醒</b>：目前停損為<b>提醒功能</b>，系統不會自動賣出。\n"
        "觸發停損時會推播通知，請手動執行賣出操作。"
    )


_STOP_LOSS_PATH = Path("data/stop_loss.json")


def _load_stop_losses() -> dict[str, float]:
    """H5: 從磁碟讀取所有停損設定（{code: price}）。"""
    from atomic_json import atomic_read_json
    data = atomic_read_json(_STOP_LOSS_PATH)
    if isinstance(data, dict):
        return {k: float(v) for k, v in data.items()}
    return {}


def _save_stop_loss(code: str, price: float) -> None:
    """H5: 原子寫入停損設定，重啟後依然保留。"""
    from atomic_json import atomic_write_json, atomic_read_json
    data = atomic_read_json(_STOP_LOSS_PATH)
    stops = data if isinstance(data, dict) else {}
    stops[code] = price
    atomic_write_json(_STOP_LOSS_PATH, stops)


def _handle_set_stop_loss(chat_id: str, text: str) -> None:
    """Parse '停損 CODE PRICE' and persist to disk."""
    _FORMAT_HINT = (
        "⚠️ 格式錯誤，請輸入：\n\n"
        "<code>停損 2330 540</code>\n\n"
        "（代號 + 停損價格）"
    )
    parts = text.split()
    if len(parts) != 3:
        send_text(chat_id, _FORMAT_HINT)
        return
    code = parts[1]
    try:
        price = float(parts[2])
    except ValueError:
        send_text(chat_id, _FORMAT_HINT)
        return

    _save_stop_loss(code, price)   # H5: 持久化到 data/stop_loss.json

    send_text(
        chat_id,
        f"🛡️ <b>停損已設定</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"股票：{code}\n"
        f"停損價：{price}\n\n"
        f"⚠️ 注意：此設定僅更新提醒，系統不會自動下單。",
    )


def handle_daytrading(chat_id: str) -> None:
    send_text(chat_id, "⏳ 分析中，請稍候…")
    try:
        from daytrading_report import build_daytrading_report
        report = build_daytrading_report(api=_get_sj_api())
    except Exception as e:
        log.error("handle_daytrading error: %s", e)
        report = "⚠️ 當沖預測產生失敗，請稍後再試。"
    send_text(chat_id, report)


def handle_help(chat_id: str) -> None:
    send_text(
        chat_id,
        "❓ <b>操作說明</b>\n━━━━━━━━━━━━━━━━\n"
        "📊 今日狀態　→ 帳戶總覽、損益\n"
        "💼 持倉　　　→ 今日持倉明細\n"
        "📈 選股計劃　→ AI 今日選股清單\n"
        "⚡ 快速下單　→ 執行今日計劃\n"
        "🎯 今日當沖預測 → 今日候選股當沖評分 + 預測勝率\n"
        "🛡️ 停損設定　→ 設定個股停損價\n"
        "🚨 緊急暫停　→ 立即停止系統，今日不再下單\n"
        "🔄 撤銷委託　→ 取消今日所有未成交委託\n"
        "💥 一鍵平倉　→ 市價賣出所有持倉\n\n"
        "排程：\n"
        "  08:30 盤前 AI 分析\n"
        "  09:00 開盤下單\n"
        "  09:05 開盤推播今日當沖預測\n"
        "  13:35 收盤總結\n\n"
        "查股：直接輸入代號（2330）或名稱（台積電）"
    )


def handle_emergency_halt(chat_id: str) -> None:
    from halt import halt, is_halted
    if is_halted():
        send_text(chat_id,
            "⚠️ 系統已經是暫停狀態。\n\n"
            "傳 <code>恢復系統</code> 可重新啟用。"
        )
        return

    halt(reason="telegram_manual")
    from notifier import _send
    _send("🚨 <b>緊急暫停！</b>\n系統已停止，今日不再執行任何下單。")
    send_text(chat_id,
        "🚨 <b>緊急暫停已啟動</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        "✅ HALT flag 已寫入\n"
        "✅ 今日不再下單\n\n"
        "傳 <code>恢復系統</code> 可重新啟用。"
    )


def handle_cancel_all(chat_id: str) -> None:
    send_text(chat_id,
        "🔄 <b>撤銷所有委託</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        "⚠️ 確定要撤銷今日所有未成交委託嗎？\n\n"
        "請按下方按鈕確認：",
        reply_markup={"inline_keyboard": [[
            {"text": "✅ 確認撤銷", "callback_data": "cancel_all_confirm"},
            {"text": "❌ 取消",     "callback_data": "cancel_all_abort"},
        ]]}
    )


def handle_liquidate_all(chat_id: str) -> None:
    send_text(chat_id,
        "💥 <b>一鍵全平倉</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        "⚠️ 確定要以市價出清所有庫存嗎？此操作無法復原！\n\n"
        "請按下方按鈕確認：",
        reply_markup={"inline_keyboard": [[
            {"text": "💥 確認市價全平倉", "callback_data": "liquidate_all_confirm"},
            {"text": "❌ 取消",     "callback_data": "liquidate_all_abort"},
        ]]}
    )


_PORTFOLIO_PATH    = "data/portfolio.json"
_PENDING_PLAN_PATH = "data/pending_planset.json"


# ── Pending planset: save / load ───────────────────────────────────────────────

def save_pending_planset(plan_set, path: str = _PENDING_PLAN_PATH) -> None:
    """早晨推播時把 PlanSet 存起來，等使用者選擇後執行。"""
    data = planset_to_dict(plan_set)
    data["created_at"] = datetime.now().isoformat()
    atomic_write_json(path, data)


def load_pending_planset(path: str = _PENDING_PLAN_PATH):
    """載入今日待執行的 PlanSet。找不到回傳 None。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        plan_set = planset_from_dict(data)
        # 把 created_at 附加到 plan_set 物件（若原本不含此屬性）
        if plan_set is not None and not hasattr(plan_set, "created_at"):
            plan_set.created_at = data.get("created_at")
        elif plan_set is not None and data.get("created_at"):
            # 覆寫（若物件有預設值）
            plan_set.created_at = data.get("created_at")
        return plan_set
    except Exception:
        return None


# ── Price fetch ────────────────────────────────────────────────────────────────

def _fetch_stock_price(code: str) -> float:
    """用 yfinance 抓台股現價（code.TW）。失敗回傳 0.0（模擬模式繼續執行）。"""
    try:
        import yfinance as yf  # type: ignore
        ticker = yf.Ticker(f"{code}.TW")
        hist   = ticker.history(period="2d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except Exception:
        pass
    return 0.0


# ── Plan execution ────────────────────────────────────────────────────────────

def execute_plan(plan, portfolio: SimulatedPortfolio, entry_date) -> list[str]:
    """把 StrategyPlan 的 picks 執行進 SimulatedPortfolio。
    回傳每一筆的執行結果文字（成功 / 失敗）。
    """
    messages: list[str] = []

    for pick in plan.picks:
        # hold：不動作
        if pick.action == "hold":
            messages.append(f"⚪ {pick.code} {pick.name} — 維持持倉不動")
            continue

        try:
            price = _fetch_stock_price(pick.code)
            qty_str = f"{pick.quantity}張" if not pick.is_fractional else f"{pick.shares}股"
            price_str = f"@{price:.1f}" if price > 0 else "@模擬價"

            if pick.action in ("open", "buy"):
                portfolio.open_position(
                    code=pick.code, name=pick.name,
                    quantity=pick.quantity, price=price,
                    is_fractional=pick.is_fractional, shares=pick.shares,
                    reason=pick.reason, entry_date=entry_date,
                )
                messages.append(f"🟢 {pick.code} {pick.name} 建倉 {qty_str} {price_str}")

            elif pick.action == "add":
                portfolio.add_position(
                    code=pick.code, quantity=pick.quantity, price=price,
                    is_fractional=pick.is_fractional, shares=pick.shares,
                )
                messages.append(f"🔵 {pick.code} {pick.name} 加碼 {qty_str} {price_str}")

            elif pick.action == "close":
                portfolio.close_position(code=pick.code, price=price)
                messages.append(f"🔴 {pick.code} {pick.name} 全部平倉 {price_str}")

            elif pick.action == "reduce":
                portfolio.reduce_position(
                    code=pick.code, quantity=pick.quantity, price=price,
                    is_fractional=pick.is_fractional, shares=pick.shares,
                )
                messages.append(f"🟡 {pick.code} {pick.name} 減碼 {qty_str} {price_str}")

            elif pick.action == "switch":
                if pick.switch_from:
                    from_price = _fetch_stock_price(pick.switch_from)
                    try:
                        portfolio.close_position(code=pick.switch_from, price=from_price)
                        messages.append(f"🔄 {pick.switch_from} 換出 @{from_price:.1f}")
                    except Exception as e:
                        messages.append(f"⚠️ {pick.switch_from} 換出失敗：{e}")
                portfolio.open_position(
                    code=pick.code, name=pick.name,
                    quantity=pick.quantity, price=price,
                    is_fractional=pick.is_fractional, shares=pick.shares,
                    reason=pick.reason, entry_date=entry_date,
                )
                messages.append(f"🔄 {pick.code} {pick.name} 換入 {qty_str} {price_str}")

        except Exception as e:
            messages.append(f"⚠️ {pick.code} 執行失敗：{e}")

    return messages


# ── Morning push: briefing + 3 strategy plans ─────────────────────────────────

_PLAN_EMOJI = {
    "aggressive":   "🚀",
    "balanced":     "⚖️",
    "conservative": "🛡️",
}
_PLAN_LABEL = {
    "aggressive":   "衝刺版",
    "balanced":     "均衡版",
    "conservative": "保守版",
}
_OUTLOOK_LABEL = {
    "bullish": "📈 偏多",
    "bearish": "📉 偏空",
    "neutral": "↔️ 中性",
}


def format_briefing_message(briefing) -> str:
    """把 MorningBriefing 轉為 Telegram HTML 訊息。"""
    md = briefing.market_data
    outlook = _OUTLOOK_LABEL.get(briefing.market_outlook, briefing.market_outlook)

    def _f(val, fmt=",.0f", suffix=""):
        return f"{val:{fmt}}{suffix}" if val is not None else "N/A"

    lines = [
        f"🌅 <b>{briefing.date} 早晨市場簡報</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "【台灣大盤】",
        f"  加權指數：<b>{_f(md.twse_index)}</b> 點",
        f"  外資淨買：<b>{_f(md.foreign_net, '+.1f', ' 億')}</b>" if md.foreign_net is not None else "  外資淨買：N/A",
        f"  投信淨買：<b>{_f(md.trust_net, '+.1f', ' 億')}</b>" if md.trust_net is not None else "  投信淨買：N/A",
        "",
        "【美股 & 恐慌指標】",
        f"  S&P 500：{_f(md.sp500, ',.2f')}　NASDAQ：{_f(md.nasdaq, ',.2f')}",
        f"  SOX 費半：{_f(md.sox, ',.2f')}　VIX：{_f(md.vix, '.2f')}",
        "",
        f"【Gemini 分析】{outlook}",
        briefing.ai_analysis,
        "",
        "⚠️ 主要風險：" + "、".join(briefing.key_risks[:3]),
        "📌 今日重點：" + " / ".join(briefing.sector_focus[:3]),
    ]
    return "\n".join(lines)


def format_plan_card(plan) -> str:
    """把單一 StrategyPlan 轉為 Telegram HTML 訊息。"""
    emoji = _PLAN_EMOJI.get(plan.plan_type, "📋")
    label = _PLAN_LABEL.get(plan.plan_type, plan.plan_type)
    deployed = f"{plan.capital_deployed_pct * 100:.0f}%"

    lines = [
        f"{emoji} <b>{label}（{plan.plan_type}）</b>",
        f"預期報酬：<b>+{plan.expected_return_pct:.1f}%</b>　"
        f"資金部署：{deployed}",
        f"風險：{plan.risk_note}",
        "",
        f"📖 論述：{plan.thesis[:100]}{'…' if len(plan.thesis) > 100 else ''}",
        "",
    ]

    if not plan.picks:
        lines.append("📭 空手觀望（無進場標的）")
    else:
        lines.append("📋 選股：")
        for p in plan.picks:
            action_tag = {
                "open": "🟢新倉", "add": "🔵加碼", "hold": "⚪持有",
                "reduce": "🟡減碼", "close": "🔴平倉", "switch": "🔄換股",
            }.get(p.action, p.action)
            qty_str = f"{p.quantity}張" if not p.is_fractional else f"{p.shares}股"
            lines.append(
                f"  • {p.code} {p.name}｜{action_tag} {qty_str}｜"
                f"目標+{p.expected_return_pct:.1f}%　{p.reason}"
            )

    return "\n".join(lines)


def send_morning_push(briefing, plan_set, starting_capital: float = 0.0) -> None:
    """推送早晨簡報 + 三套策略到 Telegram，附 Inline Keyboard 讓使用者選擇。"""
    # 0. 把 PlanSet 存起來，等使用者選擇後執行
    save_pending_planset(plan_set)

    # 1. 早晨簡報（沒有 briefing 時跳過）
    if briefing is not None:
        _post("sendMessage", {
            "chat_id": CHAT_ID,
            "text": format_briefing_message(briefing),
            "parse_mode": "HTML",
        })

    # 2. 三套策略 card（各一則）
    for plan in (plan_set.aggressive, plan_set.balanced, plan_set.conservative):
        _post("sendMessage", {
            "chat_id": CHAT_ID,
            "text": format_plan_card(plan),
            "parse_mode": "HTML",
        })

    # 3. 選擇訊息 + Inline Keyboard
    agg = plan_set.aggressive
    bal = plan_set.balanced
    con = plan_set.conservative

    selection_text = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👆 請選擇今日執行的策略\n"
        "（點選後系統自動記錄，不執行實際下單）"
    )
    keyboard = {
        "inline_keyboard": [[
            {
                "text": f"🚀 衝刺 +{agg.expected_return_pct:.0f}%",
                "callback_data": "select_plan:aggressive",
            },
            {
                "text": f"⚖️ 均衡 +{bal.expected_return_pct:.0f}%",
                "callback_data": "select_plan:balanced",
            },
            {
                "text": f"🛡️ 保守 +{con.expected_return_pct:.0f}%",
                "callback_data": "select_plan:conservative",
            },
        ]]
    }
    _post("sendMessage", {
        "chat_id": CHAT_ID,
        "text": selection_text,
        "parse_mode": "HTML",
        "reply_markup": keyboard,
    })


def handle_select_plan(callback_query_or_chat_id, plan_type_arg: str = "") -> None:
    """處理使用者點選策略的 callback：記錄到 daily_tracker + 執行 picks。

    支援兩種呼叫方式：
        handle_select_plan(callback_query_dict)
        handle_select_plan(chat_id_str, plan_type_str)   # 測試用
    """
    if isinstance(callback_query_or_chat_id, dict):
        callback_query = callback_query_or_chat_id
        chat_id   = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
        data      = callback_query.get("data", "")
        plan_type = data.split(":", 1)[1] if ":" in data else ""
    else:
        chat_id   = str(callback_query_or_chat_id)
        plan_type = plan_type_arg

    valid_plans = {"aggressive", "balanced", "conservative"}
    if plan_type not in valid_plans:
        send_text(chat_id, f"❌ 錯誤：未知的策略類型「{plan_type}」")
        return

    label = _PLAN_LABEL.get(plan_type, plan_type)
    emoji = _PLAN_EMOJI.get(plan_type, "📋")

    # 1. 載入今日 PlanSet
    plan_set = load_pending_planset()

    # 過期檢查
    if plan_set is not None:
        created_at_str = getattr(plan_set, "created_at", None)
        if created_at_str:
            try:
                created = datetime.fromisoformat(created_at_str)
                if created.date() < datetime.now().date():
                    send_text(chat_id, "⚠️ 策略計劃已過期，請等待今日 08:30 新策略推播。")
                    return
            except Exception:
                pass

    # 2. 執行 picks（有計劃才執行）
    exec_lines: list[str] = []
    portfolio = SimulatedPortfolio.load(_PORTFOLIO_PATH)

    if plan_set is None:
        exec_lines.append("⚠️ 找不到今日策略計劃，無法執行（僅記錄選擇）")
    else:
        plan = getattr(plan_set, plan_type)
        exec_lines = execute_plan(plan, portfolio, date.today())
        portfolio.save(_PORTFOLIO_PATH)

    # 3. 記錄到 daily_tracker
    record = DailyTrackRecord(
        date=date.today(),
        starting_capital=portfolio.get_available_capital(),
        chosen_plan_type=plan_type,
        results=[
            PlanResult(plan_type=pt, pnl=None, pnl_pct=None,
                       closing_capital=None, is_actual=(pt == plan_type))
            for pt in ("aggressive", "balanced", "conservative")
        ],
    )
    save_day_record(record)

    # 4. 抓即時股價，組詳細確認訊息
    import threading as _th
    import shioaji as _sj

    stock_lines: list[str] = []
    sim_positions: list[SimPosition] = []
    price_map_local: dict[str, float] = {}   # 供 learning_db 寫入用
    total_cost = 0.0

    if plan_set is not None:
        plan = getattr(plan_set, plan_type)
        try:
            _api = _get_sj_api()
            if _api is None:
                raise RuntimeError("Shioaji 無法連線")
            for p in plan.picks:
                try:
                    contract = _api.Contracts.Stocks.get(p.code)
                    _result: list = []
                    def _fetch(_c=contract, _r=_result):
                        try: _r.extend(_api.snapshots([_c]))
                        except: pass
                    _t = _th.Thread(target=_fetch, daemon=True)
                    _t.start(); _t.join(timeout=6)
                    price = float(_result[0].close) if _result else 0.0
                except Exception:
                    price = 0.0
                price_map_local[p.code] = price
                qty_str = f"{p.shares} 股（零股）" if p.is_fractional else f"{p.quantity} 張"
                cost = p.shares * price if p.is_fractional else p.quantity * 1000 * price
                total_cost += cost
                stock_lines.append(
                    f"  • {p.code} {p.name}｜{qty_str}｜@{price:.1f} 元｜≈ {cost:,.0f} 元"
                )
                sim_positions.append(SimPosition(
                    code=p.code,
                    name=p.name,
                    entry_price=price,
                    shares=getattr(p, "shares", 0),
                    quantity=getattr(p, "quantity", 0),
                    is_fractional=getattr(p, "is_fractional", False),
                    plan_type=plan_type,
                    entry_date=date.today().isoformat(),
                ))
            # 儲存模擬持倉
            if sim_positions:
                try:
                    save_sim_positions(sim_positions)
                    log.info("sim_positions saved: %d stocks", len(sim_positions))
                except Exception as _se:
                    log.warning("save_sim_positions failed: %s", _se)

            # 盤前寫入 learning_db（預測記錄 + 籌碼快照）
            try:
                from learning_db import LearningDB, StockPredictionLog, ChipSnapshot
                from chip_data import fetch_institutional_investors, fetch_margin_trading
                ldb   = LearningDB()
                today = date.today()
                today_str = today.strftime("%Y%m%d")
                inst_data   = fetch_institutional_investors(today_str)
                margin_data = fetch_margin_trading(today_str)

                for p in plan.picks:
                    # 預測記錄
                    ldb.insert_prediction(StockPredictionLog(
                        date=today,
                        code=p.code,
                        name=p.name,
                        action=p.action,
                        confidence=p.confidence,
                        expected_return_pct=p.expected_return_pct,
                        entry_price=price_map_local.get(p.code, 0.0),
                        closing_price=None,
                        actual_return_pct=None,
                        was_correct=None,
                    ))
                    # 籌碼快照
                    inst = inst_data.get(p.code, {})
                    margin = margin_data.get(p.code, {})
                    margin_prev = margin.get("margin_prev", 0)
                    margin_change = margin.get("margin_change", 0)
                    margin_pct = (margin_change / margin_prev * 100) if margin_prev else 0.0
                    ldb.upsert_chip_snapshot(ChipSnapshot(
                        date=today,
                        code=p.code,
                        foreign_net=inst.get("foreign_net", 0.0),
                        trust_net=inst.get("investment_trust_net", 0.0),
                        margin_change_pct=margin_pct,
                    ))
                log.info("learning_db predictions inserted: %d stocks", len(plan.picks))
            except Exception as _le:
                log.warning("learning_db pre-market write failed: %s", _le)
        except Exception:
            pass

    stock_section = "\n".join(stock_lines) if stock_lines else "  （無股票明細）"
    total_str = f"{total_cost:,.0f}" if total_cost > 0 else "—"

    send_text(
        chat_id,
        f"✅ <b>{emoji} {label}策略已確認執行</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 {date.today()}　{emoji} {label}\n\n"
        f"<b>模擬買入明細：</b>\n"
        f"{stock_section}\n\n"
        f"💰 <b>合計投入：{total_str} 元</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"收盤後點下方按鈕結算損益 👇",
        reply_markup={
            "inline_keyboard": [[
                {"text": "📊 收盤結算", "callback_data": "settle_now"},
            ]]
        },
    )


def handle_settle_now(chat_id: str) -> None:
    """抓收盤價結算模擬持倉，推播結果到 Telegram。"""
    import threading as _th
    import shioaji as _sj
    from sim_position_store import load_sim_positions
    from sim_settlement import settle_positions, fetch_closing_prices
    from notifier import notify_settlement

    positions = load_sim_positions()
    if not positions:
        send_text(chat_id, "⚠️ 沒有模擬持倉紀錄，請先選擇今日策略。")
        return

    send_text(chat_id, "⏳ 抓取收盤價中，請稍候...")

    codes = [p.code for p in positions]
    price_map: dict[str, float] = {}

    # 先嘗試 Shioaji
    try:
        _api = _get_sj_api()
        if _api is not None:
            price_map = fetch_closing_prices(_api, codes)
    except Exception as e:
        log.warning("Shioaji fetch_closing_prices failed: %s", e)

    # Shioaji 失敗或資料不完整 → yfinance fallback
    missing = [c for c in codes if c not in price_map or price_map[c] == 0]
    if missing:
        for code in missing:
            try:
                p = _fetch_stock_price(code)
                if p > 0:
                    price_map[code] = p
            except Exception:
                pass

    if not price_map:
        send_text(chat_id, "❌ 無法取得收盤價（Shioaji 與 yfinance 均失敗），請稍後再試。")
        return

    settlement = settle_positions(positions, price_map)
    total_pnl     = settlement["total_pnl"]
    total_pnl_pct = settlement["total_pnl_pct"]
    per_stock     = settlement["per_stock"]
    plan_type     = positions[0].plan_type
    entry_date    = positions[0].entry_date

    # ── 寫入 learning_db ─────────────────────────────────────
    try:
        from learning_db import LearningDB, DailyStrategyLog, StockPredictionLog
        ldb = LearningDB()
        today = date.today()
        n_win  = sum(1 for s in per_stock if s["pnl"] >= 0)
        n_loss = sum(1 for s in per_stock if s["pnl"] < 0)

        # 日策略彙總
        ldb.upsert_daily_log(DailyStrategyLog(
            date=today,
            plan_type=plan_type,
            total_pnl=total_pnl,
            pnl_pct=total_pnl_pct,
            market_index_change=fetch_market_index_change(),
            n_win=n_win,
            n_loss=n_loss,
        ))

        # 個股預測結果（先插入、再更新收盤價）
        for s in per_stock:
            entry_pct = s.get("expected_return_pct", 0.0)
            was_correct = s["pnl"] >= 0
            # 先確保有預測紀錄（盤前若未插入，在此補建）
            ldb.insert_prediction(StockPredictionLog(
                date=today,
                code=s["code"],
                name=s["name"],
                action="open",
                confidence=s.get("confidence", 0),
                expected_return_pct=entry_pct,
                entry_price=s["entry_price"],
                closing_price=None,
                actual_return_pct=None,
                was_correct=None,
            ))
            ldb.update_closing_price(
                date=today,
                code=s["code"],
                closing_price=s["closing_price"],
                actual_return_pct=s.get("actual_return_pct",
                    (s["closing_price"] - s["entry_price"]) / s["entry_price"] * 100
                    if s["entry_price"] > 0 else 0.0),
                was_correct=was_correct,
            )
        log.info("learning_db updated: %d stocks", len(per_stock))
    except Exception as _le:
        log.warning("learning_db write failed: %s", _le)

    # ── 推播結算結果 ──────────────────────────────────────────
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    plan_zh   = {"aggressive": "積極版", "balanced": "均衡版", "conservative": "保守版"}.get(plan_type, plan_type)

    lines = [
        f"{pnl_emoji} <b>收盤結算結果</b>",
        f"━━━━━━━━━━━━━━━━",
        f"策略：{plan_zh}　買入日：{entry_date}",
        f"總損益：<b>{total_pnl:+,.0f} 元（{total_pnl_pct:+.2f}%）</b>",
        "",
        "<b>個股明細：</b>",
    ]
    for s in per_stock:
        icon = "🟢" if s["pnl"] >= 0 else "🔴"
        lines.append(
            f"{icon} {s['code']} {s['name']}"
            f"　{s['entry_price']:.1f}→{s['closing_price']:.1f}"
            f"　{s['pnl']:+,.0f} 元"
        )

    send_text(chat_id, "\n".join(lines))


def handle_callback(callback_query: dict) -> None:
    chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
    data    = callback_query.get("data", "")
    cq_id   = callback_query.get("id", "")

    _post("answerCallbackQuery", {"callback_query_id": cq_id})

    # ── 早晨策略選擇 ──
    if data.startswith("select_plan:"):
        handle_select_plan(callback_query)
        return

    # ── 收盤結算 ──
    if data == "settle_now":
        handle_settle_now(chat_id)
        return

    # ── 盤前選股確認（user_confirm.py 送出的 inline keyboard）──
    if data == "approve_all":
        send_text(chat_id, "✅ 全部批准，09:00 開盤將執行今日計劃。")
    elif data == "reject_all":
        reject_all_picks_from_plan(date.today(), DB_PATH)
        send_text(chat_id, "❌ 全部拒絕，今日計劃已清除，不執行任何下單。")
    elif data.startswith("approve:"):
        code = data.split(":", 1)[1]
        send_text(chat_id, f"✅ {code} 已批准，開盤時將執行此筆委託。")
    elif data.startswith("reject:"):
        code = data.split(":", 1)[1]
        reject_pick_from_plan(date.today(), code, DB_PATH)
        send_text(chat_id, f"❌ {code} 已從今日計劃中移除。")

    # ── 快速下單確認（⚡ 快速下單 按鈕送出的）──
    elif data == "order_confirm":
        send_text(chat_id, "✅ 已收到確認。\n\n⚠️ 測試版說明：下單由排程自動執行（09:00 開盤），非即時觸發。\n\n請等待排程執行後查看委託狀態。")
    elif data == "order_cancel":
        send_text(chat_id, "❌ 已取消。")
        send_main_menu(chat_id)

    # ── 撤銷所有委託確認 ──
    elif data == "cancel_all_confirm":
        send_text(chat_id, "⏳ 正在撤銷所有委託...")
        from halt import cancel_all_orders
        from monitor_agent import ensure_connected
        import os
        api = ensure_connected(
            os.getenv("SHIOAJI_API_KEY", ""),
            os.getenv("SHIOAJI_SECRET_KEY", ""),
            simulation=os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true",
        )
        if api:
            result = cancel_all_orders(api)
            n = len(result["cancelled"])
            f = len(result.get("failed", []))
            send_text(chat_id,
                f"🔄 <b>撤銷完成</b>\n"
                f"✅ 成功撤銷：{n} 筆\n"
                f"❌ 失敗：{f} 筆"
            )
        else:
            send_text(chat_id, "❌ 無法連線 Shioaji，撤銷失敗。")
    elif data == "cancel_all_abort":
        send_text(chat_id, "已取消操作。")
        send_main_menu(chat_id)

    # ── 一鍵全平倉確認 ──
    elif data == "liquidate_all_confirm":
        send_text(chat_id, "⏳ 正在執行市價全平倉...")
        from halt import liquidate_all_positions
        from monitor_agent import ensure_connected
        import os
        api = ensure_connected(
            os.getenv("SHIOAJI_API_KEY", ""),
            os.getenv("SHIOAJI_SECRET_KEY", ""),
            simulation=os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true",
        )
        if api:
            result = liquidate_all_positions(api)
            n = len(result["liquidated"])
            f = len(result.get("failed", []))
            send_text(chat_id,
                f"💥 <b>全平倉完成</b>\n"
                f"✅ 成功送出：{n} 檔\n"
                f"❌ 失敗：{f} 檔"
            )
        else:
            send_text(chat_id, "❌ 無法連線 Shioaji，全平倉失敗。")
    elif data == "liquidate_all_abort":
        send_text(chat_id, "已取消操作。")
        send_main_menu(chat_id)

    # ── 當沖買入確認 ──
    elif data.startswith("dt_buy:"):
        code = data.split(":", 1)[1]
        _handle_dt_buy(chat_id, code)
    elif data == "dt_buy_all":
        _handle_dt_buy_all(chat_id)
    elif data.startswith("dt_skip:"):
        code = data.split(":", 1)[1]
        send_text(chat_id, f"⏭️ 已跳過 {code}")
    elif data == "dt_skip_all":
        send_text(chat_id, "⏭️ 已跳過所有當沖候選股")


def _handle_dt_buy(chat_id: str, code: str) -> None:
    """當沖買入：取得即時報價 → 執行買單 → 標記持倉為 active。"""
    import os
    from daytrading_monitor import (
        load_daytrading_positions, save_daytrading_positions,
        mark_position_entered, fetch_current_price,
    )
    from daytrading_config import load_daytrading_config
    from executor import place_stock_order

    positions = load_daytrading_positions()
    pos = next((p for p in positions if p.code == code and p.status == "watching"), None)

    if pos is None:
        send_text(chat_id, f"⚠️ {code} 無可用的當沖候選持倉（已成交或不存在）。")
        return

    api = _get_sj_api()
    if api is None:
        send_text(chat_id, f"❌ 無法連線 Shioaji，{code} 買入失敗。")
        return

    dt_config = load_daytrading_config()
    send_text(chat_id, f"⏳ 正在為 {code} {pos.name} 下買單（預算 {dt_config.budget_per_stock:,.0f} 元）...")

    price = fetch_current_price(code, api=api)
    if price is None:
        send_text(chat_id, f"❌ 無法取得 {code} 即時報價，買入取消。")
        return

    result = place_stock_order(
        api=api,
        code=code, name=pos.name,
        action="buy",
        budget=dt_config.budget_per_stock,
        price=price,
        hard_limit=float(os.getenv("ORDER_HARD_LIMIT", "150000")),
    )

    if result.success:
        mark_position_entered(pos, result.price, result.quantity, result.lot_type)
        save_daytrading_positions(positions)
        send_text(
            chat_id,
            f"✅ <b>當沖買入成功</b>\n"
            f"{code} {pos.name}\n"
            f"買入價 {result.price:,.2f}　"
            f"數量 {result.quantity}{'張' if result.lot_type == 'common' else '股'}\n"
            f"金額 {result.amount:,.0f} 元　委託 ID：{result.order_id}",
        )
        log.info("DT buy: %s qty=%d price=%.2f id=%s", code, result.quantity, result.price, result.order_id)
    else:
        send_text(chat_id, f"❌ {code} 買入失敗：{result.reason}")
        log.warning("DT buy failed %s: %s", code, result.reason)


def _handle_dt_buy_all(chat_id: str) -> None:
    """全部買入：對所有 watching 當沖候選股依序執行買單。"""
    from daytrading_monitor import load_daytrading_positions
    watching = [p for p in load_daytrading_positions() if p.status == "watching"]
    if not watching:
        send_text(chat_id, "⚠️ 無可用的當沖候選股（已全部成交或清空）。")
        return
    send_text(chat_id, f"⏳ 開始依序買入 {len(watching)} 支候選股...")
    for pos in watching:
        _handle_dt_buy(chat_id, pos.code)


# ── Message router ─────────────────────────────────────────────────────────────

# Reverse name→code lookup built once at import time (avoids per-message rebuild).
import config as _tb_cfg
_REV_STOCK_NAMES: dict[str, str] = {v: k for k, v in _tb_cfg.STOCK_NAMES.items()}

# Map button text → handler function name (string).
# Using names instead of direct references so unittest.mock.patch works correctly.
_HANDLER_NAMES: dict[str, str] = {
    "📊 今日狀態":      "handle_status",
    "💼 持倉":          "handle_holdings",
    "📈 選股計劃":      "handle_plan",
    "⚡ 快速下單":      "handle_quick_order",
    "🎯 今日當沖預測":  "handle_daytrading",
    "🛡️ 停損設定":     "handle_stop_loss",
    "❓ 說明":          "handle_help",
    "🚨 緊急暫停":      "handle_emergency_halt",
    "🔄 撤銷所有委託":  "handle_cancel_all",
    "💥 一鍵全平倉":    "handle_liquidate_all",
}


def _is_authorized(update: dict) -> bool:
    """H9: 雙重驗證 — chat_id + user_id（若 TELEGRAM_USER_ID 已設定）。"""
    msg     = update.get("message") or update.get("callback_query", {}).get("message", {})
    chat_id = str((msg or {}).get("chat", {}).get("id", ""))
    if chat_id != CHAT_ID:
        return False

    # 額外驗證發送者 user_id（防止同群組內其他成員操控 Bot）
    if USER_ID:
        sender = (
            update.get("message", {}).get("from", {})
            or update.get("callback_query", {}).get("from", {})
        )
        if str(sender.get("id", "")) != USER_ID:
            return False

    return True


def process_update(update: dict) -> None:
    if not _is_authorized(update):
        return

    if "callback_query" in update:
        handle_callback(update["callback_query"])
        return

    msg     = update.get("message", {})
    text    = msg.get("text", "").strip()
    chat_id = str(msg.get("chat", {}).get("id", ""))

    if text in ("/start", "/menu"):
        send_main_menu(chat_id, "📱 AI Stock 已啟動，請選擇功能：")
        return

    if text == "恢復系統":
        from halt import resume, is_halted
        if is_halted():
            resume()
            send_text(chat_id, "✅ 系統已恢復，排程重新生效。")
        else:
            send_text(chat_id, "ℹ️ 系統目前正常運作中，無需恢復。")
        return

    if text.startswith("停損 "):
        _handle_set_stop_loss(chat_id, text)
        return

    if text in ("今日當沖", "/當沖"):
        handle_daytrading(chat_id)
        return

    # ── 查股路由：代號或名稱，支援以下格式 ───────────────────────────────────
    #   2330            → 純數字代號
    #   台積電          → 股票名稱（完全比對 config.STOCK_NAMES）
    #   查 2330         → 明確查股指令（代號）
    #   查 台積電       → 明確查股指令（名稱）
    #   /查股 2330      → slash 指令（代號）
    #   /查股 台積電    → slash 指令（名稱）
    import re as _re

    _query_input: str | None = None

    if _re.match(r"^\d{4,6}$", text):
        # 純數字代號，直接使用
        _query_input = text
    else:
        # 明確查股前綴：/查股 或 查
        _m = _re.match(r"^(?:/查股|查)\s+(.+)$", text)
        if _m:
            _query_input = _m.group(1).strip()
        else:
            # 無前綴：只有名稱完全比對 config.STOCK_NAMES 才觸發，避免攔截其他訊息
            if text in _REV_STOCK_NAMES:
                _query_input = text

    if _query_input is not None:
        send_text(chat_id, "⏳ 查詢中…")
        from stock_query import resolve_stock_input as _resolve, query_stock as _query_stock
        _api = _get_sj_api()
        _code, _err = _resolve(_query_input, api=_api)
        if _err:
            send_text(chat_id, _err)
        else:
            result = _query_stock(_code, api=_api)
            send_text(chat_id, result)
        return

    import telegram_bot as _self
    handler_name = _HANDLER_NAMES.get(text)
    if handler_name:
        getattr(_self, handler_name)(chat_id)
    else:
        send_main_menu(chat_id, f"不認識「{text}」，請使用下方選單：")


# ── Long polling loop ──────────────────────────────────────────────────────────

def run() -> None:
    os.makedirs("data", exist_ok=True)
    init_db(DB_PATH)
    log.info("Telegram bot started (long polling)")

    offset = 0
    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"timeout": _POLL_TIMEOUT, "offset": offset},
                timeout=_POLL_TIMEOUT + 5,
            )
            data = resp.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                process_update(update)
        except requests.exceptions.ReadTimeout:
            pass
        except Exception as e:
            log.error("Polling error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run()
