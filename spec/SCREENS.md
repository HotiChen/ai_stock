# Screens · 畫面實作規格

> 每個畫面：版面、欄位、資料來源、互動。
> 對應 `index.html` 設計畫布的 artboard。
> 校對時請開啟設計畫布、雙擊艙位放大做 pixel 比對。

---

## <a id="screen-01"></a>01 · Login 登入

**路由**：`/login`
**版型**：左右 1.05 : 1 比例。左側深底品牌氛圍區，右側 560px 表單。

### 左側（品牌氛圍）
- 深底 `--ink-bg`，6% 透明網格紋理
- 合成 K 棒裝飾條（48 根，紅綠交錯）+ 微弱 sparkline 曲線
- Header：Logo（白色）+ Shioaji 連線狀態指示燈
- 中央 Hero：
  - eyebrow：「台股 · AI 量化交易工作站」
  - H1：56px 細體，3 行；包含 `13:25` 紅字重點
  - 副標：14px / 480px 寬，2 行
- 4 欄統計條（最下方，hairline 分隔）：
  - 今日推薦 · 8 檔
  - 信心 ≥ 0.75 · 5 檔
  - 本週勝率 · 68.4%
  - 累計報酬 · +15.4%
- Footer：版權 + 風險揭露聲明

### 右側（表單）
- Padding 56px
- 表單欄位：
  1. Email（focus 狀態示意）
  2. Password（mono 字型顯示遮罩）
  3. 「忘記密碼」連結
- 2FA hint 卡片（gold 底）：「下一步將要求 6 位數驗證碼…」
- 「記住此裝置（30 天）」checkbox + SSL 標示
- Primary 按鈕：「繼續 · 兩步驟驗證」+ Enter 鍵指示
- SSO：永豐金 API、Google（2 欄）
- 底部 fineprint：服務條款 / 風險揭露聲明

### 資料 / 行為
- `POST /api/auth/login` → `LoginResponse`
- 若 `two_factor_required` → push to `/login/verify`
- 「永豐金 API」按鈕 → OAuth flow 綁定 Shioaji
- 失敗：表單下方紅色 inline error，不要彈 toast

### 視覺要點
- 不要陰影
- K 棒裝飾條 opacity 0.6，sparkline opacity 0.18
- Hero 標題使用 light weight (300)

---

## <a id="screen-02"></a>02 · Dashboard 總覽

**路由**：`/`
**結構**：`AppChrome` 包裹，內部 `KPI strip` + `主圖區` + `側邊區 (Top 5 + 警報)`。

### KPI Strip（5 欄）
- 欄 1（寬 1.4）：今日損益 — 大數字 `+12,340`（紅）+ pill `+0.74%` + sparkline
- 欄 2：可用資金 `NT$ 363,600` + 「30.3% 現金部位」
- 欄 3：今日已執行 `8 筆` + 「買 6 / 賣 2」
- 欄 4：AI 信心均值 `0.74` + 「≥ 0.75 共 4 檔」
- 欄 5：距強制平倉 `02:42:42` + ARMED 標記（紅閃）

### 主圖（佔 2 欄 × 2 行）
- 標題：「大盤 · 加權指數 vs 投組淨值」
- 時段切換 pills：1D / 5D / 1M / 3M / YTD / 1Y
- 雙線：投組淨值（紅實線）+ 加權指數（灰虛線）
- AI buy / TP markers（B / TP 方塊在線上）
- 當前價虛線 + 右側價格小標
- Legend 左上：兩條線各自的數值

### Top 5 推薦清單（側邊）
- 6 row（排名 # + code + name + sector + sparkline + price + change% + confidence tick）
- 點 row → 跳至 `/predict/:code`

### 警報串流（側邊）
- LIVE 標記
- 6 條 row：時間 / level 色條 / code+name / 文字 / 已處理 pill
- 點 row → 跳至相關持倉

### 資料
- `GET /api/dashboard` → `{ kpi, chart_data, top_picks, recent_alerts }`
- WS push：每分鐘更新 chart_data 末端、實時 push alerts

---

## <a id="screen-03-1"></a>03.1 · AI 預測 · Top N ★

**路由**：`/predict`
**API**：`GET /api/predict/today`

### 上方 sub-tabs
今日推薦 / 深度分析 / 推理過程 / 多模型比較 / 預測 vs 實際

