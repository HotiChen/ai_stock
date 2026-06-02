#!/usr/bin/env bash
# stop_all.sh — 收盤後關閉所有服務
# crontab: 0 14 * * 1-5  cd /path/to/ai_stock && bash stop_all.sh >> logs/stop_all.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[$(date '+%H:%M:%S')] ── 關閉 QUANT·AI 系統 ──"

# 關閉 main.py
pkill -f "python3 main.py"       2>/dev/null && echo "[$(date '+%H:%M:%S')] ✅ main.py 已停止"       || echo "[$(date '+%H:%M:%S')] main.py 未在執行"

# 關閉 telegram_bot.py
pkill -f "python3 telegram_bot"  2>/dev/null && echo "[$(date '+%H:%M:%S')] ✅ telegram_bot 已停止"   || echo "[$(date '+%H:%M:%S')] telegram_bot 未在執行"

# 關閉後端
kill $(lsof -ti :1234) 2>/dev/null && echo "[$(date '+%H:%M:%S')] ✅ 後端 :1234 已停止" || echo "[$(date '+%H:%M:%S')] 後端未在執行"

# 關閉前端
kill $(lsof -ti :9439) 2>/dev/null && echo "[$(date '+%H:%M:%S')] ✅ 前端 :9439 已停止" || echo "[$(date '+%H:%M:%S')] 前端未在執行"

echo "[$(date '+%H:%M:%S')] ── 全部關閉完成 ──"
