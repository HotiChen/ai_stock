"""交易時段內不可讓 Mac 進入閒置睡眠。

2026-08-18 實際故障
-------------------
main.py 08:28 正常啟動，morning_strategy 08:34:36 開始生成策略，接著：

    pmset: 08:35:07 Entering Sleep state
    log:   11:44:08 策略生成完成，推播到 Telegram

整個 process 被凍結 3 小時 10 分。當沖預測、09:05 開盤確認、盤中監控
全部沒跑；策略推播在 11:44 才送出，早就沒有參考價值。

隔天（08-19）沒睡著純粹是運氣——當時唯一持有 PreventUserIdleSystemSleep
assertion 的是 Adobe Lightroom Classic。它一關，交易系統就跟 08-18 一樣死。

start_all.sh 啟動了五個服務卻沒有向系統宣告「這段時間別睡」。這裡補上，
並且要求那個 assertion 綁在 main.py 的生命週期上——不能變成關不掉的殘留。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

# 兩支腳本會叫到的外部程式，測試裡全部換成只記錄呼叫的 stub。
#
# pkill 與 kill 必須在這份清單裡。第一版漏了它們，跑 stop_all.sh 的測試
# 就對整台機器執行了真的 pkill，把當時正在跑的 telegram_bot.py 砍掉——
# 測試絕不能碰到測試沙箱以外的東西，尤其不能碰正式交易程序。
_STUBBED = [
    "caffeinate", "pgrep", "pkill", "kill", "lsof",
    "nohup", "npm", "open", "sleep", "python3",
]


@pytest.fixture
def sandbox(tmp_path):
    """把兩支腳本複製到 tmp，配上假的 PATH，回傳 (執行器, 呼叫紀錄路徑)。"""
    for name in ("start_all.sh", "stop_all.sh"):
        shutil.copy(_REPO / name, tmp_path / name)

    # start_all.sh 的守衛要求 main.py 存在
    (tmp_path / "main.py").touch()
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()

    calls = tmp_path / "calls.log"
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    for name in _STUBBED:
        stub = bin_dir / name
        # pgrep 回 1（查無程序）讓 start_all 走「啟動」而非「略過」分支；
        # 其餘一律成功。所有呼叫都記到 calls.log。
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "{name} $*" >> "{calls}"\n'
            + ("exit 1\n" if name in ("pgrep", "lsof") else "exit 0\n")
        )
        stub.chmod(0o755)

    def run(script: str) -> str:
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        subprocess.run(
            ["bash", str(tmp_path / script)],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
        )
        return calls.read_text() if calls.exists() else ""

    return run, calls


def _caffeinate_line(out: str) -> str:
    """取出 caffeinate 的呼叫。腳本用 `nohup caffeinate ...` 起，
    所以紀錄到的那行開頭是 nohup，不能只看 startswith。"""
    hits = [l for l in out.splitlines() if "caffeinate" in l and "pgrep" not in l]
    assert hits, f"沒有任何 caffeinate 呼叫：\n{out}"
    return hits[0]


# ── 這是 2026-08-18 缺的那一塊 ────────────────────────────────────────────────

def test_start_all_asserts_no_idle_sleep(sandbox):
    run, _ = sandbox
    out = run("start_all.sh")

    assert "caffeinate" in out, (
        "start_all.sh 沒有向系統宣告防閒置睡眠。08-18 就是這樣讓 Mac 在\n"
        f"08:35 睡著、交易停擺三小時。實際呼叫：\n{out}"
    )


def test_the_assertion_blocks_idle_sleep_specifically(sandbox):
    """要的是 -i（PreventUserIdleSystemSleep），不是只擋螢幕的 -d。"""
    run, _ = sandbox
    line = _caffeinate_line(run("start_all.sh"))

    assert " -i" in line or line.split()[1].startswith("-i"), \
        f"caffeinate 沒有用 -i 擋系統閒置睡眠：{line}"


def test_the_assertion_cannot_outlive_the_trading_day(sandbox):
    """必須綁 main.py 的存活或設時限，否則會變成永遠關不掉的殘留。"""
    run, _ = sandbox
    line = _caffeinate_line(run("start_all.sh"))

    assert " -w " in line or " -t " in line, (
        f"caffeinate 沒有綁定結束條件（-w 綁 PID 或 -t 設時限）：{line}\n"
        "沒有結束條件的話，收工後 Mac 會永遠不睡。"
    )


def test_stop_all_releases_the_assertion(sandbox):
    run, _ = sandbox
    out = run("stop_all.sh")

    assert "pkill -x caffeinate" in out, (
        f"stop_all.sh 沒有解除防睡眠 assertion，收工後 Mac 會一直不睡：\n{out}"
    )


# ── 不可重複堆疊 ──────────────────────────────────────────────────────────────

def test_start_all_does_not_stack_a_second_assertion(tmp_path):
    """已經有 caffeinate 在跑時不可再開一個。

    start_all 對 main.py 與後端都有「已在執行就略過」的守衛，
    防睡眠也要有，否則手動重跑一次就多留一個孤兒 process。
    """
    src = (_REPO / "start_all.sh").read_text(encoding="utf-8")

    assert "caffeinate" in src, "start_all.sh 還沒有 caffeinate"
    block = src[src.index("caffeinate") - 400: src.index("caffeinate") + 200]
    assert "pgrep" in block, (
        "caffeinate 前後找不到 pgrep 守衛，重跑 start_all 會堆疊出多個 caffeinate：\n"
        + block
    )
