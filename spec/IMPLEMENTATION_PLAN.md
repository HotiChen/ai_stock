# Implementation Plan · 實作里程碑

> 建議實作順序與技術選型。給 AI agent 與工程師參考。

---

## 0. 必要前置（Week 0）

- [ ] 讀完所有 spec 文件
- [ ] 開啟設計檔 `index.html` 對視覺有完整印象
- [ ] 開啟 `SYSTEM.md` 確認後端邏輯
- [ ] 確認技術棧（建議：FastAPI + React/Vite + WebSocket）
- [ ] 建立 monorepo：
  ```
  ai_stock/                  ← 既有 Python (原封不動)
  backend/                   ← 新 FastAPI
  frontend/                  ← 新 React/Vite
  spec/                      ← 本資料夾
  docker-compose.yml
  ```

---

## 1. Milestone M1 · 骨架 + 設計系統（3 天）

**目標**：能跑、能登入、骨架對得上設計

- [ ] FastAPI 起跑 + 健康檢查 `GET /api/health`
- [ ] React/Vite 起跑 + 路由（React Router）
- [ ] 套入 `DESIGN_TOKENS.md` 所有 CSS 變數 / Tailwind config
- [ ] 引入 IBM Plex Mono webfont
- [ ] 實作通用元件（最少這幾個）：
  - [ ] `<AppChrome>`（含 sidebar、topbar、statusbar）
  - [ ] `<Eyebrow>`, `<Pill>`, `<Mono>`, `<Bar>`, `<Spark>`
  - [ ] `<Btn>` （primary / danger / ghost / small）
  - [ ] `<Card>`, `<Tabs>`
- [ ] 實作 **01 Login** + **06 Settings**（這兩頁最簡單，先試水溫）
- [ ] Auth flow（JWT）+ 2FA placeholder
- [ ] `GET /api/settings` + `PATCH /api/settings`（直接讀寫 `.env`）

**Done 標準**：跑起來，登入 → 設定頁可以修改 BUDGET / ORDER_HARD_LIMIT，重新整理仍生效。

---

## 2. Milestone M2 · AI 預測（5 天）★

**目標**：使用者最重視的兩大區塊之一完成

### M2.1 (1 天) — 03.1 Top N
- [ ] `GET /api/predict/today` → `TopNRun`
  - 直接讀 `research_db.daily_plans` 最新一筆
  - 組合 picks 列表
- [ ] 前端畫面：
  - Sub-tabs 列
  - Run banner（含 stats）
  - 主表格（11 欄 grid）
  - 底部 3 卡（風控紀要 / 板塊集中度 / 預期分佈）
- [ ] `POST /api/predict/approve` + `/reject` 互動

### M2.2 (2 天) — 03.2 深度分析
- [ ] `GET /api/predict/:code` → `DeepAnalysis`
  - 包裹 `deep_analyzer.run_deep_analysis()`
- [ ] 前端畫面：
  - Identity strip + AI 判決區
  - 3 欄主體（價格圖 + 9 指標 + 情境/結論）
  - 自製 `<DeepChart>` SVG 元件
- [ ] 「批准」按鈕 → 串到 03.1 的批准 API

### M2.3 (2 天) — 03.3 推理過程 ★★ 最創新
- [ ] **後端**：擴充 `ai_client.py`
  - 新增 `trace_call()` 紀錄每步耗時 / tokens / cost
  - 新增 `ai_traces` SQLite 表
  - 既有 `call_haiku()` 改為 thin wrapper
- [ ] `GET /api/predict/:code/reasoning` → `ReasoningTrace`
- [ ] 前端 3 欄：
  - 左：`<TraceStep>` 9 step timeline + 成本明細
  - 中：Prompt + Response code 區（mono、紅邊）
  - 右：`<ContribRow>` 信心構成（中心對稱 bar）+ self-check + decision hash

**Done 標準**：每天 08:30 自動跑完盤前選股後，使用者打開 `/predict` 看到當日 8 檔候選；點任一檔可看完整深度分析；切到「推理過程」可看到 LLM 怎麼想出 0.86 信心。

---

## 3. Milestone M3 · AI 當沖（6 天）★

**目標**：使用者最重視的另一塊 + 全系統最即時

### M3.1 (2 天) — 04.1 駕駛艙
- [ ] **後端**：擴充 `monitor_agent.py` 加 `subscribe()` for WS
- [ ] `GET /api/daytrade/live` → `DaytradeLive`（初始 snapshot）
- [ ] `WS /ws/daytrade` → tick / alert / thread / countdown push
- [ ] 前端：
  - 倒數 bar（黑底，progress 漸層）
  - 4 宮格（持倉 / 警報 / 執行緒 / 風控）
  - `<DistRangeBar>`（SL ────●─── TP）
  - `<ThreadRow>` `<AlertRowDetailed>` `<RiskCockpit>`

### M3.2 (2 天) — 04.2 K 線標記
- [ ] **後端**：擴充 `monitor_agent.py` 把每次 AI 建議寫入 `ai_marks` 表
- [ ] `GET /api/daytrade/:code/chart` → `ChartView`
- [ ] `WS /ws/daytrade/:code/chart` → 新 tick + 新 mark
- [ ] 前端：
  - 自製 `<KChart>` SVG（K 棒 + MA + 標記）
  - 子圖 RSI
  - 側欄 AI mark log + 建議下一步

### M3.3 (2 天) — 04.3 下單流程
- [ ] `POST /api/order/preview` → `OrderTicket`（含 6 項風控）
- [ ] `POST /api/order/submit` → 觸發 `executor.place_stock_order()` + Telegram 二次確認
- [ ] 前端 3 欄：
  - 下單委託（含 AI 預填）
  - 風控檢查 + dry-run code 區
  - Telegram 鏡像 + 送出按鈕

