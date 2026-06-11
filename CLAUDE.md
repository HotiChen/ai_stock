# QUANT·AI · 台股 AI 量化交易工作站 — 系統設計規範

> **本文件是所有開發工作的唯一權威來源。**
> 所有 Agent 在開始任何任務前必須先讀完本文件，再讀 `spec/` 對應章節。
> **若需變更本文件，必須先提出問題點並取得 Tim 的明確同意，不得自行修改。**

---

## 變更管控規則（Agent 必讀）

1. **禁止自行修改本文件**。若你認為某個設計有問題，請以文字提出：
   - 問題點是什麼
   - 建議的變更內容
   - 變更的原因
   然後等待 Tim 同意後才能修改。

2. **禁止實作 spec/ 未描述的功能**，除非 Tim 明確要求。

3. **技術決策若與本文件衝突，以本文件為準**。若有技術上的不可行性，提出說明，不要自行繞過。

4. **每個任務完成後，檢查實作是否與本文件一致**。若有偏差，主動回報。

5. **不得修改 repo 根目錄下任何既有 Python 模組**（main.py、executor.py、telegram_bot.py 等）。Backend 只能做薄包裝。

---

## 一、專案目標

將既有台股 AI 量化交易系統（Streamlit）升級為：

1. **FastAPI 後端**：包裝現有 Python 模組，提供 REST + WebSocket API
2. **React 前端**：Bloomberg 機構級 UI，17 個畫面，pixel-level 還原 spec/SCREENS.md
3. **即時推送**：駕駛艙、K 線、警報串流透過 WebSocket 更新
4. **Telegram 二次確認**：所有下單操作沿用既有 `user_confirm.py` 流程

**現有系統繼續運作**：`start.sh`（main.py 排程器 + Streamlit）保留作備援，不停機。

---

## 二、核心原則（DO / DON'T）

### 必須做到
- **台股漲跌色**：漲紅 `#c8332b` / 跌綠 `#2e7d4f`，**絕對不能反**
- **數字全用 IBM Plex Mono**，開啟 `font-feature-settings: "tnum" 1, "zero" 1`
- **13:25 強制平倉倒數**在任何持倉相關畫面常駐
- **每筆下單必經 Telegram 二次確認**（`user_confirm.py`）
- **不改動 repo 根目錄既有 Python 模組邏輯**，只做薄包裝
- **資訊密度優先**，機構級 Bloomberg 風格

### 絕對禁止
- 不用美股漲跌色（綠漲紅跌）
- 不用陰影、漸層、emoji 當主視覺
- 不用 Plotly / Chart.js / ECharts（用自製 SVG）
- 不把 AI 信心顯示為單一數字（要附高/中/低分級）
- 不重寫既有 Python 模組
- 不用圓角 > 4px（pill/toggle 例外）

---

## 三、技術棧

| 層級 | 技術 | 備註 |
|---|---|---|
| 既有後端 | Python（repo 根目錄 `*.py`） | **原封不動** |
| 新後端 | FastAPI + uvicorn | 薄包裝層，port 8000 |
| 即時推送 | WebSocket（FastAPI 原生） | 駕駛艙、K 線、市場資料 |
| 前端 | React + TypeScript + Vite | port 5173（dev） |
| 路由 | React Router | 對應 spec/SCREENS.md 的 17 個路由 |
| 狀態 | Zustand + TanStack Query | 即時資料快取 |
| 圖表 | 自製 SVG | 不用第三方圖表庫 |
| 樣式 | CSS 變數（無 Tailwind） | 嚴格遵守 spec/DESIGN_TOKENS.md |
| 字型 | IBM Plex Mono（數字）+ Helvetica Neue（UI） | Google Fonts CDN |
| Auth | JWT（python-jose） | 單用戶，.env 設定 email/password |

---

## 四、目錄結構

