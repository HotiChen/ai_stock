# Claude Execution Prompts
**版本：** 2026-05-09  
**用途：** 逐段餵給 Claude 的執行 prompt 手冊  
**原則：** 一次只做一件事；每次都限制修改檔案範圍；每次都要求測試與回報風險。

---

## 使用方式

1. 先照 `docs/task_breakdown_2026-05-09.md` 的順序執行
2. 一次只貼一段 prompt，不要把整份文件全貼給 Claude
3. 若 Claude 想順手改其他模組，拒絕它
4. 每次完成後，先看 diff，再跑指定測試，再進下一題

---

## Prompt 1: 先做資料契約盤點

```text
你現在是資深後端工程師，請只做「資料契約盤點」，不要直接大改功能。

目標：
1. 盤點這個專案目前的 trade record、prior order、current position、force close 所使用的欄位
2. 指出欄位不一致、缺欄位、依賴 note 字串的地方
3. 產出一份精簡的 schema 對照摘要

限制：
- 不要改動業務邏輯
- 只允許修改文件
- 如果需要引用程式，請明確指出檔案與函式

優先檢查檔案：
- research_db.py
- main.py
- executor.py
- risk_guard.py
- docs/remediation_prd_2026-05-09.md

完成後請回報：
- 現況 schema
- 目標 schema
- 最危險的 3 個不一致點
```

---

## Prompt 2: 修正 `prior_orders` 與 duplicate guard 契約

```text
請只修正「重複下單防護」的資料契約問題。

任務：
- 對齊 main.py 的 load_prior_orders() 與 executor.py 的 is_duplicate_order()
- 確保 prior_orders 至少包含 code、action、date
- 補上必要測試

只允許修改這些檔案：
- main.py
- executor.py
- tests/test_main.py
- tests/test_executor.py

禁止：
- 不要順手改 risk_guard.py
- 不要改 Telegram 流程
- 不要改文件以外的其他模組

驗收要求：
- 同股同向同交易日會被擋下
- 測試要能證明 date 欄位有被正確使用

完成後請提供：
- 改了哪些函式
- 新增或修改了哪些測試
- 執行了哪些 pytest 命令
```

---

## Prompt 3: 修正正式持倉模型

```text
請只處理「current positions 不是正式持倉模型」這個問題。

任務：
- 讓 main.py 載入的 current positions 成為正式結構
- 讓資料中至少能取得 code、sector、value、lot_type
- 不要再把今日 buy trades 直接等同目前持倉，除非你同時明確補上限制與文件

只允許修改：
- main.py
- research_db.py
- tests/test_main.py
- tests/test_research_db.py
- tests/test_research_db_v2.py

禁止：
- 不要動 app.py
- 不要動 telegram_bot.py
- 不要動 strategy 類模組

驗收要求：
- risk_guard 可以直接吃 current positions
- 測試覆蓋空持倉、單一持倉、多筆持倉

完成後請回報：
- 正式持倉 schema 長什麼樣
- 還有哪些地方仍然依賴舊格式
```

---

## Prompt 4: 修正 `lot_type` 正式保存與 `ForceCloseJob`

```text
請只修正整股/零股閉環問題。

任務：
- 讓 lot_type 成為正式保存欄位，不要只存在 note
- 修正 ForceCloseJob 與 force_stop_loss 的資料傳遞
- 確保整股與零股平倉邏輯正確

只允許修改：
- research_db.py
- main.py
- executor.py
- tests/test_main.py
- tests/test_executor.py

禁止：
- 不要改 Telegram callback
- 不要改 market scanner
- 不要處理 simulation 開關

驗收要求：
- ForceCloseJob 不可預設所有部位都是 common
- 有測試覆蓋 intraday_odd

完成後請回報：
- lot_type 現在保存在哪
- ForceCloseJob 怎麼取得 lot_type
- 跑了哪些測試
```

---

## Prompt 5: 修正 `risk_guard` 的既有曝險計算

