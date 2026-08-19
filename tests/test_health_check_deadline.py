"""檢查必須在階段的截止時刻之後才判定，否則誤報。

2026-08-19 實際情況
-------------------
    launchd  com.aistock.health_premarket  08:35
    log      08:36:13  Premarket: 0 approved, 0 rejected

檢查比紀錄早 73 秒，於是推了一則「盤前當沖預測失敗（main.py 可能未啟動）」
給在工地上的 Tim。當天盤前其實完全正常。

三個檢查全都排在自己的時間窗結束之前：

    premarket   08:35 檢查，窗 08:25–09:00
    entry       09:15 檢查，窗 09:00–09:30
    settlement  13:40 檢查，窗 13:30–14:05

先前那版把「時段未結束」一律當 pending 不告警，套在這個排程上等於
整個看門狗永久靜音——比誤報更糟。

所以兩件事要分開：

    window    ：哪些 log 行算這個階段的（過濾用，維持寬鬆）
    deadline  ：什麼時候該做出判定（早於它就是 pending）

launchd 的執行時間排在 deadline 之後，判定才有意義。
"""

from __future__ import annotations

from datetime import time

import pytest

_TODAY = "2026-08-19"


def _premarket_line(hhmmss: str) -> str:
    return f"{_TODAY} {hhmmss},214 [__main__] INFO: Premarket: 0 approved, 0 rejected"


# ── 每個階段都要有截止時刻 ────────────────────────────────────────────────────

def test_every_stage_declares_a_deadline():
    import health_check

    for name, spec in health_check.STAGES.items():
        assert getattr(spec, "deadline", None), f"{name} 沒有 deadline"


def test_deadline_is_not_earlier_than_the_windows_end_of_real_work():
    """截止時刻必須晚於該階段實際完成的時間，否則又是誤報。

    盤前實測：08-17 完成於 08:33、08-19 完成於 08:38。
    """
    import health_check

    dl = health_check.STAGES["premarket"].deadline
    assert dl >= "08:40", f"盤前截止 {dl} 太早，08-19 實際 08:38 才完成"


# ── 這是 2026-08-19 08:35 誤報的那一則 ────────────────────────────────────────

def test_checking_before_the_deadline_is_pending_not_failure():
    import health_check

    r = health_check.check_stage("premarket", [], _TODAY, now=time(8, 35))

    assert r.pending, "08:35 還沒到盤前截止，不可判定為故障"


def test_after_the_deadline_a_missing_record_is_a_real_failure():
    import health_check

    r = health_check.check_stage("premarket", [], _TODAY, now=time(8, 46))

    assert not r.ok and not r.pending, "過了截止還沒紀錄就是真故障"


def test_a_record_that_lands_late_still_counts():
    """08:36 才落地的紀錄，在 08:46 判定時要算數。"""
    import health_check

    r = health_check.check_stage(
        "premarket", [_premarket_line("08:36:13")], _TODAY, now=time(8, 46),
    )

    assert r.ok


# ── 排程必須排在截止之後 ──────────────────────────────────────────────────────

_PLIST_STAGE = {
    "com.aistock.health_premarket":  "premarket",
    "com.aistock.health_entry":      "entry",
    "com.aistock.health_settlement": "settlement",
}


def _plist_time(label: str) -> str | None:
    import plistlib
    from pathlib import Path

    p = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not p.exists():
        return None
    d = plistlib.loads(p.read_bytes())
    entries = d.get("StartCalendarInterval") or []
    if isinstance(entries, dict):
        entries = [entries]
    if not entries:
        return None
    return f"{entries[0].get('Hour', 0):02d}:{entries[0].get('Minute', 0):02d}"


@pytest.mark.parametrize("label,stage", sorted(_PLIST_STAGE.items()))
def test_launchd_runs_the_check_after_the_deadline(label, stage):
    """排在截止之前的話，這個檢查永遠只會回 pending，等於沒裝看門狗。"""
    import health_check

    job = _plist_time(label)
    if job is None:
        pytest.skip(f"{label}.plist 不在這台機器上")

    dl = health_check.STAGES[stage].deadline
    assert job >= dl, (
        f"{label} 排在 {job}，但 {stage} 的截止是 {dl}——"
        "檢查會永遠回 pending，故障不會有人知道。"
    )
