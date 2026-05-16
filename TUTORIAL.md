# AI Stock 系統教學

> **文件定位**：本文件為**主運營手冊（Single Source of Truth）**。
> 架構細節參見 `SYSTEM.md`；上線放行標準參見 `docs/go_no_go_checklist_2026-05-09.md`。

## 目錄

1. [系統概覽](#1-系統概覽)
2. [前置需求](#2-前置需求)
3. [設定 `.env`](#3-設定-env)
4. [啟動系統](#4-啟動系統)
5. [每日流程詳解](#5-每日流程詳解)
6. [Telegram Bot 指令](#6-telegram-bot-指令)
7. [緊急處理](#7-緊急處理)
8. [資料庫結構](#8-資料庫結構)
9. [模組地圖](#9-模組地圖)
10. [已知限制與尚未接通的功能](#10-已知限制與尚未接通的功能)
11. [如何擴充](#11-如何擴充)

---

## 1. 系統概覽

```
┌─────────────────────────────────────────────────────────────┐
│                    AI Stock 三層架構                         │
├──────────────┬──────────────────┬───────────────────────────┤
│  分析層       │  執行層           │  監控層                   │
│              │                  │                           │
│ deep_analyzer│ main.py          │ monitor_agent.py          │
│ risk_guard   │ ├─ 08:30 盤前    │ ├─ 每 30 秒 snapshot      │
│ market_scanner│ ├─ 09:00 開盤   │ ├─ 達標/停損 alert        │
│              │ ├─ 13:25 強制平倉│ └─ AlertWorker → notifier │
│              │ └─ 13:35 收盤    │                           │
└──────────────┴──────────────────┴───────────────────────────┘
        ↕                ↕                      ↕
   Anthropic API    Shioaji API           Telegram Bot
   (分析/決策)     (報價/下單)           (通知/指令)
        ↕
   research.db (SQLite)
```

系統由三個獨立程序組成：

| 程序 | 啟動方式 | 職責 |
|------|---------|------|
| `main.py` | 背景 | 三個排程任務（盤前/開盤/收盤） |
| `telegram_bot.py` | 背景 | 接收手機指令、推送通知 |
| `app.py` | 前景 | Streamlit Dashboard（可選） |

---

## 2. 前置需求

### Python 套件

```bash
pip install shioaji anthropic python-dotenv requests streamlit yfinance pandas
```

### 帳號申請

| 服務 | 用途 | 申請網址 |
|------|------|---------|
| 永豐金 Shioaji | 報價 + 模擬/實盤下單 | [fubon.com](https://www.sinotrade.com.tw) |
| Anthropic API | AI 分析（Claude） | console.anthropic.com |
| Telegram Bot | 通知 + 遠端控制 | @BotFather |

### 建立 Telegram Bot

```
1. 在 Telegram 搜尋 @BotFather
2. 傳 /newbot，取得 BOT_TOKEN
3. 開啟 https://api.telegram.org/bot<TOKEN>/getUpdates
   傳任一訊息給 Bot，從 response 取得你的 chat_id
```

---

## 3. 設定 `.env`

在專案根目錄建立 `.env`（已列入 `.gitignore`，不會被 commit）：

```env
# ── Anthropic API ──────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...

# ── Telegram Bot ───────────────────────────────────────────
TELEGRAM_BOT_TOKEN=1234567890:AAE...
TELEGRAM_CHAT_ID=你的chat_id          # 只有這個 ID 可以操控 bot
TELEGRAM_VERBOSE=false                # true = 每股細節都推送

# ── 永豐金 Shioaji ─────────────────────────────────────────
SHIOAJI_API_KEY=你的API_KEY
SHIOAJI_SECRET_KEY=你的SECRET_KEY
SHIOAJI_SIMULATION=true              # true = 模擬模式（不動真錢）

# ── 資金設定 ───────────────────────────────────────────────
BUDGET=100000                        # 每日可用資金（元）
ORDER_HARD_LIMIT=150000              # 單筆委託金額上限（元）

# ── 資料庫 ─────────────────────────────────────────────────
DB_PATH=data/research.db

# ── 監控間隔 ───────────────────────────────────────────────
MONITOR_POLL_SECONDS=30              # 報價輪詢間隔（秒）
```

> **重要**：`SHIOAJI_SIMULATION=true` 時系統只模擬，不會真的下單。  
> 切換到 `false` 前請務必先用模擬模式跑完整一天確認邏輯正確。

---

## 4. 啟動系統

### 方式 A：一鍵啟動（推薦）

```bash
chmod +x start.sh
./start.sh
```

`start.sh` 做了什麼：
1. 背景啟動 `main.py`（排程）
2. 背景啟動 `telegram_bot.py`（Bot）
3. 前景啟動 `streamlit run app.py`（Dashboard）
4. 關閉 Dashboard 時自動清除背景程序

### 方式 B：分開啟動（除錯用）

```bash
# 終端 1
python3 main.py

# 終端 2
python3 telegram_bot.py

# 終端 3（可選）
streamlit run app.py
```

### 驗證啟動成功

Telegram 應收到：
```
🚀 AI Stock 系統啟動
模式：🟡 模擬模式
排程：08:30 / 09:00 / 13:35
```

---

## 5. 每日流程詳解

```
時間       動作                             Telegram 通知
────────────────────────────────────────────────────────────────
08:30  ┌─ market_scanner 掃描熱門股          🔍 盤前分析開始
       ├─ deep_analyzer 逐股 AI 分析         （VERBOSE=true 才推每股）
       ├─ risk_guard 風控審查                🛡️ 風控審查完成（含摘要）
       ├─ save_daily_plan → research.db     
       └─ send_confirmation → Telegram      📊 今日選股建議（可確認）

09:00  ┌─ executor.place_stock_order         🔔 09:00 開盤
       ├─ save_daily_trade → DB             📋 委託成功（每筆）
       └─ MonitorAgent.start()             

盤中   ┌─ 每 30 秒 api.snapshots()           
       ├─ check_price_alerts()              🎯 達到目標價！（若觸發）
       └─ AlertWorker → notifier           🚨 觸及停損！（若觸發）

13:25  ┌─ ForceCloseJob.run()               🔔 13:25 強制平倉
       └─ 所有持倉以市價賣出（整股/零股依 lot_type 自動判斷）

13:35  ┌─ MonitorAgent.stop()               🔕 13:35 收盤
       └─ save_daily_summary → DB          📈 今日損益

隨時   └─ Telegram Bot 指令（見第 6 節）
```

### 盤前分析（AI 如何決定買或不買）

`deep_analyzer.run_deep_analysis()` 會分析五個維度：

| 維度 | 資料來源 | 權重說明 |
|------|---------|---------|
| 歷史走勢 | yfinance 20 日 | MA5/MA20 趨勢 |
| 新聞面 | 傳入的 news 參數 | 正負面情緒 |
| 題材面 | 傳入的 theme_info | 是否有題材 |
| 美股影響 | 傳入的 market_summary | 前一日美股 |
| 台灣政策 | 傳入的 market_summary | 政策利多/利空 |

回傳 `DeepAnalysis`，包含：
- `signal`：`"buy"` / `"hold"` / `"sell"`
- `confidence`：1–10（影響預算分配，見下）
- `target_price`、`stop_loss_price`

### 預算分配（confidence 權重）

```
confidence = 1  →  資金的 2.0%
confidence = 5  →  資金的 3.3%
confidence = 10 →  資金的 5.0%  （risk_guard 上限）
```

`risk_guard.validate_plan()` 會再套用：
- 單股上限 5%（`MAX_POSITION_RATIO`）
- 同類股合計上限 30%（`MAX_SECTOR_RATIO`）
- 除息日過濾
- 漲跌停過濾
- 黑名單過濾

---

## 6. Telegram Bot 指令

Bot 啟動後按 **Menu** 鍵會出現 8 個按鈕：

| 按鈕 | 動作 |
|------|------|
| 📊 今日狀態 | 今日委託金額、損益、筆數 |
| 💼 持倉 | 今日買進明細 |
| 📈 選股計劃 | 08:30 AI 選出的清單（含目標/停損價） |
| ⚡ 快速下單 | 查看計劃並確認（測試版：下單由排程執行） |
| 🛡️ 停損設定 | 設定個股停損提醒 |
| ❓ 說明 | 完整操作說明 |
| 🚨 緊急暫停 | 立即停止系統（寫入 HALT flag） |
| 🔄 撤銷所有委託 | 呼叫 Shioaji 取消所有未成交委託 |

### 文字指令

```
停損 2330 540        → 設定 2330 停損提醒為 540 元
恢復系統             → 清除 HALT flag，排程恢復運行
/start 或 /menu     → 重新顯示選單
```

---

## 7. 緊急處理

### 按 🚨 緊急暫停 發生什麼事？

1. 寫入 `data/HALT` 檔案
2. `main.py` 的主迴圈每次都檢查此檔案，有就 `sleep(30)` 不執行任何排程
3. Telegram 廣播通知
4. **不會自動取消委託** — 需另按 🔄 撤銷所有委託

```
🚨 緊急暫停
   ↓
data/HALT 存在
   ↓
main.py 主迴圈跳過所有排程（每 30 秒 check 一次）
   ↓
傳「恢復系統」解除
```

### HALT flag 跨程序重啟仍有效

`data/HALT` 是磁碟檔案，重新啟動 `main.py` 也會繼續停止，直到手動傳「恢復系統」。

---

## 8. 資料庫結構

SQLite 位於 `data/research.db`（或 `DB_PATH` 指定的路徑）：

| 資料表 | 內容 | 主要欄位 |
|--------|------|---------|
| `daily_plans` | 每日選股計劃 | date, picks_json |
| `daily_trades` | 實際委託記錄 | trade_date, code, action, quantity, price, pnl, lot_type, sector |
| `daily_summaries` | 每日收盤總結 | date, total_pnl, target_met |
| `alerts` | 價格警報記錄 | code, alert_type, current_price, sent |
| `stock_analysis` | AI 分析快取 | code, signal, confidence, factors_json |
| `market_context` | 市場脈絡快照 | sp500_change, vix, sentiment |

查詢範例：

```bash
sqlite3 data/research.db "SELECT trade_date, code, name, action, quantity, price, lot_type, sector FROM daily_trades ORDER BY trade_date DESC LIMIT 20;"
```

---

## 9. 模組地圖

```
main.py                 ← 啟動入口，三個排程任務
├── deep_analyzer.py    ← AI 分析（呼叫 Anthropic Claude）
├── risk_guard.py       ← 風控過濾（除息/漲跌停/上限）
├── executor.py         ← 實際下單（Shioaji API）
├── monitor_agent.py    ← 盤中監控（每 30 秒輪詢）
├── notifier.py         ← 統一 Telegram 推送
├── halt.py             ← HALT flag 讀寫
├── research_db.py      ← SQLite 讀寫
└── user_confirm.py     ← 盤前確認訊息發送

telegram_bot.py         ← Bot 長輪詢主程式
├── halt.py             ← 緊急暫停/恢復
├── notifier.py         ← 推送
└── research_db.py      ← 查詢今日狀態/計劃

app.py                  ← Streamlit Dashboard（獨立，不影響排程）
├── market_scanner.py   ← 掃描熱門股（App 內部用）
├── technical_indicators.py ← K 線技術指標
├── strategies.py       ← 策略計算
└── sim_engine.py       ← 模擬回測
```

---

## 10. 已知限制與尚未接通的功能

以下功能程式碼已存在但尚未完整接通，**現階段屬於已知 stub**：

### 🔴 高優先（影響核心流程）

**1. 候選股清單是空的**

`main.py` 第 302 行：
```python
job = PremarketJob(candidates=[], ...)  # ← 永遠空！
```
`market_scanner` 模組已完整，但沒有被呼叫。需要在這裡填入掃描結果：
```python
from market_scanner import MarketScanner, ScanCriteria
scanner = MarketScanner(api)
candidates = scanner.run(ScanCriteria(top_n=20))
job = PremarketJob(candidates=[c.__dict__ for c in candidates], ...)
```

**2. 用戶 approve/reject 沒有實際作用**

08:30 發送確認訊息後，09:00 的 `approved_picks` 是 `risk_guard` 通過的全部 picks，
Telegram 上按 ✅/❌ 只會回覆文字，不會修改 `daily_plans` 的執行清單。

完整做法：按下 approve/reject 後需更新 `daily_plans`，
09:00 時重新從 DB 讀取過濾後的清單再下單。

**3. ✅ prior_orders 永遠為空（已修正 2026-05-11）**

~~`job = MarketOpenJob(..., prior_orders=[])`~~
`load_prior_orders()` 現在從 Shioaji `api.list_trades()` 載入當日已有委託，
每筆記錄包含 `{code, action, date, quantity, price}`，
`is_duplicate_order()` 可正確比對同股同向同日而拒絕重複下單。

### 🟡 中優先（影響風控品質）

**4. ✅ current_positions 永遠為空（已修正 2026-05-11）**

~~`risk_guard` 的 sector 倉位計算從 0 開始，無法考慮昨日留倉。~~
`load_current_positions()` 現在回傳 risk_guard 相容格式
`{code, name, sector, value, lot_type, quantity, price}`，
`validate_plan()` 同時基於現有持倉計算單股與板塊總曝險。

**5. ✅ 13:25 強制平倉未接入 main.py（已修正 2026-05-11）**

~~`executor.force_stop_loss()` 存在，但 `main.py` 的排程只有 13:35 收盤。~~
`ForceCloseJob` 現已接入 `main.py` 排程（13:25），
從 `daily_trades.lot_type` 欄位正確讀取整股/零股並平倉。

**6. `mark_alert_sent()` 未被呼叫**

`alerts` 資料表有 `sent` 欄位，`mark_alert_sent()` 也有，
但 `AlertWorker` 處理完 alert 後從未標記為已送，
重啟後 `load_pending_alerts()` 會重複回傳同樣的 alerts。

### 🟢 低優先（品質改善）

**7. `user_confirm.py` 的 `_API_BASE` 在 import 時固化**

與之前修復的 issue #6 相同問題，`_API_BASE = f"...{TELEGRAM_BOT_TOKEN}"` 在模組載入時就已確定，
測試時 monkeypatch token 不會更新 URL。

**8. 台灣國定假日判斷 ⚠️ 部分實作（2026-05-12）**

`is_trading_day()` 已整合 `tw_trading_calendar.py`，2026 年**確定性**假日已內建（元旦、春節、勞動節等）。
已知限制：
- **端午節、中秋節尚未加入**（2026 年確切日期待 TWSE 正式公告）。這些日期目前會被當成**交易日**，不會觸發任何告警（因為年份本身已存在於曆法中）。
- `tw_trading_calendar._TWSE_HOLIDAYS` 僅收錄 2026 年；未來年份**不會自動覆蓋**。
- 兩種告警情境：
  - 「**年份完全未收錄**」（如 2030）→ `is_trading_day()` 每次呼叫都會發出 `WARNING`，提示退化為 weekday-only。
  - 「**年份存在但曆法不完整**」（如 2026）→ `is_trading_day()` 每年**僅第一次呼叫**發出 `WARNING`，提示部分假日可能缺漏，之後不再重複。

維護方式：每年 11 月前更新 `tw_trading_calendar.py`，官方來源：https://www.twse.com.tw/zh/news/notice（搜尋「休市」）。更新後，同時將該年移出 `_INCOMPLETE_CALENDAR_YEARS`。

**9. Shioaji 斷線後無重連**

網路中斷後 `api` 物件不會自動重新連線，需要重啟程序。

---

## 11. 如何擴充

### 新增選股邏輯

1. 在 `market_scanner.py` 的 `score_snapshot()` 調整評分公式
2. 在 `ScanCriteria` 加入新過濾條件
3. 補上 `main.py` 中 `candidates=[]` 的缺口

### 新增風控規則

在 `risk_guard.validate_plan()` 的 `for pick in picks:` 迴圈中加入新 rule：
```python
# 範例：過濾流動性不足的股票（成交量 < 1000 張）
if pick.get("volume", 0) < 1000:
    result["reason"] = "volume_too_low"
    rejected.append(result)
    continue
```

### 新增 Telegram 按鈕

1. 在 `telegram_bot.py` 的 `send_main_menu()` 加入按鈕文字
2. 在 `_HANDLER_NAMES` dict 加入對應 handler 名稱
3. 實作 `handle_xxx(chat_id: str)` 函式

### 切換到實盤

```env
SHIOAJI_SIMULATION=false
```

切換前清單：
- [ ] `candidates=[]` 已補上真實掃描邏輯
- [ ] `prior_orders` 已從 API 載入
- [ ] 在低資金帳戶測試過完整一天流程
- [ ] `ORDER_HARD_LIMIT` 設定到可接受的上限
- [ ] Telegram 緊急暫停按鈕可以正常運作

---

## 快速排查

| 問題 | 可能原因 | 檢查方式 |
|------|---------|---------|
| Bot 沒反應 | `telegram_bot.py` 沒啟動 | `ps aux \| grep telegram_bot` |
| 08:30 沒分析 | `candidates=[]` 未填 | 見第 10 節 |
| 09:00 沒下單 | HALT flag 存在 | `ls data/HALT` |
| 下單失敗 | Shioaji session 過期 | 重啟 main.py |
| Alert 重複推送 | `mark_alert_sent` 未呼叫 | 見第 10 節 |
| 通知沒收到 | TOKEN 或 CHAT_ID 錯 | `curl https://api.telegram.org/bot<TOKEN>/getMe` |
