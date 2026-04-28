#!/bin/bash
cd "$(dirname "$0")"

echo "啟動 AI 股票系統（模擬模式）..."

# 背景啟動主排程（08:30/09:00/13:35 三個排程任務）
python3 main.py &
MAIN_PID=$!
echo "✅ main.py 已啟動 (PID: $MAIN_PID)"

# 背景啟動 Telegram Bot
python3 telegram_bot.py &
BOT_PID=$!
echo "✅ telegram_bot.py 已啟動 (PID: $BOT_PID)"

# 前景啟動 Dashboard
streamlit run app.py

# Dashboard 關閉時，一起停掉所有背景程序
echo "🛑 關閉背景程序..."
kill $MAIN_PID 2>/dev/null
kill $BOT_PID 2>/dev/null
wait $MAIN_PID 2>/dev/null
wait $BOT_PID 2>/dev/null