### Run banner
- RUN-ID + 6 個 stat（掃描 / 深度 / buy / hold / 風控過 / Telegram）
- 右側：「重新跑分析」+「全部批准 (6)」按鈕

### 主表格（11 欄 grid）
| 欄 | 寬比 | 內容 |
|---|---|---|
| # | 40px | 排名 (01, 02…) |
| 標的 | 1.2 | code + name + sparkline |
| 板塊 | 0.8 | sector text |
| 訊號 | 0.6 | buy / hold pill |
| AI 信心 | 1 | ConfidenceBar 10 段 |
| 現價 / 漲幅 | 1.1 | price + change% |
| 目標 / 停損 | 1.1 | TP / SL 雙行 |
| 進場理由 | 1.4 | AI 一句話 |
| 技術訊號 | 1.6 | tags pills |
| 建議部位 | 0.9 | NT$ + % |
| 狀態 | 0.8 | 已批准 / 待確認 / 觀望 + Telegram 標記 |

- 偶數列用 surface-2 背景
- hold 訊號的 row opacity 0.55

### 底部 3 卡（grid 1fr × 3）
1. **風控紀要**：6 項 check（✓ / !）+ 細節
2. **板塊集中度**：水平 bar，超過 limit 變紅
3. **預期結果分佈**：直方圖（−5% ~ +7%），中位數標出

### 互動
- 點 row → 跳至 `/predict/:code`
- 點 code → 跳至 `/predict/:code`
- 點 ✅ 已批准 pill → 顯示 batch action menu
- 點「重新跑分析」→ 顯示 confirm modal，預期耗時 18s

---

## <a id="screen-03-2"></a>03.2 · AI 預測 · 個股深度

**路由**：`/predict/:code`
**API**：`GET /api/predict/:code`

### 頂部 identity strip（雙欄）
- 左 280px：code + name + sector + 大價格 + OHLV
- 右 (flex)：
  - eyebrow「AI 判決」
  - BUY 大 pill + 信心數字 0.86
  - TP / SL / R/R / Budget 四個小格
  - 兩個 action button：「批准 · 加入今日清單」「跳過」

### 主體（3 欄）
1. **價格圖 (1.4 比例)**：20 日走勢 + TP/SL zones（紅綠 5% 透明）+ MA20 金線 + 標籤
   - 右側 140px 副欄：預期報酬 / 最大下行 / 建議部位 / 進場方式（分批）
2. **技術指標 (1 比例)**：9 項 row（key / value / hint / weight pill）
   - 底部「總分」+15 + 推薦「強力買進」
3. **情境分析 + AI 結論 (1 比例)**：
   - 3 情境（基準 / 樂觀 / 保守）+ 機率 bar + 描述
   - AI 結論卡：多行繁中分析 + 來源 model + 時間戳

### 互動
- 點「批准」→ confirm modal → POST `/api/predict/approve`
- 點「跳過」→ `/api/predict/reject`
- 點「檢視規則」→ 跳至 `/settings#rules` 或 popover

---

## <a id="screen-03-3"></a>03.3 · AI 預測 · 推理過程 ★

**路由**：`/predict/:code/reasoning`
**API**：`GET /api/predict/:code/reasoning`

### 三欄佈局

#### 左欄：推理流水線（垂直 timeline）
9 步 TraceStep：INPUT → FETCH → FETCH → EVAL → PROMPT → LLM → PARSE → GUARD → OUTPUT
- 每步：方框序號 + Phase pill + label + body code block + 耗時
- LLM 步驟用紅色強調 + API CALL pill
- 底部「成本明細」卡：tokens / 時間 / 費用

#### 中欄：Prompt + Response
- SYSTEM 區塊（淺底）：系統指令
- USER · INDICATORS 區塊：technical indicators payload（外部資料用 `<external_data>` 隔離）
- ASSISTANT · streaming 區塊（左紅邊框）：解析後的 JSON，含閃爍游標
- 字型全 mono，11px

#### 右欄：判決構成
- contributions 列表（8 項）：對信心 0.86 的影響量
  - 每項：key + detail + 中心對稱 bar（正左 / 負右）+ delta 數字
  - 底部「最終信心」加總
- 模型自檢 4 條 Q/A 卡
- 底部黑底「DECISION TRACE」卡：完整 audit 資訊 + hash

