# AI Stock Go/No-Go Checklist
**版本：** 2026-05-09  
**適用範圍：** `main.py` 排程交易流、`telegram_bot.py` 指令控制、`app.py` 操作介面、`research_db.py` / `data/*` 交易資料  
**決策原則：** 任何 `P0` 未完成，一律 `No-Go`；任何高風險項目無明確 fallback，一律 `No-Go`。

---

## 1. 決策結論

### 目前狀態：`No-Go（地基修復完成，進入 Milestone 2）`

> 更新：2026-05-11 — P0 資料契約、重複下單、曝險風控、模式切換均已修正；
> 文件收斂完成。尚缺 3 個交易日模擬盤 dry run、緊急處置演練、台股交易日曆。

目前專案適合：
- 模擬模式驗證完整交易日流程（Milestone 2 進行中）
- 內部開發測試

目前專案不適合：
- 實盤下單
- 對外宣稱具備完整風控
- 交由非開發者獨立操作

目前專案不適合：
- 實盤下單
- 對外宣稱具備完整風控
- 交由非開發者獨立操作

---

## 2. 上線定義

本文件的「上線」分兩級：

### Level 1：模擬盤可交付
- 可連續跑完盤前、開盤、盤中監控、收盤結算
- 不會因為舊資料、重複 callback、設定分叉而產生錯誤狀態
- 文件、設定、資料表定義一致

### Level 2：實盤可放行
- Level 1 全部通過
- 關鍵交易風控具備可驗證證據
- 事故停損、撤單、停用流程可演練
- 有明確操作手冊與值班責任人

現況連 Level 1 都未達標。

---

## 3. Blockers

### P0. 交易資料契約一致性 ✅ 完成（2026-05-11）
- [x] `prior_orders` 與 `is_duplicate_order()` 使用同一資料 schema，必含 `code`、`action`、`date`
- [x] `current_positions` 與 `risk_guard.validate_plan()` 使用同一資料 schema，必含 `code`、`sector`、`value`、`lot_type`
- [x] `daily_trades` 或等價持倉來源可正確還原「目前持倉」而不是只看「今日買單」
- [x] `lot_type` 以結構化欄位保存，不可只藏在 `note`
- [x] 強制平倉、停損、一般賣出都能基於同一持倉模型運作

**放行證據**
- `docs/data_contract_audit_2026-05-11.md`（schema 定義與 migration 說明）
- `research_db.py` `_upgrade_daily_trades()`（idempotent migration）
- `tests/test_research_db_v2.py`、`tests/test_main.py`（整股/零股/當日/跨日）

### P0. 重複下單防護 ✅ 完成（2026-05-11）
- [x] 同股、同向、同交易日不得重複下單
- [x] Telegram callback 重送、排程重跑、程式重啟後，防重複規則仍成立
- [ ] API 回傳異常時，不可默默退化成「可能重複下單」（尚無 API 錯誤情境測試）

**放行證據**
- `tests/test_smoke_trading_day.py::TestMarketOpenDuplicateOrderSmoke`（兩次 09:00 觸發，僅一次下單）
- `tests/test_main.py::TestLoadPriorOrdersBlocksDuplicates`（load_prior_orders → is_duplicate_order 整合）

### P0. 持倉與曝險風控 ✅ 完成（2026-05-11）
- [x] 單一部位上限依真實持倉後的總曝險計算
- [x] 板塊集中度依真實持倉後的總曝險計算
- [x] 隔夜持倉策略明確：**不允許隔夜**，`ForceCloseJob` 於 13:25 強制全部平倉
- [x] ForceCloseJob 整股/零股依 `daily_trades.lot_type` 欄位正確選擇 order_lot

**放行證據**
- `tests/test_risk_guard.py::TestValidatePlanSingleStockWithExistingPositions`（單股總曝險）
- `tests/test_risk_guard.py::TestValidatePlanMultiDayExposure`（昨日持倉 + 今日新買）
- `tests/test_smoke_trading_day.py::TestForceCloseIntradayOddSmoke`（零股平倉）

