# AI Stock 修復優先級 PRD
**文件日期：** 2026-05-09  
**文件性質：** 修復導向 PRD  
**產品階段：** 內部原型收斂期  
**目標讀者：** PM、工程、測試、實際操作者

---

## 1. Executive Summary

此專案目前具備策略分析、排程執行、Telegram 控制與 Dashboard 介面，但核心交易資料契約尚未收斂，導致風控與執行之間存在錯配。這不是功能不足，而是「交易系統地基不一致」。

本 PRD 的目標不是新增功能，而是在最短時間內把專案拉回可驗證、可操作、可交接的狀態，先達成「模擬盤可交付」，再評估是否進入實盤準備。

---

## 2. 背景與問題陳述

### 目前問題
- 重複下單防護依賴的資料欄位不完整
- 持倉、板塊曝險、強制平倉使用不同資料假設
- 整股/零股資訊未被正式保存
- 模擬/實盤模式開關命名不一致
- 文件與程式行為不完全一致
- 測試多但偏單模組，缺少交易契約整合驗證

### 商業風險
- 可能重複下單
- 可能誤判曝險
- 可能用錯 lot type 平倉
- 操作者可能因文件分叉而用錯模式
- 團隊可能誤以為系統已具備可上線等級

---

## 3. 產品目標

### 主要目標
1. 建立單一且可驗證的交易資料契約
2. 封閉風控、下單、平倉三段流程之間的資料落差
3. 建立明確的 `Go/No-Go` 放行標準
4. 將專案提升到「模擬盤可交付」水位

### 非目標
- 不新增新策略模型
- 不新增新的外部資料源
- 不優先美化 Dashboard
- 不在本階段討論擴大市場掃描能力

---

## 4. 目標使用者

### 主要使用者
- 專案維運者：需要知道系統何時能安全執行
- 開發者：需要明確知道先修什麼，驗收什麼
- PM/決策者：需要知道何時能放行，何時必須擋下

### 次要使用者
- 未來實際操作者：需要一致的文件與清楚的模式標示

---

## 5. 需求範圍

### Epic A：交易資料契約統一 ✅ 完成（2026-05-11）

#### User Story
As a developer, I want a single trade and position schema so that risk checks, execution, and close-out all use the same truth.

#### Requirements
- 定義 `order record`、`trade record`、`position snapshot`、`execution context` 欄位
- 至少統一以下欄位：`code`、`action`、`trade_date`、`quantity`、`price`、`amount`、`sector`、`position_value`、`lot_type`
- `lot_type` 必須以正式欄位保存
- `current_positions` 不可再用「今日 buy 單」近似

#### Acceptance Criteria
- 風控與平倉不再依賴 `note` 字串解析
- 任一部位都能回答「現在持有幾股、屬於哪個 lot type、價值多少」
- 有 migration 或 schema change 文件

### Epic B：重複下單與執行防呆 ✅ 完成（2026-05-11）

#### User Story
As an operator, I want the system to reject duplicate orders so that retries, callback repeats, or scheduler reruns do not create extra exposure.

#### Requirements
- `prior_orders` schema 與 duplicate guard 對齊
- 09:00 排程重跑需具 idempotency
- Telegram callback 重送不可導致重複執行
- API 查詢失敗要有明確失敗策略，不可默默放行

#### Acceptance Criteria
- 同股同向同交易日無法重複下單
- 有整合測試覆蓋重跑情境
- 系統 log 可清楚指出「因重複下單而拒絕」

### Epic C：持倉曝險與平倉閉環 ✅ 完成（2026-05-11）

#### User Story
As a PM, I want position exposure and force-close behavior to be consistent so that risk control is not only descriptive but executable.

#### Requirements
- 單股與板塊上限基於真實持倉後曝險計算
- 13:25 強制平倉支援整股與零股
- 若策略允許隔夜，需明確定義；若不允許，需完整平倉
- 監控警報與平倉動作需可追溯

#### Acceptance Criteria
- `ForceCloseJob` 在整股/零股/部分倉位案例下都正確
- 昨日持倉 + 今日新倉的風控測試通過
- 收盤後資料可反推出完整持倉變化

