# AI Stock Project Task Tracker
**版本：** 2026-05-09  
**用途：** 給工程師與 Claude 逐項執行的修復清單  
**來源依據：** `docs/go_no_go_checklist_2026-05-09.md`、`docs/remediation_prd_2026-05-09.md`

---

## 使用規則
- 每次只做一個 Task，不要跨 Phase 混改
- 先改資料契約，再改交易流程，再補測試，最後收文件
- 每個 Task 完成前，必須附對應測試結果
- 若任務會改 schema，必須同步更新文件與測試
- 未完成 `Phase 1` 前，不准討論實盤放行

---

# Phase 1: 交易資料契約與資料庫地基 (負責人: `/backend-dev`, `/dba-expert`, `/test-engineer`)

## 目標
讓 `main.py`、`executor.py`、`risk_guard.py`、`research_db.py` 對「交易、持倉、lot type、曝險」有同一套定義。

## Pre-conditions
- 已閱讀 `docs/remediation_prd_2026-05-09.md`
- 知道目前 `daily_trades` 無結構化 `lot_type`
- 知道目前 `current_positions` 不是正式持倉模型

## Post-conditions
- 可以從正式資料結構還原目前持倉
- 強制平倉與風控使用同一套欄位
- 不再依賴 `note` 字串保存關鍵交易資訊

- [ ] `Task 1.1`: 盤點現行交易資料欄位與缺口，產出 schema 對照表  
  Owner: `/backend-dev`  
  Files: `research_db.py`, `main.py`, `executor.py`, `risk_guard.py`, `docs/remediation_prd_2026-05-09.md`  
  AC: 文件列出現況 schema、目標 schema、缺漏欄位。  
  AC: 明確定義 `trade record`、`position snapshot`、`prior order record`。

- [ ] `Task 1.2`: 為 `daily_trades` 增加正式欄位或等價資料結構，至少包含 `lot_type`、`sector`、`position_value`  
  Owner: `/dba-expert`  
  Files: `research_db.py`, `tests/test_research_db.py`, `tests/test_research_db_v2.py`  
  AC: 資料庫層可讀寫新欄位。  
  AC: 測試可驗證資料不再只依賴 `note`。

- [ ] `Task 1.3`: 新增或整理「正式持倉載入函式」，不可再把「今日 buy 單」直接當持倉  
  Owner: `/backend-dev`  
  Files: `main.py`, `research_db.py`, `tests/test_main.py`  
  AC: 可從正式資料模型得到目前持倉。  
  AC: 測試覆蓋無持倉、單一持倉、多筆持倉情境。

- [ ] `Task 1.4`: 定義 `prior_orders` 正式 schema，與 duplicate guard 使用同一契約  
  Owner: `/backend-dev`  
  Files: `main.py`, `executor.py`, `tests/test_main.py`, `tests/test_executor.py`  
  AC: `prior_orders` 至少含 `code`、`action`、`date`。  
  AC: `is_duplicate_order()` 不再因欄位缺漏失效。

- [ ] `Task 1.5`: 更新資料契約文件，標註所有交易核心欄位來源  
  Owner: `/tech-writer`  
  Files: `SYSTEM.md`, `TUTORIAL.md`, `docs/go_no_go_checklist_2026-05-09.md`  
  AC: 文件與實作使用同一欄位名稱。  
  AC: 不再有互相矛盾的持倉描述。

---

# Phase 2: 重複下單、防呆與 09:00 執行安全 (負責人: `/backend-dev`, `/test-engineer`)

## 目標
保證排程重跑、callback 重送、API 重試時，不會產生額外曝險。

## Pre-conditions
- `Phase 1` 已完成
- `prior_orders` schema 已固定

## Post-conditions
- 同股同向同日只能成功一次
- 09:00 job 可重跑但不重複下單
- 失敗 log 能說明為何被擋

