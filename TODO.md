# AI Stock 待辦清單 (TODO)

## 🛠️ 系統優化與修復 (基於 Antigravity 4/29 Code Review)

### 1. 效能優化 (Performance)
- [ ] **Shioaji 報價優化**：在 `app.py` 中，將題材清單的報價獲取改為「批次抓取 (Batch Fetch)」，避免在迴圈中逐一呼叫 `api.snapshots`。
- [ ] **除權息資料快取**：在 `risk_guard.py` 中，實作 `check_ex_dividend` 的每日快取機制。目前每檢查一支股票就會重新下載一次證交所清單，效率較低。

### 2. 風險控管強化 (Logic & Risk)
- [ ] **持倉上限檢查**：在 `risk_guard.py` 驗證計畫時，除了檢查單一部位上限，還應將「既有持倉價值 + 新買入預算」加總後再比對 `MAX_POSITION_RATIO`。
- [ ] **報價即時性調整**：評估將 Dashboard 的報價緩存 (TTL) 從 300 秒縮短至 60 秒，或在執行下單前強制更新報價，以確保損益顯示精準。

### 3. 安全與穩定性 (Security & Stability)
- [x] **環境隔離**：檢查並確保 `.env` 與 `data/*.db` 已加入 `.gitignore`（已確認：均已加入）。
- [ ] **API 異常處理**：在 `risk_guard.py` 中，若 TWSE OpenAPI 連線失敗，除了回傳 False 外，應增加 Log Warning 提醒使用者風險過濾暫時失效。

---

## 🚀 未來功能擴充 (Backlog)
- [ ] **雙首長制 (Ensemble) 實驗**：實作 Gemini 負責財報/新聞分析，Claude 負責最後決策的整合邏輯。
- [ ] **歷史回測系統整合**：將 `simulate.py` 的結果與真實帳務進行更直觀的對比看板。
- [ ] **Telegram 互動式停損設定**：優化 `停損 CODE PRICE` 的解析與資料庫同步邏輯。

## 2026-06-10 研究迴圈（見 docs/research_loop_design.md）
- [x] 件1 來源可追溯：stock_prediction_log 加 reason/factors_json/news_refs/youtube_refs
- [x] 件2 敘事訊號邊界化：deep_analyzer prompt 三條規則
- [x] Playbook 迴圈：research_playbook.md + playbook_updater.py + 13:50 排程
- [ ] 件3 對照組命中率報表（等件1 資料累積兩週後做）
- [ ] adaptive_scorer per-signal 權重自調（等件3 跑滿一個月再評估）

## 2026-06-13 退場機制缺口（程式碼調查發現，見下表）
- [ ] 🔴 波段自動賣出寫入 daily_trades：AlertWorker auto_execute 觸發 force_stop_loss 後須 save_daily_trade（否則 PostMarketJob 算不出損益）
- [ ] 🔴 波段 trailing stop peak_price 持久化（目前只在 MonitorAgent 記憶體，main.py 重啟即歸零→誤觸發）
- [ ] daily_trades 加 exit_reason 結構化欄位（stop_loss/take_profit/trailing_stop/force_close）
- [ ] 跨日持倉追蹤（positions table；load_current_positions 只讀當日）
- [ ] 統一波段/當沖兩套退場邏輯可靠度

## 2026-06-13 Humbled Trader 移植（評估後保留項，見對話）
### 第一波（高優先，依賴少）
- [ ] 一句話新聞催化劑 catalyst_sentence：每檔附 call_haiku 生成的一句新聞摘要（基礎已具：news_refs 來源追溯）
- [ ] 試撮資料接入 + 跳空>5%/股價門檻條件 + 帶日期 Top10 JSON 快照 + 08:35 盤前掃描推播
### 第二波（依賴第一波試撮資料）
- [ ] 五條件趨勢突破策略 thread（開盤30分後/現價>昨高/昨收>200SMA/現價>試撮高/突破當日新高；注意台股10%漲跌幅對跳空條件影響）
### 不採用
- TradingView MCP（依賴 macOS 桌面版，無法無人值守，與 cron 自動化衝突）
- 09:30 起每30分鐘輪詢（現有 MonitorAgent tick 級即時監控已更優，不倒退）
### 回測紀律（移植時必守，見 docs/backtest_discipline.md）
- [ ] 回測≥2年（涵蓋完整多空週期）；單標的樣本<30筆不採用；計入手續費/證交稅/滑價；新訊號 paper trading≥1季再實盤
