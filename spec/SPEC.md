# QUANT·AI · 台股 AI 量化交易工作站 — UI/UX 實作規格

> 本文件為 AI 編碼助手（Claude Code、Cursor 等）將設計套入既有 Python/Streamlit 後端的完整規格。
> 對應後端：`ai_stock/` 目錄（SYSTEM.md 詳述邏輯）
> 對應設計：`index.html` 設計畫布，共 17 畫面
> 版本：v0.1 · 2026-05-24

---

## 0. 文件總覽

| 檔名 | 內容 |
|---|---|
| `SPEC.md` | 本檔。設計總則 + 畫面索引 |
| `DESIGN_TOKENS.md` | 顏色 / 字型 / 間距 / 元件 token，含 CSS 變數、Python 對應 |
| `SCREENS.md` | 每個畫面的版面、欄位、資料來源、互動行為 |
| `DATA_SHAPES.md` | 前端期望的資料結構，含 Python dataclass / TypedDict 對照 |
| `BACKEND_MAPPING.md` | 既有 `*.py` 模組 → 前端畫面 / API endpoint 對照 |
| `IMPLEMENTATION_PLAN.md` | 建議實作順序、技術選型、里程碑 |

---

## 1. 設計總則（DO / DON'T）

### DO
- **資訊密度優先**：Bloomberg 機構等級。寧可資訊多、字小，不留無謂留白
- **數字用等寬字型**（IBM Plex Mono）：所有價格、百分比、tokens、ID 一律 mono
- **台股漲跌色**：上漲 `#c8332b`（紅）、下跌 `#2e7d4f`（綠），**絕不能反**
- **每個 AI 決策可追溯**：信心分數、來源模型、推理鏈、時間戳，使用者一鍵看得到
- **13:25 強制平倉倒數常駐**：每個和持倉相關的畫面都要顯示
- **模擬/真實雙模式**：UI 須明顯區分（金色警示 vs 紅色警示）
- **小數位**：價格 < 100 → 2 位；100–1000 → 1 位；> 1000 → 0 位
- **千分位**：金額 ≥ 1000 一律加千分位逗號

### DON'T
- 不要用陰影、漸層、emoji 當主視覺
- 不要用圓角 > 4px（pill / toggle 例外）
- 不要用粗框線；hairline 一律 1px
- 不要把英美漲跌色（綠漲紅跌）用在台股 UI
- 不要把 AI 信心顯示為單一數字，要附「高/中/低」分級
- 不要在 list row 上用 hover 動畫（機構級工具靜態為佳）

---

## 2. 17 畫面索引

| # | 路由 | 中文 | 後端對應 | 優先級 |
|---|------|------|---------|--------|
| 01 | `/login` | 登入 | (新) FastAPI auth | P0 |
| 02 | `/` | Dashboard 總覽 | `app.py` 主頁 | P0 |
| 03.1 | `/predict` | AI 預測 · Top N | `morning_briefing.py` `candidate_builder.py` | **P0 ★** |
| 03.2 | `/predict/:code` | AI 預測 · 個股深度 | `deep_analyzer.py` `technical_indicators.py` | **P0 ★** |
| 03.3 | `/predict/:code/reasoning` | AI 預測 · 推理過程 | `ai_client.py` `chat_agent.py` (trace mode) | **P0 ★** |
| 04.1 | `/daytrade` | AI 當沖 · 駕駛艙 | `monitor_agent.py` `intraday_monitor.py` | **P0 ★** |
| 04.2 | `/daytrade/:code/chart` | AI 當沖 · K 線標記 | `daytrading_analyzer.py` `chip_data.py` | **P0 ★** |
| 04.3 | `/daytrade/order` | AI 當沖 · 下單流程 | `executor.py` `user_confirm.py` `risk_guard.py` | **P0 ★** |
| 05.1 | `/portfolio` | 持倉 | `portfolio.py` `shioaji_portfolio.py` | P1 |
| 05.2 | `/journal` | 學習日誌 + AI 顧問 | `learning_db.py` `chat_agent.py` | P1 |
| 05.3 | `/simulate` | 回測 | `simulate.py` `sim_engine.py` | P2 |
| 05.4 | `/report` | 週報 | `weekly_report.py` `daily_tracker.py` | P2 |
| 05.5 | `/scanner` | 大盤掃描 | `market_scan.py` `market_scanner.py` `news_agent.py` | P1 |
| 06 | `/settings` | 設定 | `config.py` `.env` | P0 |
| 07.1 | mobile `/m/predict` | 手機 · AI 預測卡 | 同 03.1 | P1 |
| 07.2 | mobile `/m/daytrade` | 手機 · 當沖實況 | 同 04.1 | P1 |
| 07.3 | telegram bot 流程 | Telegram 批准 | `telegram_bot.py` `user_confirm.py` | P0 |