### P0. 模擬/實盤切換安全 ⚠️ 部分完成（2026-05-11）
- [x] 只保留一個 simulation 開關：`SHIOAJI_SIMULATION`（`.env.example`、`SYSTEM.md`、`TUTORIAL.md` 已統一）
- [ ] 實盤切換前有二次確認機制，不可只靠改 `.env`（尚未實作）
- [ ] 啟動訊息、Dashboard、Telegram 通知都清楚顯示目前模式（尚未驗證）
- [x] 文件不允許同時存在互相衝突的設定名稱（已移除舊 `SIMULATION=true`）

**放行標準**
- [x] 設定文件、程式、教學文件全部使用 `SHIOAJI_SIMULATION`
- [ ] 實盤模式下，啟動前需有顯式確認步驟（仍待實作）

### P0. 緊急處置能力
- [ ] `HALT` 可阻止新排程與新下單
- [ ] 可撤銷未成交委託
- [ ] 可對已建倉部位執行緊急賣出
- [ ] 緊急停機後，重啟不會自動恢復危險動作

**放行標準**
- 有書面 Runbook
- 有至少一次模擬演練紀錄

### P1. 交易日曆正確性 ✅ 部分完成（2026-05-12）
- [x] 不能只判週末，需支援台股休市日（`tw_trading_calendar.py` + `is_trading_day()` 已更新，2026 假日清單內建）
- [ ] 盤前、盤中、收盤與強制平倉時間依台股交易時段定義
- [ ] 非交易日不得發出誤導性交易通知

**放行證據**
- `tw_trading_calendar.py`（`_TWSE_HOLIDAYS` 2026 年清單）
- `tests/test_main.py::TestIsTwseHoliday`（5 tests）
- `tests/test_main.py::TestIsTradingDay`（含 4 個假日測試）

### P1. 文件單一真相來源 ✅ 完成（2026-05-11）
- [x] `SYSTEM.md`、`TUTORIAL.md`、`docs/*` 不得互相矛盾（本次收斂）
- [x] 已失效的「已知限制」要移除或註記已修正日期（TUTORIAL.md §10）
- [x] 上線流程只能有一份主文件：**`TUTORIAL.md`** 為主運營手冊（SoT）

### P1. 測試策略升級 ✅ 完成（2026-05-11）
- [x] 補齊跨模組交易契約測試（`tests/test_main.py` 含 load_prior_orders/load_current_positions 整合）
- [x] 增加「09:00 重跑」「ForceClose 零股/整股」等情境測試
- [x] 建立最小 smoke test：`tests/test_smoke_trading_day.py`（08:30→09:00→13:25→13:35 共 4 tests，全 pass）

### P2. 運營可交接
- [ ] 有值班說明、故障排查、資料備份與回復步驟
- [ ] 有版本化變更紀錄
- [ ] 有正式上線前 dry run 計畫

---

## 4. 放行證據清單

每一項 blocker 完成後，必須提交以下證據：

- [ ] 對應 PR 或變更說明
- [ ] 測試結果截圖或命令輸出摘要
- [ ] 受影響文件更新完成
- [ ] 失敗情境與 fallback 說明
- [ ] 若影響交易邏輯，需附一段模擬盤驗證紀錄

---

## 5. 最終放行 Gate

### Go 條件
- [ ] 所有 `P0` 完成
- [ ] 至少 3 個交易日的模擬盤連續驗證通過
- [ ] 無未解的高風險資料一致性問題
- [ ] 文件、設定、程式碼一致
- [ ] PM、開發、操作人三方確認

### No-Go 條件
- [ ] 任一 `P0` 未完成
- [ ] 任一風控只能「理論上成立」但沒有測試證據
- [ ] 任一操作手冊與真實行為不一致
- [ ] 任一錯誤可能導致重複下單、錯單平倉、曝險誤判

---

## 6. 建議決策

### 立即決策
- 建議維持 `No-Go`
- 建議將專案對內重新定位為「模擬交易決策系統 Beta」
- 建議停止任何實盤導向敘述，直到 `P0` 全數關閉

### 下一個里程碑
- 先完成資料契約修正與風控閉環
- 再做文件收斂與模擬盤 dry run
- 最後才討論實盤切換