### Epic D：模式切換與操作安全 ⚠️ 部分完成（2026-05-11）
> `SHIOAJI_SIMULATION` 已統一；二次確認機制與模式顯示尚待實作。

#### User Story
As an operator, I want one unambiguous simulation/live switch so that I do not accidentally trade in the wrong mode.

#### Requirements
- 僅保留一個 simulation 變數
- 啟動、Telegram、Dashboard 全部顯示目前模式
- 實盤模式需有顯式二次確認
- 相關文件與 `.env.example` 同步更新

#### Acceptance Criteria
- 文件中不存在第二套模式命名
- 操作者無法在不知情情況下切到實盤

### Epic E：文件與測試收斂 ✅ 完成（2026-05-11）

#### User Story
As a maintainer, I want docs and tests to reflect real system behavior so that future changes do not reintroduce unsafe assumptions.

#### Requirements
- 建立唯一主運營文件
- 清除過期的已知限制或補註修復狀態
- 新增跨模組 smoke test
- 新增交易契約整合測試

#### Acceptance Criteria
- 文件與實作一致
- 新測試能捕捉重複下單、曝險誤判、錯誤 lot type

---

## 6. 優先級排序

### P0：本週必修
- 交易資料契約統一
- 重複下單防護修正
- 持倉/曝險/平倉閉環
- simulation/live 開關統一

### P1：P0 完成後立即處理
- 台股交易日曆與休市判斷
- 緊急處置 Runbook
- 文件單一真相來源
- 核心 smoke test 建立

### P2：可延後，但不應遺忘
- 介面與流程易用性整理
- 程式模組結構重整
- 觀測性與報表優化

---

## 7. 里程碑

### Milestone 1：地基修復 ✅ 完成（2026-05-11）
目標：關閉所有 `P0`

交付物：
- [x] `docs/data_contract_audit_2026-05-11.md`（schema 定義與 migration）
- [x] 核心交易流程修正（`main.py`、`executor.py`、`risk_guard.py`、`research_db.py`）
- [x] 關鍵整合測試（`tests/test_smoke_trading_day.py` 4/4 pass）
- [x] 模式切換說明（`SHIOAJI_SIMULATION` 統一）
- [x] 文件收斂（`SYSTEM.md`、`TUTORIAL.md`、`docs/*`）

### Milestone 2：模擬盤可交付 🔄 進行中
目標：達成 `Level 1 Go`

交付物：
- 3 個交易日 dry run 紀錄
- 文件收斂完成
- 緊急處置演練紀錄

### Milestone 3：實盤前審查
目標：決定是否進入實盤準備

交付物：
- 放行評估報告
- 風險殘留清單
- 操作責任分工

---

## 8. 風險與依賴

### 主要風險
- 既有測試會因 schema 調整大量重寫
- 資料已存在 `data/`，調整時可能出現相容性問題
- 若沒有明確 owner，文件很快再次分叉

### 外部依賴
- Shioaji API 行為與回傳格式
- Telegram callback 與操作流程
- SQLite 現有資料格式

---

## 9. 驗收標準

專案可被視為「模擬盤可交付」的條件：
- 所有 `P0` 關閉
- `Go/No-Go Checklist` 全部通過
- 連續 3 個交易日模擬盤 dry run 無重大錯誤
- 文件、設定、測試、程式邏輯一致

專案可被視為「可討論實盤準備」的條件：
- 上述條件全部達成
- 緊急停機、撤單、平倉流程完成演練
- 操作人可依文件獨立完成啟動、監控、停機

---

## 10. 建議執行順序

1. 先定義資料契約與 schema
2. 再修 `main.py`、`executor.py`、`risk_guard.py`、`research_db.py`
3. 補整合測試與 smoke test
4. 最後收斂 `SYSTEM.md`、`TUTORIAL.md`、運營文件

---

## 11. 成功指標

### 短期成功指標
- 無重複下單
- 無錯誤 lot type 平倉
- 無文件誤導模式切換

### 中期成功指標
- 模擬盤可穩定跑完完整交易日
- 任何持倉與交易紀錄都可追溯
- 新成員可依文件完成安全操作
