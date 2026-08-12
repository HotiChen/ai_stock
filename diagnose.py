"""
diagnose.py — 快速診斷當沖推播流程是否正常
執行：python3 diagnose.py
"""
from __future__ import annotations
import os, sys
from dotenv import load_dotenv
load_dotenv()

OK   = "✅"
FAIL = "❌"
WARN = "⚠️ "

def check(label: str, fn):
    try:
        result = fn()
        print(f"{OK}  {label}: {result or 'OK'}")
        return True
    except Exception as e:
        print(f"{FAIL}  {label}: {e}")
        return False

print("=" * 50)
print("當沖診斷工具")
print("=" * 50)

# 1. 環境變數
telegram_id = os.getenv("TELEGRAM_CHAT_ID", "")
print(f"\n{'─'*30}")
print("【環境變數】")
print(f"{'OK  ' if telegram_id else FAIL+'  '}TELEGRAM_CHAT_ID: {'已設定 (' + telegram_id[:4] + '...)' if telegram_id else '⚠️  未設定 — Telegram 無法推播！'}")
print(f"{'OK  ' if os.getenv('SHIOAJI_API_KEY') else WARN}SHIOAJI_API_KEY: {'已設定' if os.getenv('SHIOAJI_API_KEY') else '未設定（simulation mode）'}")
print(f"{'OK  ' if os.getenv('ANTHROPIC_API_KEY') else FAIL+'  '}ANTHROPIC_API_KEY: {'已設定' if os.getenv('ANTHROPIC_API_KEY') else '未設定 — AI 分析無法運作！'}")

# 2. DaytradingConfig 載入
print(f"\n{'─'*30}")
print("【DaytradingConfig】")
check("載入設定", lambda: __import__('daytrading_config').load_daytrading_config())

# 3. Telegram 推播測試
print(f"\n{'─'*30}")
print("【Telegram 推播】")
if telegram_id:
    def _send_test():
        from telegram_bot import send_text
        send_text(telegram_id, "🔧 <b>診斷測試</b>\nTelegram 推播正常！")
        return "訊息已送出，請確認 Telegram 是否收到"
    check("送出測試訊息", _send_test)
else:
    print(f"{FAIL}  TELEGRAM_CHAT_ID 未設定，跳過推播測試")

# 4. 大盤資料
print(f"\n{'─'*30}")
print("【大盤 / 台指期資料】")
def _market():
    from daytrading_report import _fetch_market
    m = _fetch_market()
    _i = m.get("index_change_pct")
    _f = m.get("futures_premium_pct")
    _is = "資料無法取得" if _i is None else f"{_i:+.2f}%"
    _fs = "N/A" if _f is None else f"{_f:+.2f}%"
    return f"指數 {_is}  期溢貼水 {_fs}"
check("fetch_market", _market)

# 5. DT positions 狀態
print(f"\n{'─'*30}")
print("【當沖持倉狀態】")
def _positions():
    from daytrading_monitor import load_daytrading_positions
    positions = load_daytrading_positions()
    if not positions:
        return "data/daytrading_positions.json 為空（8:30 尚未跑過或昨天已清除）"
    summary = ", ".join(f"{p.code}({p.status})" for p in positions)
    return summary
check("load_daytrading_positions", _positions)

# 6. build_daytrading_report 能否執行
print(f"\n{'─'*30}")
print("【DT 報告產生（不含 AI 深度分析）】")
def _report_header():
    from daytrading_report import _fetch_market, _get_stock_universe
    picks = _get_stock_universe(api=None, top_n=5)
    return f"取得 {len(picks)} 支候選股"
check("_get_stock_universe", _report_header)

print(f"\n{'='*50}")
print("診斷完成。若 Telegram 測試訊息已送到，代表推播正常。")
print("若 main.py 沒有在跑，請確認：")
print("  1. git pull 拉到最新程式碼")
print("  2. python3 main.py 在背景執行中")
print("  3. .env 已正確設定 TELEGRAM_CHAT_ID")
print("=" * 50)
