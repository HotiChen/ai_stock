#!/usr/bin/env bash
# post_market.sh — 每日 13:35 收盤後自動執行（排在 main.py 的 crontab 之後）
#
# 執行順序：
#   1. adaptive_scorer    — 更新學習配置（勝率區間 + 最佳閾值）
#   2. dt_paper_trade     — 執行今日模擬倉，更新 paper_trading.db
#   3. notion_reporter    — 推送今日報告到 Notion
#
# Crontab 範例（與 main.py 同用一台主機）：
#   35 13 * * 1-5  cd /home/user/ai_stock && bash post_market.sh >> logs/post_market.log 2>&1
#
# 環境變數（寫在 .env 或匯出到 shell）：
#   NOTION_TOKEN=secret_xxx
#   NOTION_DB_ID=1031a6aff6b343ccbed80f24ae9f3b90  # （選填，已有預設值）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 載入 .env（若存在）
# grep -v '^#' 只濾整行註解。.env 裡有 18 行是 `KEY=value  # 中文說明`，
# 那些中文會被 xargs 拆成獨立的詞，export 收到就報 not a valid identifier；
# 本檔第 16 行是 set -euo pipefail，於是腳本死在這裡，後面三個盤後任務
# 一個都沒跑到（2026-08-19 之前每天都是這樣，launchctl 一直顯示 exit=1）。
# 這裡改用 start_all.sh 早就在用的三層過濾：只留有 = 的行、砍掉行尾註解。
if [ -f .env ]; then
  export $(grep -v '^#' .env | grep '=' | sed 's/[[:space:]]*#.*//' | xargs)
fi

echo "[$(date '+%H:%M:%S')] ── 開始收盤後學習任務 ──"

# 1. 更新學習配置
echo "[$(date '+%H:%M:%S')] 步驟 1／3：更新 AI 學習配置..."
python3 adaptive_scorer.py --days 90
echo "[$(date '+%H:%M:%S')] adaptive_scorer 完成"

# 2. 執行今日模擬倉
echo "[$(date '+%H:%M:%S')] 步驟 2／3：執行今日模擬倉..."
python3 dt_paper_trade.py --run
echo "[$(date '+%H:%M:%S')] dt_paper_trade 完成"

# 3. 推送 Notion 報告
echo "[$(date '+%H:%M:%S')] 步驟 3／3：推送報告到 Notion..."
python3 notion_reporter.py
echo "[$(date '+%H:%M:%S')] notion_reporter 完成"

echo "[$(date '+%H:%M:%S')] ── 收盤後任務全部完成 ──"
