# AI Stock System — 系統邏輯文件

## 架構總覽

```
start.sh
 ├── python3 main.py        → 排程器（背景）
 └── streamlit run app.py   → Dashboard（前景，瀏覽器 localhost:8501）
```

---

## 每日排程（`main.py`）

```
週一～週五，main loop 每 30 秒 poll 一次時間

08:30 → PremarketJob.run()
09:00 → MarketOpenJob.run()
13:35 → PostMarketJob.run()
```

---

## 一、盤前分析（08:30 `PremarketJob`）

```
candidates（候選股清單）
    │
    ▼
run_deep_analysis(code)          ← deep_analyzer.py
    │
    ├── get_price_trend_summary() ← yfinance 抓 20 日收盤
    ├── fetch_indicators()        ← technical_indicators.py
    │       計算：MA5/MA20/MA60, RSI, KD, 布林通道, 量比
    │
    └── build_deep_prompt()
            傳給 call_haiku()     ← Anthropic Haiku（輕量、便宜）
            回傳 JSON → parse_deep_response()
            得到 DeepAnalysis {signal, confidence, target_price, stop_loss_price}

    只保留 signal == "buy" 的股票
    │
    ▼
validate_plan()                  ← risk_guard.py
    │
    ├── 1. 黑名單過濾
    ├── 2. 除息日過濾（TWSE OpenAPI）
    ├── 3. 漲跌停過濾（±10% 判斷）
    ├── 4. 單一部位上限（≤ 5% 總資金）
    └── 5. 板塊集中度（≤ 30% 總資金）
    回傳 {approved: [...], rejected: [...]}
    │
    ▼
save_daily_plan()                ← research_db.py → daily_plans 表

send_confirmation()              ← user_confirm.py → Telegram
    傳送 inline keyboard：
    ✅ 2330 台積電 / ❌ 拒絕
    ✅ 全部批准   / ❌ 全部拒絕
```

---

## 二、技術指標評分（`rules.py`）

```
evaluate_signals(indicators_dict) → signals 清單

BUY 訊號（正分）：
  - 均線黃金交叉      weight 2
  - 均線多頭排列      weight 1
  - 帶量突破壓力      weight 3
  - 帶量站上均線      weight 2
  - 量縮回測支撐      weight 2
  - KD 低檔黃金交叉   weight 3
  - RSI 超賣          weight 2
  - RSI 穿越 50       weight 2
  - MACD 柱狀翻正     weight 2

SELL 訊號（負分）：
  - 均線死亡交叉      weight 2
  - 均線空頭排列      weight 1
  - 跌破支撐          weight 3
  - 爆量長黑          weight 3
  - KD 高檔死亡交叉   weight 3
  - MACD 柱狀翻負     weight 2

score_signals() → recommendation
  buy ≥ 5 & sell = 0 → 強力買進
  buy ≥ 3 & sell = 0 → 買進
  sell ≥ 5           → 強力賣出
  sell ≥ 3           → 賣出
  其他               → 觀察
```

---

## 三、開盤下單（09:00 `MarketOpenJob`）

```
approved_picks（盤前審核通過的清單）
    │
    ▼
api.snapshots([contract])        ← Shioaji simulation 取即時價

place_stock_order()              ← executor.py
    │
    ├── Guard 1：重複委託（同 code + action + 日期）→ 拒絕
    ├── Guard 2：計算 lot_type
    │       budget ≥ price × 1000 → common（整張，1000 股）
    │       budget < price × 1000 → intraday_odd（零股，盤中 09:00-13:30）
    ├── Guard 3：計算數量
    │       common：       budget // (price × 1000) 張
    │       intraday_odd： budget // price 股
    ├── Guard 4：硬性金額上限（預設 150,000 元）
    └── api.place_order(contract, _OrderSpec)
            action:     Buy / Sell（sc.Action）
            price_type: LMT 限價 / MKT 市價
            order_lot:  Common / IntradayOdd

    成功 → save_daily_trade()    ← research_db.py → daily_trades 表
    │
    ▼
MonitorAgent.start()             ← monitor_agent.py
```

---

## 四、盤中監控（`MonitorAgent`）