```
*.py（repo 根目錄）                ← 既有 Python 模組（main.py 等，禁止修改）
tests/                             ← 既有測試套件
spec/                              ← 設計規格（唯讀參考）
backend/
├── app/
│   ├── main.py                    ← FastAPI entry + CORS + router 掛載
│   ├── deps.py                    ← JWT auth dependency
│   ├── routers/
│   │   ├── auth.py                ← POST /api/auth/login, /refresh, /me
│   │   ├── dashboard.py           ← GET /api/dashboard
│   │   ├── predict.py             ← GET /api/predict/today, /:code, /:code/reasoning
│   │   ├── daytrade.py            ← GET /api/daytrade/live, /:code/chart
│   │   ├── order.py               ← POST /api/order/preview, /submit
│   │   ├── portfolio.py           ← GET /api/portfolio
│   │   ├── journal.py             ← GET /api/journal
│   │   ├── chat.py                ← POST /api/chat（SSE streaming）
│   │   ├── backtest.py            ← POST /api/backtest
│   │   ├── report.py              ← GET /api/report/weekly
│   │   ├── scanner.py             ← GET /api/scanner
│   │   ├── market.py              ← GET /api/market/snapshot
│   │   └── settings.py            ← GET/PATCH /api/settings
│   ├── ws/
│   │   ├── daytrade.py            ← WS /ws/daytrade
│   │   ├── market.py              ← WS /ws/market
│   │   └── chart.py               ← WS /ws/daytrade/:code/chart
│   └── schemas/
│       ├── base.py                ← Side, Signal, LotType, ThreadState, AlertLevel...
│       ├── auth.py                ← LoginReq, LoginResponse, User
│       ├── predict.py             ← Pick, TopNRun, DeepAnalysis, ReasoningTrace
│       ├── daytrade.py            ← Position, Alert, StrategyThread, DaytradeLive
│       ├── order.py               ← OrderTicket, OrderResult
│       ├── portfolio.py           ← PortfolioSummary
│       ├── journal.py             ← JournalEntry, ChatMessage
│       ├── backtest.py            ← BacktestResult
│       ├── settings.py            ← AppSettings
│       └── market.py              ← MarketSnapshot
frontend/
├── src/
│   ├── main.tsx                   ← React entry
│   ├── App.tsx                    ← Router 根元件
│   ├── index.css                  ← CSS 變數（DESIGN_TOKENS.md 全部）
│   ├── types/                     ← TypeScript types（對應 schemas/）
│   ├── components/                ← 共用元件
│   │   ├── AppChrome.tsx          ← Sidebar + TopBar + StatusBar
│   │   ├── Eyebrow.tsx
│   │   ├── Pill.tsx
│   │   ├── Button.tsx
│   │   ├── Card.tsx
│   │   ├── Sparkline.tsx
│   │   ├── ConfidenceBar.tsx
│   │   ├── ConfidenceTick.tsx
│   │   ├── DistRangeBar.tsx
│   │   ├── CountdownBar.tsx
│   │   ├── AlertRow.tsx
│   │   ├── ThreadRow.tsx
│   │   ├── TraceStep.tsx
│   │   ├── ContribRow.tsx
│   │   ├── GuardRow.tsx
│   │   ├── TelegramMsg.tsx
│   │   ├── KChart.tsx
│   │   ├── Kpi.tsx
│   │   ├── RiskCockpit.tsx
│   │   ├── EquityCurve.tsx
│   │   └── MonthlyHeatmap.tsx
│   ├── pages/
│   │   ├── Login.tsx              ← /login
│   │   ├── Dashboard.tsx          ← /
│   │   ├── predict/
│   │   │   ├── TopN.tsx           ← /predict
│   │   │   ├── DeepAnalysis.tsx   ← /predict/:code
│   │   │   └── Reasoning.tsx      ← /predict/:code/reasoning
│   │   ├── daytrade/
│   │   │   ├── Cockpit.tsx        ← /daytrade
│   │   │   ├── Chart.tsx          ← /daytrade/:code/chart
│   │   │   └── Order.tsx          ← /daytrade/order
│   │   ├── Portfolio.tsx          ← /portfolio
│   │   ├── Journal.tsx            ← /journal
│   │   ├── Simulate.tsx           ← /simulate
│   │   ├── Report.tsx             ← /report
│   │   ├── Scanner.tsx            ← /scanner
│   │   ├── Settings.tsx           ← /settings
│   │   └── mobile/
│   │       ├── MobilePredict.tsx  ← /m/predict
│   │       └── MobileDaytrade.tsx ← /m/daytrade
│   ├── hooks/                     ← useWebSocket, useMarket, useDaytrade...
│   ├── store/                     ← Zustand stores
│   └── api/                       ← fetch wrappers
```