```text
請只處理風控邏輯，不要跨去改其他流程。

任務：
- 修正 risk_guard.validate_plan() 對 current positions 的使用
- 讓單股上限與板塊上限依真實既有曝險計算
- 補測試涵蓋昨日持倉 + 今日新買 + 同板塊加碼

只允許修改：
- risk_guard.py
- tests/test_risk_guard.py
- tests/test_capital_constraint.py

禁止：
- 不要修改 main.py
- 不要修改 executor.py
- 不要新增 UI

驗收要求：
- 當既有板塊曝險已接近上限時，新單會被縮減或拒絕
- 測試清楚描述輸入與預期輸出

完成後請回報：
- 修正前後邏輯差異
- 新增哪些高風險測試
```

---

## Prompt 6: 統一 simulation/live 開關

```text
請只處理模擬/實盤模式設定分叉。

任務：
- 找出專案內所有 simulation 相關環境變數
- 統一為單一命名
- 同步修正文檔與 .env.example

只允許修改：
- .env.example
- main.py
- app.py
- telegram_bot.py
- SYSTEM.md
- TUTORIAL.md

禁止：
- 不要改交易邏輯
- 不要改資料庫 schema

驗收要求：
- 專案只保留一個 simulation 變數名稱
- 文件與程式完全一致

完成後請回報：
- 移除了哪些舊名稱
- 哪些檔案已同步更新
```

---

## Prompt 7: 補台股休市日判斷

```text
請只修正交易日曆邏輯。

任務：
- 改善 main.py 的 is_trading_day()
- 支援台股休市日，不只判斷週末
- 補測試

只允許修改：
- main.py
- 相關日期工具模組（若需新增請控制在最小範圍）
- tests/test_main.py

禁止：
- 不要順手改排程時段
- 不要修改 executor 或 risk_guard

驗收要求：
- 週末與台股休市日都不執行交易流程
- 測試明確列出日期案例
```

---

## Prompt 8: 建立最小 smoke test

```text
請只新增最小 smoke test，不要大改正式程式邏輯。

任務：
- 建立一個能驗證盤前、09:00、13:25、13:35 核心流程的 smoke test
- 使用乾淨 DB 與最小 mock
- 讓失敗訊息能指出是資料契約、下單、防重複、還是平倉哪一段壞掉

只允許修改：
- tests/ 新增 smoke test 檔案
- 若必須，小幅調整現有測試輔助函式

禁止：
- 不要重寫 main.py 大流程
- 不要為了測試去改大量產品邏輯

驗收要求：
- 測試能在本地直接跑
- 測試名稱清楚反映交易日流程
```

---

## Prompt 9: 文件收斂

```text
請只做文件收斂，不要改功能。

任務：
- 收斂 SYSTEM.md、TUTORIAL.md、docs/go_no_go_checklist_2026-05-09.md
- 移除或註記已過時的說法
- 指定哪一份是唯一主運營手冊

只允許修改：
- SYSTEM.md
- TUTORIAL.md
- docs/go_no_go_checklist_2026-05-09.md
- docs/remediation_prd_2026-05-09.md

禁止：
- 不要改 Python 程式
- 不要順便補新功能

驗收要求：
- 文件不再互相矛盾
- simulation 相關命名一致
- 13:25 force close 與隔夜策略描述一致
```

---

## Prompt 10: 每次執行完的固定收尾 prompt

```text
請不要再繼續擴大修改範圍。現在只做收尾：

1. 列出本次修改的檔案
2. 列出本次完成的驗收條件
3. 列出還沒解決的風險
4. 提供建議下一個最合理的單一任務

如果有測試，請附上實際執行的 pytest 命令。
```

---

## 最後提醒

- 不要把 `Phase 1` 和 `Phase 4` 混在一次 prompt
- 不要要求 Claude「順便幫我全部修完」
- 如果它開始碰不在允許清單內的檔案，直接中止那輪
- 每輪都要先看 diff，再決定要不要接受
