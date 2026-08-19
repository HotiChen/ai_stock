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
if pgrep -f "[Pp]ython[0-9.]* main\.py" > /dev/null 2>&1; then
  echo "[$(date '+%H:%M:%S')] main.py 已在執行，略過"
else
  nohup python3 main.py >> logs/main.log 2>&1 &
  echo "[$(date '+%H:%M:%S')] ✅ main.py 已啟動 (PID: $!)"
fi

# ── 1b. 防閒置睡眠 ───────────────────────────────────────
# 2026-08-18：main.py 正常啟動，但 Mac 在 08:35 進入閒置睡眠，整個 process
# 被凍結到 11:44。當沖預測、開盤確認、盤中監控全部沒跑，策略推播遲到三小時。
# 五個服務都在背景跑、沒人碰鍵盤，macOS 不知道這台機器正在做事——要明講。
# -i 只擋系統閒置睡眠（螢幕照常關）；-w 綁 main.py 的 PID，main.py 一停
# caffeinate 自己就結束，不會留下讓 Mac 永遠不睡的孤兒。
if pgrep -x caffeinate > /dev/null 2>&1; then
  echo "[$(date '+%H:%M:%S')] 防睡眠已在執行，略過"
else
  # 腳本開頭是 set -euo pipefail：pgrep 查無程序時回 1，加上 pipefail 會讓
  # 這行賦值的結束碼變成 1，整個 start_all 就此中止，後端與前端都不會啟動。
  MAIN_PID="$(pgrep -f "[Pp]ython[0-9.]* main\.py" | head -1 || true)"
  if [ -n "$MAIN_PID" ]; then
    nohup caffeinate -i -w "$MAIN_PID" >/dev/null 2>&1 &
    echo "[$(date '+%H:%M:%S')] ✅ 防閒置睡眠已啟用（綁 main.py PID $MAIN_PID）"
  else
    # 拿不到 main.py 的 PID 就退成時限模式，撐到 14:30 收工之後。
    nohup caffeinate -i -t 21600 >/dev/null 2>&1 &
    echo "[$(date '+%H:%M:%S')] ⚠️ 取不到 main.py PID，防睡眠改用 6 小時時限"
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
  # 用系統 python3 而不是 venv：venv/bin/python 這個執行檔位於外接 SSD 上，
  # macOS TCC 是按執行檔路徑授權的，SSD 上的 python 沒有「完全磁碟取用權限」，
  # 連自己的 venv/pyvenv.cfg 都讀不到（PermissionError: Operation not permitted）。
  # /usr/local/bin/python3 在內接碟且已授權，main.py 本來就是用它跑的。
  nohup bash -c "cd '$SCRIPT_DIR/backend' && python3 -m uvicorn app.main:app --host 0.0.0.0 --port 1234" >> logs/backend.log 2>&1 &
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