```
MonitorAgent
    │
    ├── Thread 1：AlertWorker
    │       監聽 Queue，收到 alert：
    │       save_alert()          ← research_db.py → alerts 表
    │       send_telegram()       ← Telegram Bot 推送手機
    │
    └── Thread 2：_poll_loop（每 30 秒）
            api.snapshots()       ← Shioaji simulation 取現價
                │
                check_price_alerts()
                    current_price ≥ target_price  → alert "target_hit"（高）
                    current_price ≤ stop_loss     → alert "stop_loss"（高）
                        │
                    放入 Queue → AlertWorker 處理
```

---

## 五、強制停損（`executor.py`）

```
force_stop_loss(api, code, quantity)
    │
    └── api.place_order(contract, _OrderSpec)
            action:     sc.Action.Sell
            price_type: sc.StockPriceType.MKT   ← 市價，立即成交
```

---

## 六、收盤（13:35 `PostMarketJob`）

```
MonitorAgent.stop()
    └── running = False + 送 None 到 Queue（poison pill）

save_daily_summary(DailySummaryRow)  ← research_db.py → daily_summaries 表
    {execution_id, date, total_pnl, target_met, review}
```

---

## 七、AI 對話顧問（Dashboard）

```
用戶輸入問題
    │
call_anthropic_chat(messages)    ← chat_agent.py
    │
    └── system prompt：
            持倉、資金、最近 AI 決策
            策略框架（MA/RSI/MACD/巴菲特/停損原則）
    └── call_sonnet(prompt)      ← Anthropic Sonnet（深度推理）
    │
回傳繁體中文建議
```

---

## 八、資料庫（`research_db.py` SQLite）

| 表 | 用途 |
|----|------|
| `daily_plans` | 每日盤前 AI 選股計劃（code、budget、sector） |
| `daily_trades` | 每筆實際委託記錄（action、price、amount、pnl） |
| `alerts` | 觸價 / 停損警報（sent=0 待推送） |
| `daily_summaries` | 每日收盤總結（pnl、review） |
| `stock_analyses` | 個股深度分析快取 |
| `market_context` | 大盤背景資料 |

---

## 九、安全機制

| 機制 | 位置 | 說明 |
|------|------|------|
| Prompt Injection 防護 | `ai_client.build_safe_prompt()` | 外部資料用 `<external_data>` 隔離，截斷 500 字 |
| 重複委託防護 | `executor.is_duplicate_order()` | 同股同向當日只下一次 |
| 金額硬上限 | `executor.check_hard_limit()` | 單筆 ≤ 15 萬（可由 `.env` 調整） |
| 黑名單 | `risk_guard.is_blacklisted()` | 硬編碼 + 可擴充 |
| 人工確認 | `user_confirm.send_confirmation()` | Telegram inline keyboard，下單前必須人工按 |
| 模擬模式 | `SIMULATION=true` | Shioaji simulation=True，不動真實資金 |

---

## 十、AI 模型分配

| 任務 | 模型 | 原因 |
|------|------|------|
| 個股深度分析（盤前） | `claude-haiku-4-5-20251001` | 量大、需快速、輕量足夠 |
| 策略生成（Dashboard） | `claude-sonnet-4-6` | 需深度推理、三套策略比較 |
| AI 對話顧問 | `claude-sonnet-4-6` | 多輪對話、需理解持倉脈絡 |

---

## 十一、環境變數（`.env`）

| 變數 | 說明 |
|------|------|
| `ANTHROPIC_API_KEY` | Anthropic API 金鑰 |
| `SHIOAJI_API_KEY` | 永豐金 API 金鑰 |
| `SHIOAJI_SECRET_KEY` | 永豐金 Secret |
| `SIMULATION` | `true` = 模擬模式（預設） |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 授權的 Telegram Chat ID |
| `ORDER_HARD_LIMIT` | 單筆委託金額上限（預設 150000） |
| `BUDGET` | 總可用資金（預設 100000） |
| `DB_PATH` | SQLite 路徑（預設 `data/research.db`） |
| `MONITOR_POLL_SECONDS` | 監控輪詢間隔（預設 30 秒） |
