from __future__ import annotations

"""
Telegram confirmation module for stock picks.
Sends inline keyboard to authorized user; parses callback responses.
"""

import json
import os
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
AUTHORIZED_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ── send_dt_buy_confirmation ──────────────────────────────────────────────────

def send_dt_buy_confirmation(
    positions: list,   # list[DaytradingPosition]
    chat_id: str,
    budget: float,
) -> Optional[int]:
    """發送當沖買入確認的 Telegram inline keyboard。

    每支候選股一行：[✅ CODE NAME] [❌ 跳過]
    最後一行：      [✅ 全部買入] [❌ 全部跳過]

    callback_data 格式：
      dt_buy:CODE   dt_skip:CODE   dt_buy_all   dt_skip_all
    """
    if not positions:
        return None

    header = (
        f"💰 <b>當沖買入確認</b>\n"
        f"預算 <b>{budget:,.0f}</b> 元／支\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"請選擇要買入的股票："
    )

    rows = []
    for p in positions:
        rows.append([
            {"text": f"✅ {p.code} {p.name}", "callback_data": f"dt_buy:{p.code}"},
            {"text": "❌ 跳過",                 "callback_data": f"dt_skip:{p.code}"},
        ])
    rows.append([
        {"text": "✅ 全部買入", "callback_data": "dt_buy_all"},
        {"text": "❌ 全部跳過", "callback_data": "dt_skip_all"},
    ])

    payload = {
        "chat_id":      chat_id,
        "text":         header,
        "parse_mode":   "HTML",
        "reply_markup": json.dumps({"inline_keyboard": rows}),
    }
    try:
        resp = requests.post(f"{_API_BASE}/sendMessage", json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        return None
    except Exception:
        return None


# ── verify_user ───────────────────────────────────────────────────────────────

def verify_user(user_id) -> bool:
    """Return True only if user_id matches the authorized chat ID."""
    if user_id is None:
        return False
    return str(user_id) == str(AUTHORIZED_CHAT_ID)


# ── format_picks_message ──────────────────────────────────────────────────────

def format_picks_message(picks: list[dict]) -> str:
    if not picks:
        return "今日無選股建議。"
    lines = ["📊 今日選股建議，請確認：\n"]
    for i, p in enumerate(picks, 1):
        budget = p.get("budget", 0)
        lines.append(
            f"{i}. {p['code']} {p['name']}  "
            f"信心值:{p.get('confidence', '?')}/10  "
            f"預算:{budget:,.0f}元  "
            f"[{p.get('sector', '')}]"
        )
    return "\n".join(lines)


# ── send_confirmation ─────────────────────────────────────────────────────────

def send_confirmation(picks: list[dict], chat_id: str) -> Optional[int]:
    """
    Send a Telegram message with inline keyboard buttons to approve/reject picks.
    Returns the message_id on success, None on failure.
    """
    text = format_picks_message(picks)

    # Build per-pick buttons + bulk approve/reject
    keyboard_rows = []
    for p in picks:
        code = p["code"]
        name = p["name"]
        keyboard_rows.append([
            {"text": f"✅ {code} {name}", "callback_data": f"approve:{code}"},
            {"text": f"❌ 拒絕",          "callback_data": f"reject:{code}"},
        ])
    keyboard_rows.append([
        {"text": "✅ 全部批准", "callback_data": "approve_all"},
        {"text": "❌ 全部拒絕", "callback_data": "reject_all"},
    ])

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps({"inline_keyboard": keyboard_rows}),
    }

    try:
        resp = requests.post(f"{_API_BASE}/sendMessage", json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        return None
    except Exception:
        return None


# ── handle_callback ───────────────────────────────────────────────────────────

def handle_callback(callback_query: dict) -> Optional[dict]:
    """
    Parse a Telegram callback_query dict.
    Returns action dict or None if unauthorized / unknown.

    Action dict shapes:
      {"action": "approve_all"}
      {"action": "reject_all"}
      {"action": "approve", "code": "2330"}
      {"action": "reject",  "code": "2330"}
    """
    user_id = callback_query.get("from", {}).get("id")
    if not verify_user(user_id):
        return None

    data: str = callback_query.get("data", "")

    if data == "approve_all":
        return {"action": "approve_all"}
    if data == "reject_all":
        return {"action": "reject_all"}
    if data.startswith("approve:"):
        return {"action": "approve", "code": data.split(":", 1)[1]}
    if data.startswith("reject:"):
        return {"action": "reject", "code": data.split(":", 1)[1]}

    return None
