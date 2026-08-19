"""post_market.sh 載入 .env 不可被行尾註解打死。

2026-08-19 launchctl 狀態
-------------------------
    com.aistock.post_market   exit=1

/tmp/aistock_post_market.log 裡 98 行全是同一種錯：

    post_market.sh: line 23: export: `模型名稱': not a valid identifier
    post_market.sh: line 23: export: `逾時（秒）': not a valid identifier

第 23 行是

    export $(grep -v '^#' .env | xargs)

`grep -v '^#'` 只濾掉整行註解。專案的 .env 有 18 行是 `KEY=value  # 中文說明`
這種行尾註解，那些中文被 xargs 拆成一個個「詞」，export 拿到就爆。

腳本第 16 行是 set -euo pipefail，所以它就死在這裡——後面的
adaptive_scorer、dt_paper_trade、notion_reporter 三個盤後任務一個都沒跑到。

start_all.sh 早就有正確寫法（多兩層過濾），post_market.sh 沒跟上。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

_ENV_WITH_COMMENTS = """\
# 整行註解
AI_MODEL=haiku          # 模型名稱
AI_TIMEOUT=60           # 逾時（秒）
PLAYBOOK=research.md    # 研究作業手冊位置
EMPTY_LINE_FOLLOWS=1

QUOTED="has space"      # 引號值
"""


@pytest.fixture
def sandbox(tmp_path):
    """把 post_market.sh 複製到 tmp，配上會觸發問題的 .env 與 stub PATH。"""
    shutil.copy(_REPO / "post_market.sh", tmp_path / "post_market.sh")
    (tmp_path / ".env").write_text(_ENV_WITH_COMMENTS, encoding="utf-8")

    calls = tmp_path / "calls.log"
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    # post_market.sh 只叫 python3；其餘 shell 內建不需要 stub。
    for name in ("python3",):
        stub = bin_dir / name
        stub.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "{calls}"\nexit 0\n')
        stub.chmod(0o755)

    def run():
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        r = subprocess.run(
            ["bash", str(tmp_path / "post_market.sh")],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
        )
        return r, (calls.read_text() if calls.exists() else "")

    return run


# ── 這是 2026-08-19 讓 post_market 永遠 exit=1 的那一行 ───────────────────────

def test_env_with_trailing_comments_does_not_break_the_script(sandbox):
    r, _ = sandbox()

    assert "not a valid identifier" not in r.stderr, (
        "行尾註解被當成變數名 export：\n" + r.stderr[:600]
    )
    assert r.returncode == 0, f"腳本以 {r.returncode} 結束：\n{r.stderr[:600]}"


def test_the_post_market_tasks_actually_run(sandbox):
    """腳本死在載入 .env 的話，三個盤後任務一個都不會跑。"""
    _, calls = sandbox()

    for task in ("adaptive_scorer.py", "dt_paper_trade.py", "notion_reporter.py"):
        assert task in calls, f"{task} 沒有被執行：\n{calls}"


def test_values_are_actually_exported(sandbox, tmp_path):
    """不是只求不報錯——變數要真的帶著乾淨的值進去。"""
    script = tmp_path / "post_market.sh"
    src = script.read_text(encoding="utf-8")
    # 在第一個 python3 呼叫之前插一行，把載到的值印出來
    src = src.replace("python3 adaptive_scorer.py",
                      'echo "SEEN AI_MODEL=[${AI_MODEL:-}] AI_TIMEOUT=[${AI_TIMEOUT:-}]"\n'
                      "python3 adaptive_scorer.py", 1)
    script.write_text(src, encoding="utf-8")

    r, _ = sandbox()

    assert "AI_MODEL=[haiku]" in r.stdout, f"值不乾淨：{r.stdout[:400]}"
    assert "AI_TIMEOUT=[60]" in r.stdout, f"值不乾淨：{r.stdout[:400]}"