### 互動
- 點任一 Trace step → 展開該步的完整 body（modal）
- 點「decision hash」→ 複製到剪貼簿

---

## <a id="screen-04-1"></a>04.1 · AI 當沖 · 駕駛艙 ★

**路由**：`/daytrade`
**API**：`GET /api/daytrade/live` + `WS /ws/daytrade`

### Sub-tabs
駕駛艙 / K 線標記 / 下單流程 / 已執行 / 策略執行緒 / 風控

### 倒數 Bar（黑底）
- LIVE 燈 + FORCE-CLOSE COUNTDOWN
- `09:00 開盤` ──── progress bar（綠→金漸層）── `13:25 強制平倉`
- 大數字：`02:42:42` 剩餘
- 右側 3 小格：待平倉 5 檔 / ForceClose 預估 / 觸發停損 0

### 主 4 宮格（2 × 2）

#### TL: 當前持倉表（9 欄）
標的 / 單位（整張/零股 pill）/ 進場 / 現價 / 數量 / 市值 / 損益（雙行）/ 停利停損距離（雙頭 bar）/ 狀態

距離 bar 設計：
```
SL ───┬─────────────────┬─── TP
       ↑ 進場            ↑ 現價（紅/綠箭頭）
[淡綠 fill]              [淡紅 fill]
```

#### TR: 警報串流（LIVE）
- 6 條，每條：左 3px 色條 + 時間 + level pill + code + name + NEW 標記 + 文字 + 來源 + Telegram 狀態

#### BL: 策略執行緒（7 thread）
表格：code / 狀態 pill / last tick / uptime / 距離 / polls / alerts

#### BR: 風控儀表
- 資金使用率 bar
- 板塊集中度（5 條 bar）
- 日內最大虧損 bar（負值往左延伸，碰上限變紅）
- 黑名單 pills 列

### 互動
- 點任一持倉 → 跳至 `/daytrade/:code/chart`
- 點警報 row → 跳至相關持倉 + 開啟 action menu
- 點「全部平倉」→ confirm modal → batch close

---

## <a id="screen-04-2"></a>04.2 · K 線 + AI 標記 ★

**路由**：`/daytrade/:code/chart`
**API**：`GET /api/daytrade/:code/chart` + `WS /ws/daytrade/:code/chart`

### Identity strip（雙欄）
- 左 240px：code + name + 大價格 + OHLV
- 右：8 個 pill（監控中時長 / AI 信心 / 距 TP / 距 SL）+ 3 個 button（調整停利 / 調整停損 / 立即平倉）

### 主體（左圖右側欄）

#### K 線圖（高 360px）
- Candles：紅漲（filled）/ 綠跌（filled）
- MA20 金線
- TP / SL 紅綠虛線 + 標籤
- 當前價虛線 + 右側價格小方塊（紅底白字）
- AI markers：方塊 + 文字（BUY / TP / ADD / WARN / NOTE）+ 引線
- 上方圖例 + 下方時間軸 + 右側價格軸

#### RSI 子圖（高 100px）
- 70 / 30 紅綠虛線
- 50 中線
- RSI 曲線（深灰）

#### 側欄 320px
- AI 進出場標記 log（5 個 mark row）
  - 左 3px 色條 + icon 方塊 + label + 時間 + 內容 + conf
- 底部「建議下一步」卡（紅左邊 + 強調）
  - 建議 + AI 信心 + 套用 / 忽略 buttons

### 互動
- 點 mark → 在圖上 highlight 該位置 + scroll-to
- 點「調整停利/停損」→ inline editor
- 點「套用」AI 建議 → POST `/api/daytrade/adjust-tp` 或 `-sl`

---

## <a id="screen-04-3"></a>04.3 · 下單流程 + 風控 ★

**路由**：`/daytrade/order?code=2330`（或從 03.x 批准跳轉）
**API**：`POST /api/order/preview` 取得 `OrderTicket`

### 3 欄佈局

#### 左欄：下單委託
1. eyebrow「下單委託」+ 右上「AI 預填」
2. 標的卡片：code + name + 現價
3. 動作 toggle：買進 BUY（紅 active）/ 賣出 SELL
4. 委託類型 radio cards：整張 (Common) / 零股 (IntradayOdd)
5. 價格類別 + 限價 雙欄
6. 停利 / 停損 雙 tile
7. AI 來源 gold 卡：信心 0.86 + 理由

