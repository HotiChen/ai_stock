#!/bin/bash
cd "$(dirname "$0")"

echo "啟動 AI 自動交易系統..."

# 背景啟動 auto_trader
python3 auto_trader.py &
TRADER_PID=$!
echo "✅ auto_trader 已啟動 (PID: $TRADER_PID)"

# 前景啟動 Streamlit
streamlit run app.py

# Streamlit 關閉時，一起停掉 auto_trader
echo "🛑 關閉 auto_trader..."
kill $TRADER_PID