---

## 五、API 規格

完整 API 路由與資料結構見：
- `spec/DATA_SHAPES.md` — 所有 Pydantic / TypeScript 型別
- `spec/BACKEND_MAPPING.md` — Python 模組 → router 對應
- `spec/SCREENS.md` — 每個畫面的版面與互動

**命名規則**：
- TypeScript field：`snake_case`（與 Python 一致，避免兩端轉換）
- URL：`/api/{domain}/{resource}/{action}`
- WebSocket：`/ws/{domain}` 或 `/ws/{domain}/:id`
- 時間：ISO 8601 字串
- 金額：整數 NTD（前端負責格式化）
- 百分比：raw 數字（`0.034` 而非 `"3.4%"`）

---

## 六、17 畫面索引與優先級

| 路由 | 畫面 | 優先級 | Milestone |
|---|---|---|---|
| `/login` | 01 登入 | P0 | M1 |
| `/settings` | 06 設定 | P0 | M1 |
| `/` | 02 Dashboard | P0 | M2 |
| `/predict` | 03.1 AI 預測 Top N | **P0 ★** | M2 |
| `/predict/:code` | 03.2 個股深度 | **P0 ★** | M2 |
| `/predict/:code/reasoning` | 03.3 推理過程 | **P0 ★** | M2 |
| `/daytrade` | 04.1 當沖駕駛艙 | **P0 ★** | M3 |
| `/daytrade/:code/chart` | 04.2 K 線標記 | **P0 ★** | M3 |
| `/daytrade/order` | 04.3 下單流程 | **P0 ★** | M3 |
| `/portfolio` | 05.1 持倉 | P1 | M4 |
| `/journal` | 05.2 學習日誌 | P1 | M4 |
| `/scanner` | 05.5 大盤掃描 | P1 | M4 |
| `/simulate` | 05.3 回測 | P2 | M4 |
| `/report` | 05.4 週報 | P2 | M4 |
| `/m/predict` | 07.1 手機預測 | P1 | M5 |
| `/m/daytrade` | 07.2 手機當沖 | P1 | M5 |
| Telegram bot | 07.3 批准流程 | P0 | M3 |

---

## 七、Agent 分工

### 核心 Agents

| Agent | Skill | 職責 |
|---|---|---|
| **Developer** | `general-purpose` | 實作程式碼，各 Milestone 的主力 |
| **Code Reviewer** | `code-review` | 每個 Milestone 完成後審查 diff |
| **Security Reviewer** | `security-review` | 審查 JWT、Telegram 確認流程、API 安全 |
| **Verifier** | `verify` | 驗證畫面與 spec/SCREENS.md 的視覺一致性 |

### Agent 工作分波（對應 Milestone）

```
Wave 1（M1 骨架，完全並行）
  [A] Developer (backend)  → FastAPI main.py + 所有 Pydantic schemas + auth + settings routers
  [B] Developer (frontend) → CSS 變數 + TypeScript types + 所有共用元件（20 個）

Wave 2（M1 完成後，並行）
  [C] Developer → backend predict routers（包裝 morning_briefing, deep_analyzer, risk_guard）
  [D] Developer → backend daytrade routers + WebSocket（包裝 monitor_agent, executor）
  [E] Developer → frontend Login（01）+ Settings（06）頁面
  [F] Developer → frontend AppChrome 整合 + React Router + Dashboard（02）

Wave 3（M2/M3，並行）
  [G] Developer → frontend 03.1 Top N + 03.2 深度 + 03.3 推理
  [H] Developer → frontend 04.1 駕駛艙 + 04.2 K 線 + 04.3 下單
  → Code Reviewer 審查 Wave 1+2 的 diff

Wave 4（M4，並行）
  [I] Developer → backend 05.x routers（portfolio, journal, chat SSE, backtest, report, scanner）
  [J] Developer → frontend 05.1–05.5 工作區頁面
  → Security Reviewer 審查 auth flow + Telegram 確認流程

Wave 5（M5+M6）
  [K] Developer → 手機版 07.1 + 07.2
  [L] Developer → polish（空狀態、skeleton、鍵盤導航、錯誤 banner）
  → Verifier 逐頁比對 spec/SCREENS.md

Wave 6（M7 QA）
  → Code Reviewer 全局 diff
  → Security Reviewer 最終安全審查
  → Verifier 完整驗收清單
```

