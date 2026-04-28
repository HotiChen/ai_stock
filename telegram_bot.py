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

import json
import os
import time
from datetime import date, datetime
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

from logger import get_logger
from research_db import init_db, load_daily_plan, load_daily_trades

log = get_logger(__name__)

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH     = os.getenv("DB_PATH", "data/research.db")
_API        = f"https://api.telegram.org/bot{BOT_TOKEN}"
_POLL_TIMEOUT = 30  # long polling seconds


# ── Telegram API helpers ───────────────────────────────────────────────────────

def _post(method: str, payload: dict) -> dict:
    try:
        resp = requests.post(f"{_API}/{method}", json=payload, timeout=15)
        return resp.json()
    except Exception as e:
        log.error("Telegram %s failed: %s", method, e)
        return {}


def send_text(chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    _post("sendMessage", payload)


def send_main_menu(chat_id: str, text: str = "請選擇功能：") -> None:
    keyboard = {
        "keyboard": [
            ["📊 今日狀態", "💼 持倉"],
            ["📈 選股計劃", "⚡ 快速下單"],
            ["🛡️ 停損設定", "❓ 說明"],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False,
    }
    send_text(chat_id, text, reply_markup=keyboard)


# ── Handlers ───────────────────────────────────────────────────────────────────

def handle_status(chat_id: str) -> None:
    now = datetime.now()
    trades = load_daily_trades(date.today(), DB_PATH)

    total_amount = sum(t.get("amount", 0) or 0 for t in trades if t.get("action") == "buy")
    total_pnl    = sum(t.get("pnl", 0) or 0 for t in trades)

    lines = [
        f"📊 <b>今日狀態</b>",
        f"━━━━━━━━━━━━━━━━",
        f"🗓 {now.strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"💸 今日委託金額：<b>{total_amount:,.0f}</b> 元",
        f"📈 今日損益：<b>{total_pnl:+,.0f}</b> 元",
        f"📋 今日委託筆數：{len(trades)} 筆",
    ]
    send_text(chat_id, "\n".join(lines))


def handle_holdings(chat_id: str) -> None:
    trades = load_daily_trades(date.today(), DB_PATH)
    buys = [t for t in trades if t.get("action") == "buy"]

    if not buys:
        send_text(chat_id, "💼 今日尚無持倉記錄。")
        return

    lines = ["💼 <b>今日持倉</b>", "━━━━━━━━━━━━━━━━"]
    for t in buys:
        pnl = t.get("pnl") or 0
        lines.append(
            f"• {t.get('code')} {t.get('name', '')}｜"
            f"{t.get('quantity')} 股｜"
            f"成本 {t.get('price', 0):.2f}｜"
            f"損益 {pnl:+,.0f}"
        )
    send_text(chat_id, "\n".join(lines))


def handle_plan(chat_id: str) -> None:
    picks = load_daily_plan(date.today(), DB_PATH)

    if not picks:
        send_text(chat_id, "📈 今日尚無選股計劃（盤前分析於 08:30 執行）。")
        return

    lines = ["📈 <b>今日選股計劃</b>", "━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(picks, 1):
        lines.append(
            f"{i}. <b>{p['code']}</b> {p.get('name', '')}｜"
            f"信心 {p.get('confidence', '?')}/10｜"
            f"預算 {p.get('budget', 0):,.0f} 元\n"
            f"   目標價 {p.get('target_price', '-')}｜"
            f"停損 {p.get('stop_loss_price', '-')}｜"
            f"[{p.get('sector', '')}]"
        )
    send_text(chat_id, "\n".join(lines))


def handle_quick_order(chat_id: str) -> None:
    picks = load_daily_plan(date.today(), DB_PATH)

    if not picks:
        send_text(chat_id, "⚡ 今日無待下單計劃。\n\n請等待 08:30 盤前分析完成。")
        return

    text = "⚡ <b>快速下單</b>\n━━━━━━━━━━━━━━━━\n請確認是否執行今日計劃：\n\n"
    for p in picks:
        text += f"• {p['code']} {p.get('name', '')}｜預算 {p.get('budget', 0):,.0f} 元\n"

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ 確認下單", "callback_data": "order_confirm"},
            {"text": "❌ 取消",     "callback_data": "order_cancel"},
        ]]
    }
    send_text(chat_id, text, reply_markup=keyboard)


def handle_stop_loss(chat_id: str) -> None:
    send_text(
        chat_id,
        "🛡️ <b>停損設定</b>\n━━━━━━━━━━━━━━━━\n"
        "請輸入停損指令，格式：\n\n"
        "<code>停損 2330 540</code>\n\n"
        "（代號 + 停損價格）"
    )


def handle_help(chat_id: str) -> None:
    send_text(
        chat_id,
        "❓ <b>操作說明</b>\n━━━━━━━━━━━━━━━━\n"
        "📊 今日狀態　→ 帳戶總覽、損益\n"
        "💼 持倉　　　→ 今日持倉明細\n"
        "📈 選股計劃　→ AI 今日選股清單\n"
        "⚡ 快速下單　→ 執行今日計劃\n"
        "🛡️ 停損設定　→ 設定個股停損價\n\n"
        "排程：\n"
        "  08:30 盤前 AI 分析\n"
        "  09:00 開盤下單\n"
        "  13:35 收盤總結"
    )


def handle_callback(callback_query: dict) -> None:
    chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
    data    = callback_query.get("data", "")
    cq_id   = callback_query.get("id", "")

    _post("answerCallbackQuery", {"callback_query_id": cq_id})

    if data == "order_confirm":
        send_text(chat_id, "✅ 下單指令已送出，請至 Dashboard 確認委託狀態。")
    elif data == "order_cancel":
        send_text(chat_id, "❌ 已取消。")
        send_main_menu(chat_id)


# ── Message router ─────────────────────────────────────────────────────────────

_HANDLERS = {
    "📊 今日狀態":  handle_status,
    "💼 持倉":      handle_holdings,
    "📈 選股計劃":  handle_plan,
    "⚡ 快速下單":  handle_quick_order,
    "🛡️ 停損設定": handle_stop_loss,
    "❓ 說明":      handle_help,
}


def _is_authorized(update: dict) -> bool:
    msg     = update.get("message") or update.get("callback_query", {}).get("message", {})
    chat_id = str((msg or {}).get("chat", {}).get("id", ""))
    return chat_id == CHAT_ID


def process_update(update: dict) -> None:
    if not _is_authorized(update):
        return

    if "callback_query" in update:
        handle_callback(update["callback_query"])
        return

    msg  = update.get("message", {})
    text = msg.get("text", "").strip()
    chat_id = str(msg.get("chat", {}).get("id", ""))

    if text in ("/start", "/menu"):
        send_main_menu(chat_id, "📱 AI Stock 已啟動，請選擇功能：")
        return

    handler = _HANDLERS.get(text)
    if handler:
        handler(chat_id)
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
                f"{_API}/getUpdates",
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