#### 中欄：風控檢查 + dry-run code
1. eyebrow「風控檢查 · 即時」+ 「5/6 通過」pill
2. 6 行 GuardRow（編號 / icon / label / sub / detail）
3. 下單後狀態預估（6 step grid）：送出 / Shioaji 接單 / 撮合成交 / 寫入 DB / 啟動 Monitor / Telegram
4. 黑底 code 區塊：`api.place_order(...)` dry-run preview

#### 右欄：Telegram 鏡像 + Actions
1. 「Telegram 鏡像」eyebrow + chat_id
2. 模擬 Telegram chat（背景 #e6dfd1）：
   - bot 訊息：AI 盤前選股
   - 用戶回覆「✅ 批准」
   - bot 訊息：已成交
   - bot 訊息：接近停損（有 glow + warn 邊框）
3. 底部 sticky：「送出委託 · 113,500」+ ⌘ ENTER hint
4. 兩個 secondary buttons：儲存草稿 / 批准全部
5. fineprint：「▴ 確認前將二次寄送 Telegram · 模擬模式不會動用真實資金」

### 互動
- 動作切換 (Buy/Sell) → 重新計算所有風控
- 價格 / 數量編輯 → 即時更新 amount
- 「送出委託」→ Telegram 二次確認 modal → `POST /api/order/submit`
- 「批准全部」→ batch flow 到下一筆委託

---

## <a id="screen-05-1"></a>05.1 · 持倉 / 投資組合

**路由**：`/portfolio`

### 上方 6 欄 summary strip
總淨值 / 可用資金 / 未實現 / 已實現 / 持倉數 / 距強制平倉

### 主體（左 1.6 / 右 1）
- **左**：持倉表格（同 04.1 TL）+ 底部 Treemap（板塊配置橫條，1 row）
- **右**：
  - 板塊配置（donut + legend）
  - 近 14 日損益（紅綠對稱 bar）
  - 本月累計 · vs 大盤（α 數字 + 雙線圖）

---

## <a id="screen-05-2"></a>05.2 · 學習日誌 + AI 顧問

**路由**：`/journal`

### 上方 summary strip
累計交易 / 勝率 / 學習進度 (rules.py v47) / 本月學習摘要（多行）+ 匯出 CSV

### 主體（左 1.6 / 右 1）
- **左**：6 條 JournalEntry 卡
  - 日期 + code + name + pnl（右上紅/綠）
  - 學習內容（左邊條色標 = 紅/綠）
  - 底部 pills：成功/檢討 + 已寫入 rules.py + 「檢視交易詳情 →」
- **右**：AI 顧問對話
  - eyebrow + LIVE 標記
  - 4 條 ChatMessage（user / ai 交替）
  - AI 訊息含 SONNET 標記
  - 底部輸入框 + ⌘ K hint

---

## <a id="screen-05-3"></a>05.3 · 回測

**路由**：`/simulate`
**API**：`POST /api/backtest` → `GET /api/backtest/:id`

### 上方參數列
RUN-ID + 區間 + 策略 pills + 初始 + 滑價 + 修改參數 / 重新執行 buttons

### 7 欄 KPI strip
累計報酬 / vs 大盤 / 勝率 / Sharpe / 最大回撤 / 平均盈 / 平均虧

### 主體（左 1.6 / 右 1）
- **左**：
  - 權益曲線圖（雙線：策略 vs 大盤，紅實 + 灰虛）
  - 月度報酬熱力圖（12 欄 × 2 行，紅/綠色階）
- **右**：
  - 交易明細表（12 筆 sample）
  - 黑底 AI 結論卡：建議調整參數

---

## <a id="screen-05-4"></a>05.4 · 週報

**路由**：`/report`

### 全頁 vertical 佈局

#### 1. Hero（左 2 / 右 1）
- 左白：「Week 21 · 2026」+ 36px 敘事（含紅字 +1.54% / 68.4%）+ AI 一段話
- 右黑：本週淨損益 +18,420（紅大字）+ 4 小格 KPI

#### 2. 每日明細（5 欄）
- 各日：日期 + 損益（左 3px 色條：紅/綠）+ 筆數 + 勝率 bar

