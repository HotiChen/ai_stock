#!/usr/bin/env bash
# start_all.sh — 一鍵啟動所有服務
# crontab: 25 8 * * 1-5  cd /path/to/ai_stock && bash start_all.sh >> logs/start_all.log 2>&1

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p logs

# 載入 .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | grep '=' | sed 's/[[:space:]]*#.*//' | xargs)
fi

echo "[$(date '+%H:%M:%S')] ── 啟動 QUANT·AI 系統 ──"

# ── 1. 主系統排程（main.py）──────────────────────────────
if pgrep -f "python3 main.py" > /dev/null 2>&1; then
  echo "[$(date '+%H:%M:%S')] main.py 已在執行，略過"
else
  nohup python3 main.py >> logs/main.log 2>&1 &
  echo "[$(date '+%H:%M:%S')] ✅ main.py 已啟動 (PID: $!)"
fi

# ── 2. Telegram Bot ──────────────────────────────────────
if pgrep -f "python3 telegram_bot.py" > /dev/null 2>&1; then
  echo "[$(date '+%H:%M:%S')] telegram_bot.py 已在執行，略過"
else
  nohup python3 telegram_bot.py >> logs/telegram.log 2>&1 &
  echo "[$(date '+%H:%M:%S')] ✅ telegram_bot.py 已啟動 (PID: $!)"
fi

# ── 3. FastAPI 後端 ──────────────────────────────────────
if lsof -ti :1234 > /dev/null 2>&1; then
  echo "[$(date '+%H:%M:%S')] 後端 :1234 已在執行，略過"
else
  nohup bash -c "cd '$SCRIPT_DIR/backend' && source '$SCRIPT_DIR/venv/bin/activate' && uvicorn app.main:app --host 0.0.0.0 --port 1234" >> logs/backend.log 2>&1 &
  echo "[$(date '+%H:%M:%S')] ✅ 後端 :1234 已啟動 (PID: $!)"
fi

# ── 4. React 前端 ────────────────────────────────────────
if lsof -ti :9439 > /dev/null 2>&1; then
  echo "[$(date '+%H:%M:%S')] 前端 :9439 已在執行，略過"
else
  nohup bash -c "cd '$SCRIPT_DIR/frontend' && npm run dev -- --host 0.0.0.0" >> logs/frontend.log 2>&1 &
  echo "[$(date '+%H:%M:%S')] ✅ 前端 :9439 已啟動 (PID: $!)"
fi

# ── 5. 等前端啟動後開瀏覽器 ─────────────────────────────
sleep 5
echo "[$(date '+%H:%M:%S')] 開啟瀏覽器..."
open "http://localhost:9439" 2>/dev/null || true

echo "[$(date '+%H:%M:%S')] ── 全部啟動完成 ──"
