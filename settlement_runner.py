#!/usr/bin/env python3
"""
settlement_runner.py — 每日 13:35 自動執行收盤結算腳本（H10）

功能：
  台股收盤為 13:30，此腳本於 13:35 自動執行，抓取收盤價並結算模擬持倉，
  推播損益報告到 Telegram，取代原本需要手動按「收盤結算」按鈕的流程。

排程方式（macOS launchd）：
  ~/Library/LaunchAgents/com.aistock.settlement.plist
  週一至週五 13:35 自動執行
  Log: logs/ai_stock.log
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

_PROJECT_DIR = Path(__file__).parent.resolve()
os.chdir(_PROJECT_DIR)
sys.path.insert(0, str(_PROJECT_DIR))

from logger import get_logger

log = get_logger("settlement_runner")


def _is_trading_day() -> bool:
    """週一至週五才執行（不含節假日，未做完整節假日判斷）。"""
    return datetime.now().weekday() < 5


def run() -> None:
    log.info("=== settlement_runner 開始 %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))

    if not _is_trading_day():
        log.info("今日非交易日，略過。")
        return

    try:
        from telegram_bot import handle_settle_now, CHAT_ID
        if not CHAT_ID:
            log.error("TELEGRAM_CHAT_ID 未設定，無法推播結算結果。")
            return

        log.info("開始執行收盤結算...")
        handle_settle_now(CHAT_ID)
        log.info("=== settlement_runner 完成 ===")

    except Exception as e:
        log.error("settlement_runner 執行失敗：%s", e, exc_info=True)
        try:
            from notifier import _send
            _send(f"⚠️ <b>13:35 自動結算失敗</b>\n錯誤：{e}")
        except Exception:
            pass


if __name__ == "__main__":
    run()