- [ ] `Task 2.1`: 修正 `load_prior_orders()` 與 `executor.is_duplicate_order()` 契約  
  Owner: `/backend-dev`  
  Files: `main.py`, `executor.py`, `tests/test_main.py`, `tests/test_executor.py`  
  AC: 當日既有委託可正確被 duplicate guard 擋下。  
  AC: 回歸測試可驗證含 `date` 的正確判斷。

- [ ] `Task 2.2`: 為 `MarketOpenJob.run()` 增加 idempotency 驗證  
  Owner: `/backend-dev`  
  Files: `main.py`, `tests/test_main.py`  
  AC: 模擬兩次 09:00 執行，第二次不產生新買單。  
  AC: 被擋下的原因可在 log 或 result 中辨識。

- [ ] `Task 2.3`: 加入 Telegram callback 重送防呆需求的測試骨架  
  Owner: `/test-engineer`  
  Files: `tests/test_telegram_bot.py`, `tests/test_user_confirm.py`  
  AC: 測試能模擬重複 callback。  
  AC: 不因重送造成重複動作。

- [ ] `Task 2.4`: 定義 API 查詢失敗時的明確策略  
  Owner: `/backend-dev`  
  Files: `main.py`, `executor.py`, `risk_guard.py`, `SYSTEM.md`  
  AC: 文件與程式明確定義「查不到 prior orders 時是否拒絕下單」。  
  AC: 不允許默默退化成危險行為。

---

# Phase 3: 持倉曝險、強制平倉與 lot type 閉環 (負責人: `/backend-dev`, `/dba-expert`, `/test-engineer`)

## 目標
讓風控、持倉、13:25 強制平倉使用一致邏輯，避免錯單或錯誤曝險。

## Pre-conditions
- `Phase 1` 完成
- `lot_type` 已成為正式欄位

## Post-conditions
- `ForceCloseJob` 能正確處理整股與零股
- 板塊上限依真實持倉後的曝險計算
- 是否允許隔夜有正式定義

- [ ] `Task 3.1`: 修正 `load_current_positions()` 輸出格式，補齊 `sector`、`value`、`lot_type`  
  Owner: `/backend-dev`  
  Files: `main.py`, `research_db.py`, `tests/test_main.py`  
  AC: `risk_guard.validate_plan()` 可直接使用輸出結果。  
  AC: 不需靠猜測或預設值補欄位。

- [ ] `Task 3.2`: 修正 `risk_guard.validate_plan()` 對既有曝險的計算  
  Owner: `/backend-dev`  
  Files: `risk_guard.py`, `tests/test_risk_guard.py`, `tests/test_capital_constraint.py`  
  AC: 可正確計算單股與板塊上限。  
  AC: 覆蓋昨日持倉 + 今日加碼情境。

- [ ] `Task 3.3`: 修正 `ForceCloseJob` 使用正式 `lot_type` 而非預設 `common`  
  Owner: `/backend-dev`  
  Files: `main.py`, `executor.py`, `tests/test_main.py`, `tests/test_executor.py`  
  AC: 整股與零股平倉都正確。  
  AC: 測試能證明不會用錯 lot type。

- [ ] `Task 3.4`: 明確定義隔夜持倉策略，二選一：允許隔夜或 13:25 全平  
  Owner: `/product-manager`, `/backend-dev`  
  Files: `SYSTEM.md`, `TUTORIAL.md`, `main.py`, `docs/go_no_go_checklist_2026-05-09.md`  
  AC: 文件與排程一致。  
  AC: 操作者能從文件直接知道預設行為。

---

# Phase 4: 模擬/實盤切換與交易日曆安全 (負責人: `/backend-dev`, `/tech-writer`, `/test-engineer`)

## 目標
避免因設定分叉、交易日判斷錯誤或模式誤判造成操作事故。

## Pre-conditions
- 核心交易流程已穩定

## Post-conditions
- simulation/live 只有一個真相來源
- 非交易日不會跑錯排程
- 實盤切換具備顯式保護

