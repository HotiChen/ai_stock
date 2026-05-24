#!/bin/bash
# Session start: reads spec.md and primes Claude to begin development

SPEC_FILE="spec.md"
WORKFLOW_DIR=".claude"

mkdir -p "$WORKFLOW_DIR"

# Nothing to do if no spec
if [ ! -f "$SPEC_FILE" ] || [ ! -s "$SPEC_FILE" ]; then
    exit 0
fi

SPEC=$(cat "$SPEC_FILE")

echo "## 🚀 自動化開發 Workflow 已啟動

**以下是 spec.md 的開發需求：**

---
$SPEC
---

**工作規則：**
1. 根據上述需求完整實作程式碼
2. 為每個功能撰寫 pytest 測試（放在 tests/ 目錄）
3. 完成後 hook 會自動執行 code review 和測試
4. 如有 lint 錯誤或測試失敗，hook 會提供詳細回饋讓你修正
5. 所有測試通過後自動產生 REPORT.md

請現在開始實作。"
