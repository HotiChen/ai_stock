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