- [ ] `Task 4.1`: 統一 simulation 變數命名，移除重複開關  
  Owner: `/backend-dev`  
  Files: `.env.example`, `main.py`, `app.py`, `telegram_bot.py`, `SYSTEM.md`, `TUTORIAL.md`  
  AC: 全專案只保留一個 simulation 設定名。  
  AC: 文件與程式一致。

- [ ] `Task 4.2`: 在啟動訊息、Dashboard、Telegram 顯示明確模式標識  
  Owner: `/frontend-dev`, `/backend-dev`  
  Files: `app.py`, `telegram_bot.py`, `notifier.py`, `main.py`  
  AC: 任一入口都能看出目前是模擬還是實盤。  
  AC: 不依賴使用者記憶 `.env` 內容。

- [ ] `Task 4.3`: 補台股休市日判斷  
  Owner: `/backend-dev`  
  Files: `main.py`, 相關日期工具模組, `tests/test_main.py`  
  AC: 國定假日不執行交易排程。  
  AC: 測試覆蓋週末與休市日。

- [ ] `Task 4.4`: 定義實盤切換前的二次確認流程  
  Owner: `/product-manager`, `/tech-writer`  
  Files: `TUTORIAL.md`, `SYSTEM.md`, `docs/go_no_go_checklist_2026-05-09.md`  
  AC: 文件有明確切換步驟與責任人。  
  AC: 不能只寫「把 `.env` 改成 false」。

---

# Phase 5: 文件收斂、Smoke Test 與操作交接 (負責人: `/tech-writer`, `/test-engineer`, `/support-agent`)

## 目標
讓這個專案不是只有作者自己看得懂，而是可被他人安全接手。

## Pre-conditions
- `Phase 1` 到 `Phase 4` 已完成主要修復

## Post-conditions
- 有唯一主運營文件
- 有最小 smoke test
- 有值班與異常處理文件

- [ ] `Task 5.1`: 收斂主文件，標記哪份是唯一上線運營手冊  
  Owner: `/tech-writer`  
  Files: `SYSTEM.md`, `TUTORIAL.md`, `docs/*`  
  AC: 主文件唯一且內容不互斥。  
  AC: 已過時敘述移除或標註修正狀態。

- [ ] `Task 5.2`: 建立最小核心 smoke test  
  Owner: `/test-engineer`  
  Files: `tests/` 新增 smoke test 檔案  
  AC: 可在乾淨 DB 下驗證盤前、09:00、13:25、13:35 核心流程。  
  AC: 測試失敗時能快速指出是哪一段契約壞掉。

- [ ] `Task 5.3`: 建立 Runbook：停機、撤單、緊急賣出、恢復流程  
  Owner: `/support-agent`, `/tech-writer`  
  Files: `docs/` 新增 runbook 文件  
  AC: 非作者也能依文件操作。  
  AC: 每個步驟有前置條件與預期結果。

- [ ] `Task 5.4`: 建立 3 個交易日 dry run 驗證清單  
  Owner: `/product-manager`, `/test-engineer`  
  Files: `docs/go_no_go_checklist_2026-05-09.md`, `docs/` 新增 dry run 記錄模板  
  AC: 每日可記錄成功/失敗、異常、處置、結論。  
  AC: 可作為最終 Go/No-Go 決策依據。

---

# 建議執行順序

1. `Task 1.1` → `Task 1.4`
2. `Task 2.1` → `Task 2.4`
3. `Task 3.1` → `Task 3.4`
4. `Task 4.1` → `Task 4.4`
5. `Task 5.1` → `Task 5.4`

---

# 不可犯的錯

- 不要一次改 `main.py`、`executor.py`、`risk_guard.py`、`telegram_bot.py` 再一起測
- 不要在 schema 還沒定案前先補 UI
- 不要讓 Claude 一次自由發揮跨兩個 Phase
- 不要在未完成 smoke test 前宣稱可實盤