#### 3. 三欄分析
- 信心分層勝率（4 行 bar）
- 板塊勝率（5 行）
- 下週調整建議（4 條 numbered list）

---

## <a id="screen-05-5"></a>05.5 · 大盤掃描

**路由**：`/scanner`

### 上方指數 strip（6 欄）
加權 / OTC / TSMC ADR / USD/TWD / VIX / 台指期

### 主體（左 1.4 / 右 1）
- **左**：板塊熱力表（8 行 grid）
  - 板塊名 / 檔數 / 漲跌% / 廣度雙色 bar / 領漲股
- **右**：
  - AI 訊號掃描（4 卡）：code / name / signal / confidence
  - 今日重要新聞（3 條）：來源 + 時間 + headline

---

## <a id="screen-06"></a>06 · Settings

**路由**：`/settings`

### 左 sub-nav（220px）
資金與風控 / API 金鑰 / AI 模型 / 模擬·真實 / 通知偏好 / 黑名單 / 主題 / 匯出 / 關於

### 右主體
#### 「資金與風控」分頁
1. 總預算 + 單筆上限 + 日內最大虧損（Stepper）
2. 單一部位 / 板塊集中度 / 入場信心門檻 / 預設停損（Slider）
3. AI 模型分配（3 個 task：盤前 / 策略 / 對話）
4. 執行模式（兩張並排 mode card：模擬 = 當前 / 真實）
5. 通知偏好（5 channel row：Telegram / Email / 桌面 / iOS / Slack）
6. 底部黑底 `.env` preview（多行 mono）

### 互動
- 切換真實模式：跳大 modal，需輸入 email 全文確認
- 儲存：顯示「將在下一個 PremarketJob 生效」
- API 金鑰：永遠 masked，編輯時需 step-up auth

---

## <a id="screen-07-1"></a>07.1 · 手機 · AI 預測卡

**裝置**：iPhone 17 (402 × 874)
**路由**：`/m/predict`

- Nav row：AI · PREMARKET + 推送時間 pill
- 大標題：「今日推薦」+ 日期/檔數
- 3 欄迷你 KPI tiles
- 5 張 MobilePickCard：
  - rank + code + name + sector pill
  - 大價格 + change%
  - confidence 大字
  - 理由卡
  - TP / SL / sparkline / 狀態 pill
- 底部固定 black bar：距 09:00 開盤倒數

---

## <a id="screen-07-2"></a>07.2 · 手機 · 當沖實況

- 頂部黑色 banner：FORCE-CLOSE 倒數
- PnL hero：大紅字 +12,340
- 迷你淨值 sparkline 圖卡（高 90px）
- 持倉清單（5 卡）

---

## <a id="screen-07-3"></a>07.3 · Telegram 批准流程

- Telegram 官方藍色 header
- 米色聊天背景
- Bot 訊息（白卡）+ user 訊息（綠卡）交替
- 重要訊息含按鈕（✅/❌/✏️）
- 警示訊息有紅框 + 陰影

---

## 通用元件清單

請建立這些可重用元件：

| 元件 | 在哪些畫面用 |
|---|---|
| `<AppChrome>` | 02–06 所有桌面 |
| `<Eyebrow>` | 所有 |
| `<Pill>` | 所有 |
| `<ConfidenceTick>` | 02 |
| `<ConfidenceBar>` | 03.1, 03.2 |
| `<Sparkline>` | 02, 03.1, 07.1 |
| `<Spark>` (filled) | 02, 05.1 |
| `<DistRangeBar>` | 04.1, 05.1 |
| `<KChart>` | 04.2 |
| `<AlertRow>` | 02, 04.1 |
| `<ThreadRow>` | 04.1 |
| `<TraceStep>` | 03.3 |
| `<ContribRow>` | 03.3 |
| `<GuardRow>` | 04.3 |
| `<TelegramMsg>` | 04.3, 07.3 |
| `<Kpi>` | 02, 05.1, 05.3, 05.4 |
| `<CountdownBar>` | 04.1, 07.2 |
| `<RiskCockpit>` | 04.1 |
| `<EquityCurve>` | 05.3 |
| `<MonthlyHeatmap>` | 05.3 |

所有元件的 styling 必須來自 `DESIGN_TOKENS.md`。