### Agent 行為規範

1. 每個 Agent 開始工作前必須讀完本文件
2. 不得跨越自己的職責範圍
3. 發現問題超出職責，整理成清單回報，由 Tim 決定召喚哪個 Agent
4. 需要變更本文件，整理問題點回報，不得自行修改

---

## 八、開發 Milestone 快速索引

| Milestone | 目標 | 完成標準 |
|---|---|---|
| **M1** 骨架（3天）| FastAPI 起跑 + React 起跑 + 設計系統 + Login + Settings | 登入 → Settings 修改 BUDGET 後重整仍生效 |
| **M2** AI 預測（5天）| 03.1 + 03.2 + 03.3 | 08:30 選股後能看到 8 檔候選 + 完整推理過程 |
| **M3** AI 當沖（6天）| 04.1 + 04.2 + 04.3 + Telegram | 開盤後駕駛艙即時更新；下單 Telegram 確認通 |
| **M4** 工作區（4天）| 05.1–05.5 | 所有 sidebar 項目可點且有完整資料 |
| **M5** Dashboard（1天）| 02 整合頁 | Dashboard 聚合各模組資料 |
| **M6** 手機+Telegram（3天）| 07.x | iPhone Safari 可瀏覽 AI 預測 + 當沖實況 |
| **M7** Polish QA（2天）| 空狀態、skeleton、錯誤處理 | 全部驗收清單通過 |

詳細工項見 `spec/IMPLEMENTATION_PLAN.md`。

---

## 九、驗收清單（上線前必須通過）

- [ ] 一個完整交易日（09:00–13:35）在 simulation 模式跑通
- [ ] Telegram 確認鏈不會卡死（60s timeout）
- [ ] 13:25 強制平倉 100% 觸發
- [ ] 所有 17 個畫面視覺對齊 spec/SCREENS.md（Verifier 逐頁確認）
- [ ] 台股漲跌色正確（漲紅跌綠，非美股色）
- [ ] 所有數字使用 IBM Plex Mono + tabular numerals
- [ ] 鍵盤可導航全部主要動作（D/P/T/H/M/B/J/R/⌘K）
- [ ] 手機版（iPhone Safari）可正常瀏覽 AI 預測 + 當沖實況
- [ ] 模擬 / 真實切換需 email 全文確認
- [ ] 全螢幕讀取速度 < 1s（除 backtest）
- [ ] WebSocket 斷線自動重連

---

## 十、已知問題與待決定事項

> 此節由 Agent 在實作過程中回報問題，等待 Tim 決策後更新。
> **Agent 不得自行解決本節記錄的待決定事項。**

### M1 實作後（待填入）

| # | 問題 | 目前做法 | 建議 | 狀態 |
|---|------|---------|------|------|
| — | — | — | — | — |

### Security Review 待處理

| # | 問題 | 影響範圍 | 狀態 |
|---|------|---------|------|
| — | — | — | — |

---

## 十一、參考文件索引

| 文件 | 內容 |
|---|---|
| `spec/SPEC.md` | 設計總則 + 17 畫面索引 + DO/DON'T |
| `spec/DESIGN_TOKENS.md` | 所有顏色、字型、間距 CSS 變數 |
| `spec/DATA_SHAPES.md` | TypeScript types + Pydantic models |
| `spec/SCREENS.md` | 每個畫面的版面、欄位、互動細節 |
| `spec/BACKEND_MAPPING.md` | Python 模組 → FastAPI router 對應 |
| `spec/IMPLEMENTATION_PLAN.md` | 8 個 Milestone + 詳細工項 |
| `SYSTEM.md` | 既有後端邏輯說明（排程、選股、下單流程）|
