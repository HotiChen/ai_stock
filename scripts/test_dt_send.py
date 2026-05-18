"""scripts/test_dt_send.py — 本機測試：送當沖預測 + 買入確認到 Telegram

用法：
    python scripts/test_dt_send.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
if not CHAT_ID:
    print("❌ TELEGRAM_CHAT_ID 未設定，請在 .env 加入")
    sys.exit(1)

from daytrading_monitor import load_daytrading_positions, DaytradingPosition, save_daytrading_positions
from user_confirm import send_dt_buy_confirmation
from telegram_bot import send_text

# ── 確保有測試資料 ────────────────────────────────────────────
positions = [p for p in load_daytrading_positions() if p.status == "watching"]
if not positions:
    print("⚠️  daytrading_positions.json 無 watching 持倉，建立測試資料...")
    positions = [
        DaytradingPosition(
            code="2603", name="長榮",
            entry_low=185.0, entry_high=192.0,
            target_price=202.0, stop_loss=180.0,
            dt_score=8, status="watching",
        ),
        DaytradingPosition(
            code="2382", name="廣達",
            entry_low=250.0, entry_high=258.0,
            target_price=270.0, stop_loss=244.0,
            dt_score=7, status="watching",
        ),
        DaytradingPosition(
            code="2330", name="台積電",
            entry_low=1040.0, entry_high=1060.0,
            target_price=1100.0, stop_loss=1015.0,
            dt_score=9, status="watching",
        ),
    ]
    save_daytrading_positions(positions)
    print(f"   已寫入 {len(positions)} 支")

# ── 送出報告 ──────────────────────────────────────────────────
budget = float(os.getenv("DT_BUDGET", "30000"))

report_lines = [
    "⚡ <b>今日當沖預測</b>（測試）",
    "━━━━━━━━━━━━━━━━",
]
for i, p in enumerate(positions, 1):
    bar = "█" * p.dt_score + "░" * (10 - p.dt_score)
    report_lines.append(
        f"<b>{i}. {p.code} {p.name}</b>  [{bar}] {p.dt_score}/10\n"
        f"   進場：{p.entry_low:.0f}–{p.entry_high:.0f}  "
        f"目標：{p.target_price:.0f}  停損：{p.stop_loss:.0f}"
    )
report_lines.append("\n⚠️ 當沖有風險，請控制部位")

report_text = "\n".join(report_lines)
send_text(CHAT_ID, report_text)
print("✅ 已送出當沖報告")

# ── 送出買入確認 keyboard ──────────────────────────────────────
msg_id = send_dt_buy_confirmation(positions, CHAT_ID, budget=budget)
if msg_id:
    print(f"✅ 已送出買入確認 keyboard（message_id={msg_id}）")
else:
    print("❌ 買入確認傳送失敗，請確認 TELEGRAM_BOT_TOKEN")
