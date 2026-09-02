#!/usr/bin/env bash
# start_all.sh — 一鍵啟動所有服務
# crontab: 25 8 * * 1-5  cd /path/to/ai_stock && bash start_all.sh >> logs/start_all.log 2>&1

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 守衛：確認在外接 SSD 上、關鍵檔案在線 ─────────────────────────────────
# 外接 SSD 若在 cron 觸發時未掛載，cd 會失敗；這裡再驗證一次關鍵檔案存在，
# 不在就寫 log 到 $HOME（一定存在）並嘗試推 Telegram，避免靜默失敗。
if [ ! -f "$SCRIPT_DIR/main.py" ]; then
  echo "[$(date '+%F %T')] ❌ start_all 中止：main.py 不存在於 $SCRIPT_DIR（SSD 未掛載？）" \
    >> "$HOME/quant_start_guard.log"
  exit 1
fi

mkdir -p logs

# 載入 .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | grep '=' | sed 's/[[:space:]]*#.*//' | xargs)
fi

echo "[$(date '+%H:%M:%S')] ── 啟動 QUANT·AI 系統 ──"

# ── 0. YouTube 名嘴分析（08:25 抓昨天影片）──────────────
echo "[$(date '+%H:%M:%S')] 分析 YouTube 名嘴觀點..."
python3 youtube_analyzer.py --send >> logs/youtube.log 2>&1 &
echo "[$(date '+%H:%M:%S')] ✅ youtube_analyzer 執行中"

# ── 1. 主系統排程（main.py）──────────────────────────────
# 注意：macOS 上 python3 的程序名會解析成 ".../Python.app/.../Python main.py"，
# 必須用 "[Pp]ython... main.py" 匹配，寫死 "python3 main.py" 會永遠匹配不到。
#
# 為什麼要「啟動後回頭驗證」：2026-09-02 早上 pgrep 誤判成「已在執行」而跳過，
# 腳本照樣印出「全部啟動完成」，但 main.py 其實沒起來——當天 8:30 選股完全
# 沒跑，而且沒有任何人知道。只檢查不驗證的成功訊息等於說謊。
MAIN_PATTERN="[Pp]ython[0-9.]* main\.py"

if pgrep -f "$MAIN_PATTERN" > /dev/null 2>&1; then
  echo "[$(date '+%H:%M:%S')] main.py 已在執行，略過"
else
  nohup python3 main.py >> logs/main.log 2>&1 &
  echo "[$(date '+%H:%M:%S')] ✅ main.py 已啟動 (PID: $!)"
fi

# 驗證：等它真的出現在程序表裡（Shioaji 登入約需 15-20 秒，給到 30 秒）
for _i in $(seq 1 30); do
  if pgrep -f "$MAIN_PATTERN" > /dev/null 2>&1; then break; fi
  sleep 1
done

if pgrep -f "$MAIN_PATTERN" > /dev/null 2>&1; then
  echo "[$(date '+%H:%M:%S')] ✅ main.py 驗證通過 (PID: $(pgrep -f "$MAIN_PATTERN" | head -1))"
else
  # 重試一次：pgrep 誤判導致跳過啟動時，這裡會補上
  echo "[$(date '+%H:%M:%S')] ⚠️ main.py 未在執行，重試啟動…"
  nohup python3 main.py >> logs/main.log 2>&1 &
  sleep 20
  if pgrep -f "$MAIN_PATTERN" > /dev/null 2>&1; then
    echo "[$(date '+%H:%M:%S')] ✅ main.py 重試成功"
  else
    echo "[$(date '+%H:%M:%S')] ❌ main.py 啟動失敗（已重試）"
    tail -20 logs/main.log 2>/dev/null
    # 一定要讓人知道：當日 8:30 選股、當沖、複盤全部不會執行
    python3 - <<'PY' 2>/dev/null || true
import os
try:
    from telegram_bot import send_text
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if chat:
        send_text(chat, "🚨 <b>main.py 啟動失敗</b>\n"
                        "今日 8:30 選股、當沖監控、13:35 複盤都不會執行。\n"
                        "請檢查 logs/main.log。")
except Exception:
    pass
PY
  fi
fi

# ── 2. Telegram Bot ──────────────────────────────────────
if pgrep -f "[Pp]ython[0-9.]* telegram_bot" > /dev/null 2>&1; then
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