**Done 標準**：交易日 09:00 開盤，4 檔自動下單，駕駛艙即時跑；任何停損警報 30s 內推到 UI + Telegram；點警報跳到該股的 K 線頁可看到 AI mark；任何手動操作（調 TP / 平倉）能送出且 Telegram 確認流程通。

---

## 4. Milestone M4 · 其他工作區（4 天）

- [ ] **05.1 持倉**（0.5 天）
  - `GET /api/portfolio` → `PortfolioSummary`
- [ ] **05.2 學習日誌 + AI 顧問**（1 天）
  - `GET /api/journal` → `JournalEntry[]`
  - `POST /api/chat` SSE streaming（包 `chat_agent.py`）
- [ ] **05.3 回測**（1 天）
  - `POST /api/backtest` 包 `simulate.py` `sim_engine.py`
  - 自製 `<EquityCurve>` `<MonthlyHeatmap>`
- [ ] **05.4 週報**（0.5 天）
  - `GET /api/report/weekly` 包 `weekly_report.py`
- [ ] **05.5 大盤掃描**（1 天）
  - `GET /api/scanner` 整合 `market_scan / news_agent / futures_premium`

**Done 標準**：所有左側 sidebar 項目都能點且有完整資料。

---

## 5. Milestone M5 · Dashboard 總覽（1 天）

- [ ] 02 為其他模組的整合頁，最後做
- [ ] `GET /api/dashboard` 聚合多個 service
- [ ] 自製 `<DashChart>`（投組 vs 大盤雙線）

---

## 6. Milestone M6 · 手機 + Telegram（3 天）

### M6.1 (1.5 天) — 手機版
- [ ] `/m/predict` `/m/daytrade` 路由
- [ ] 響應式：寬度 < 768 自動 router 到 mobile
- [ ] iOS PWA manifest

### M6.2 (1.5 天) — Telegram bot 整合
- [ ] 包裹既有 `telegram_bot.py` `user_confirm.py`
- [ ] 確認新流程：04.3 下單 → bot inline keyboard → 用戶按 → 寫回 DB
- [ ] 警報推送：04.1 alert level=high → Telegram

---

## 7. Milestone M7 · Polish & QA（2 天）

- [ ] 全頁 i18n（先 zh-TW only，預留 keys）
- [ ] 鍵盤導航完整測試（§5.2）
- [ ] 錯誤處理（Shioaji 斷線、API quota 用完、DB lock）
- [ ] 空狀態畫面（無持倉、無候選、收盤後）
- [ ] Loading skeleton（每個畫面）
- [ ] 13:25 強制平倉 modal 流程測試
- [ ] 模擬 → 真實切換流程測試（含 step-up auth）

---

## 8. 預估工時

| Milestone | 工作量 | 備註 |
|---|---|---|
| M0 | 0.5 天 | 準備 |
| M1 | 3 天 | 設計系統 + Login + Settings |
| M2 | 5 天 | **AI 預測** ★ |
| M3 | 6 天 | **AI 當沖** ★ |
| M4 | 4 天 | 其他工作區 |
| M5 | 1 天 | Dashboard 整合 |
| M6 | 3 天 | 手機 + Telegram |
| M7 | 2 天 | Polish |
| **合計** | **約 25 天** | 1 人開發；AI agent 輔助可壓到 12–15 天 |

---

## 9. 給 AI agent 的具體 prompt 模版

當你要請 Claude Code / Cursor 實作某個畫面時：

```
請依照 spec/ 資料夾的規格實作畫面 03.3（AI 推理過程可視化）。

參考檔：
- spec/SCREENS.md 的 §<a id="screen-03-3"></a>
- spec/DATA_SHAPES.md 的 §4
- spec/DESIGN_TOKENS.md 全部
- spec/BACKEND_MAPPING.md 的 §3.1 + §5.1
- index.html 設計畫布的 artboard "predict-reason"

要求：
1. 前端用 React + TypeScript + 既有設計 tokens
2. 後端用 FastAPI，包裹既有的 ai_stock/ai_client.py
3. 不得改動 ai_stock/* 既有邏輯，只能薄包裝
4. 視覺要對齊設計畫布
5. 完成後 npm run typecheck + pytest 都要過
```

---

## 10. 風險與緩解

| 風險 | 影響 | 緩解 |
|---|---|---|
| Shioaji simulation 與真實 API 行為不一致 | 上線後當機 | 一律先在 simulation 跑 1 週 |
| LLM 回應格式變動 | 解析失敗 | `parse_deep_response()` 加 strict JSON 驗證 + fallback |
| 13:25 ForceCloseJob 漏單 | 留倉 | 加雙重檢查：13:30 再掃一次 |
| WebSocket 斷線 | UI 不更新 | 自動重連 + 退化到 polling |
| AI 信心校準偏差 | 假信心 | 每週末用 backtest 校準閾值（→ 05.3 自動寫 settings） |

---

## 11. 驗收清單

上線前必須通過：

- [ ] 一個完整交易日 (09:00-13:35) 在 simulation 模式跑通
- [ ] Telegram 確認鏈不會卡死（60s timeout）
- [ ] 13:25 強制平倉 100% 觸發
- [ ] 所有 14 個畫面 pixel-level 對齊設計檔
- [ ] 鍵盤可導航全部主要動作
- [ ] 手機版（iPhone 15+ Safari）可正常瀏覽 AI 預測 + 當沖實況
- [ ] 模擬 / 真實切換需 step-up auth
- [ ] 全螢幕讀取速度 < 1s（除 backtest）

---

完成。對應檔案均位於 `spec/`。如需進一步說明任何畫面或模組，請指明編號。