「**P0 ★**」表示用戶最重視的兩大區塊（AI 預測 + AI 當沖），須最先實作完成。

---

## 3. 建議技術棧（給 AI agent）

**目前**：Streamlit (`app.py`)
**建議升級**：

| 層級 | 推薦 | 備註 |
|---|---|---|
| 後端 | FastAPI + SSE / WebSocket | 取代 Streamlit；既有 `*.py` 模組可直接包成 router |
| 即時資料 | WebSocket | 用於 04.1 駕駛艙的警報串流、K 線、持倉更新 |
| 前端 | React + TypeScript + Vite | 與設計檔 (jsx) 一致 |
| 路由 | React Router | 對應 §2 路由表 |
| 狀態 | Zustand / TanStack Query | 即時資料快取 |
| 圖表 | 自製 SVG（如設計檔） + lightweight-charts（K 線） | 不要用 Plotly/Chart.js，太厚重且風格不符 |
| 樣式 | CSS Modules 或 Tailwind（須客製 tokens） | 必須遵守 `DESIGN_TOKENS.md` |
| 部署 | Docker + Nginx | 後端 + 前端分離 |

**若要保留 Streamlit**：只實作核心 4 畫面（02、03.1、04.1、06），用 `streamlit-extras` + custom components。但本規格主要假設前後端分離。

---

## 4. 通用版面骨架

每個桌面畫面都用 `AppChrome` 容器：

```
┌─────────────────────────────────────────────────────────────┐
│ TOPBAR · LOGO │ EYEBROW · TITLE         · 大盤時鐘 + 倒數    │  44px
├──────────────┼──────────────────────────────────────────────┤
│              │                                              │
│ SIDEBAR      │  CONTENT                                     │
│ (200px)      │                                              │
│  - 總覽       │                                              │
│  - AI 預測 ★  │                                              │
│  - AI 當沖 ★  │                                              │
│  - 持倉       │                                              │
│  - 市場掃描   │                                              │
│  - 回測       │                                              │
│  - 學習日誌   │                                              │
│  - 週報       │                                              │
│              │                                              │
│ ─────────    │                                              │
│ 系統狀態      │                                              │
│  • Shioaji   │                                              │
│  • 模式       │                                              │
│  • 版本       │                                              │
├──────────────┴──────────────────────────────────────────────┤
│ STATUSBAR · 加權 · OTC · 匯率 · API · DB · LOAD              │  22px
└─────────────────────────────────────────────────────────────┘
```

- **TopBar** 高度 44px。左 logo 200px wide，右側顯示大盤盤中時鐘 + 距收盤倒數
- **Sidebar** 寬 200px。AI 預測 / AI 當沖 加上紅色 `★` 標記
- **StatusBar** 高度 22px。常駐大盤資訊
- **Content** 區內部結構由各畫面定義

側邊欄項目用 React 元件 `<NavItem id label kbd star active />`。點擊變更路由。

---

## 5. 互動模式

### 5.1 即時更新
- **WebSocket 連線**：駕駛艙、K 線圖、警報串流必須用 WS push（30s 內必收到 tick）
- **TopBar 大盤時鐘**：每秒更新
- **倒數至 13:25**：每秒更新，13:24 起閃爍 / 13:25 觸發 modal

