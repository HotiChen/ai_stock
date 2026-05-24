#!/bin/bash
# Stop hook: the main workflow loop
# Exit 0 = Claude stops (done or nothing to do)
# Exit 2 = Claude sees stdout and continues working

CODE_FLAG=".claude/.code_written"
ITER_FILE=".claude/.iteration"
DONE_FLAG=".claude/.done"
MAX_ITER=5

# If workflow is marked done, let Claude stop
[ -f "$DONE_FLAG" ] && exit 0

# Only engage if code was written this turn
[ ! -f "$CODE_FLAG" ] && exit 0

# Consume the flag (recreated next time Claude edits)
rm -f "$CODE_FLAG"

# Read + increment iteration counter
ITER=$(cat "$ITER_FILE" 2>/dev/null || echo "0")
ITER=$((ITER + 1))
echo "$ITER" > "$ITER_FILE"

echo "## 🔄 Workflow 自動檢查 (第 ${ITER}/${MAX_ITER} 次)" >&2

# --- Step 1: Lint (ruff) ---
LINT_OUTPUT=$(python -m ruff check . \
    --select=E,F,W \
    --ignore=E501,E402,W291,W293 \
    --exclude=tests,docs \
    2>&1 | head -60)
LINT_CLEAN=$?

# --- Step 2: Tests (pytest) ---
TEST_OUTPUT=$(python -m pytest tests/ -x --tb=short -q 2>&1 | head -100)
TEST_EXIT=$?

# --- All clear: generate report and finish ---
if [ $LINT_CLEAN -eq 0 ] && [ $TEST_EXIT -eq 0 ]; then
    touch "$DONE_FLAG"
    bash .claude/hooks/generate_report.sh "PASSED" "$ITER"
    echo "## ✅ 所有檢查通過！開發完成。

測試結果：
\`\`\`
$TEST_OUTPUT
\`\`\`

詳細報告已生成：**REPORT.md**"
    exit 0
fi

# --- Hit iteration limit ---
if [ "$ITER" -ge "$MAX_ITER" ]; then
    touch "$DONE_FLAG"
    bash .claude/hooks/generate_report.sh "MAX_ITERATIONS_REACHED" "$ITER"
    echo "## ⚠️ 已達最大迭代次數 ($MAX_ITER)，停止自動循環。

最後一次 lint：
\`\`\`
$LINT_OUTPUT
\`\`\`

最後一次測試：
\`\`\`
$TEST_OUTPUT
\`\`\`

報告已寫入 REPORT.md。請手動處理剩餘問題。"
    exit 0
fi

# --- Build feedback for Claude to act on ---
FEEDBACK="## 🔧 第 ${ITER} 次回饋：請修正以下問題\n\n"

if [ $LINT_CLEAN -ne 0 ] && [ -n "$LINT_OUTPUT" ]; then
    FEEDBACK="${FEEDBACK}### Lint 問題 (ruff)：\n\`\`\`\n${LINT_OUTPUT}\n\`\`\`\n\n"
fi

if [ $TEST_EXIT -ne 0 ]; then
    FEEDBACK="${FEEDBACK}### 測試失敗 (pytest)：\n\`\`\`\n${TEST_OUTPUT}\n\`\`\`\n\n"
fi

FEEDBACK="${FEEDBACK}請逐一修正所有問題，修改後 workflow 將自動重新檢查。"

printf "%b" "$FEEDBACK"
exit 2