### 5.2 鍵盤導航
| 快捷鍵 | 動作 |
|---|---|
| `D` | 跳到 Dashboard |
| `P` | 跳到 AI 預測 |
| `T` | 跳到 AI 當沖 |
| `H` | 跳到持倉 |
| `M` | 跳到大盤掃描 |
| `B` | 跳到回測 |
| `J` | 跳到學習日誌 |
| `R` | 跳到週報 |
| `⌘ K` | 喚出 AI 顧問對話框 |
| `⌘ ↵` | 在下單頁送出委託 |
| `Esc` | 關閉 modal / 退出 focus mode |

### 5.3 確認流程
- **任何下單 / 平倉操作** → 兩步驟：UI 點擊 → Telegram 二次確認
- **真實 / 模擬切換** → 跳 modal + 需輸入完整 email 才能切換
- **修改風控參數** → 即時顯示影響的持倉，按「儲存」後生效於下一個 PremarketJob

### 5.4 錯誤與空狀態
- **Shioaji 斷線** → TopBar 系統燈轉紅，所有下單按鈕禁用，顯示重連 banner
- **API 配額耗盡** → AI 預測頁顯示「使用快取結果」黃色 banner
- **空持倉** → 顯示「下一次盤前 08:30 自動選股，距 22:18:42」

---

## 6. 開發起手式（給 AI agent）

依此順序實作可最快出可用版本：

1. 讀 `DESIGN_TOKENS.md` → 建立 CSS 變數 / Tailwind config
2. 讀 `DATA_SHAPES.md` → 定義 TypeScript types + Pydantic models
3. 建 `AppChrome` 骨架（§4）
4. 實作 **04.1 駕駛艙**（最複雜、最高優先；其他元件多源自此頁）
5. 實作 **03.1 AI 預測 · Top N**
6. 補齊 03.2、03.3、04.2、04.3
7. 補齊 Settings、Login
8. 其他畫面（持倉、回測、週報、學習日誌、掃描）
9. 手機版

每完成一個畫面，與 `index.html` 對應 artboard 做視覺比對，pixel 級別校對。

---

## 7. 與既有 Python 模組的對應索引

| 設計畫面 | Python 模組（你已有的） | 用途 |
|---|---|---|
| 03.1 Top N | `morning_briefing.py`, `candidate_builder.py`, `risk_guard.py` | 每日推薦清單來源 |
| 03.2 深度分析 | `deep_analyzer.py`, `technical_indicators.py`, `rules.py` | 個股分析 + 指標評分 |
| 03.3 推理過程 | `ai_client.py` (`call_haiku`, `build_safe_prompt`), `chat_agent.py` | LLM 呼叫 trace |
| 04.1 駕駛艙 | `monitor_agent.py`, `intraday_monitor.py`, `alerts.py`, `daily_tracker.py` | 即時監控 |
| 04.2 K 線 | `daytrading_analyzer.py`, `chip_data.py`, `technical_indicators.py` | 分時資料 |
| 04.3 下單 | `executor.py`, `user_confirm.py`, `risk_guard.py`, `trades.py` | 委託流程 |
| 05.1 持倉 | `portfolio.py`, `shioaji_portfolio.py`, `sim_position_store.py` | 部位 |
| 05.2 學習日誌 | `learning_db.py`, `learning_report.py`, `chat_agent.py` | 日誌 + AI 顧問 |
| 05.3 回測 | `simulate.py`, `sim_engine.py`, `sim_settlement.py` | 回測引擎 |
| 05.4 週報 | `weekly_report.py`, `weekly_report_runner.py`, `daily_tracker.py` | 週報 |
| 05.5 掃描 | `market_scan.py`, `market_scanner.py`, `news_agent.py`, `futures_premium.py` | 大盤 |
| 06 Settings | `config.py`, `.env`, `themes.py` | 設定 |
| 07.3 Telegram | `telegram_bot.py`, `user_confirm.py`, `notifier.py` | 機器人 |

---

## 8. 變更請求協定

當你（使用者）想調整任何畫面，請指定：

```
畫面編號 + 想改的部份 + 期望結果
例：「4.1 駕駛艙的警報串流區，加上『靜音 30 分鐘』按鈕」
例：「3.3 推理頁的信心構成改成 waterfall chart」
```

`SCREENS.md` 中每個畫面都有 `<a id="screen-04-1">` 錨點，AI agent 可直接定位。

---

下一步：讀 `DESIGN_TOKENS.md`。
