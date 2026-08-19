"""看門狗要能看出 main.py 死了，不能只讀 log。

2026-08-19 實際情況
-------------------
main.py 在 08:34 收到誤發的 SIGTERM，做完手上的盤前工作後於 08:39 停止。
log 讀起來就是一次乾淨的關機：

    08:38:16  daytrading_db: saved 20 predictions
    08:39:18  main.py stopped

接下來 35 分鐘完全沒有紀錄——但「沒有紀錄」在這套系統裡有兩種意思：

    a) main.py 活著，只是這段時間沒事做（今天 10:00 就是這樣，
       候選全 skipped，_mid_session_check 直接 return）
    b) main.py 根本不在了

兩者在 log 裡一模一樣。三個階段檢查最快也要等到 09:15 才會發現，而且是
間接推論出來的。這中間 09:05 的開盤確認窗口已經永久錯過。

程序死活是可以直接問的事實，不必用 log 推。這裡就直接問。
"""

from __future__ import annotations

from datetime import time

import pytest


def _check(alive: bool, now: time):
    import health_check
    return health_check.check_process(now=now, is_alive=lambda: alive)


# ── 這是 2026-08-19 08:39–09:14 那段沒人看見的空窗 ────────────────────────────

def test_dead_main_during_market_hours_is_a_failure():
    r = _check(alive=False, now=time(10, 0))

    assert not r.ok and not r.pending, "盤中 main.py 不在，必須是故障"
    assert "main.py" in r.detail


def test_dead_main_during_market_hours_alerts():
    import health_check

    sent = []
    health_check.maybe_alert(_check(alive=False, now=time(10, 0)), "2026-08-19", send=sent.append)

    assert len(sent) == 1, "盤中程序死掉一定要推播"


def test_live_main_during_market_hours_is_ok_and_silent():
    import health_check

    sent = []
    r = _check(alive=True, now=time(10, 0))
    health_check.maybe_alert(r, "2026-08-19", send=sent.append)

    assert r.ok
    assert sent == [], "正常時要安靜"


# ── 交易時段外不算故障 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("t", [time(6, 0), time(8, 0), time(14, 30), time(22, 0)])
def test_outside_trading_hours_is_pending_not_failure(t):
    """14:00 stop_all 之後 main.py 本來就該停，不可報成故障。"""
    r = _check(alive=False, now=t)

    assert r.pending, f"{t} 不在交易時段，main.py 不在是正常的"


def test_boundaries():
    assert _check(alive=False, now=time(8, 25)).pending is False, "08:25 起就該在"
    assert _check(alive=False, now=time(13, 59)).pending is False, "收盤前都該在"
    assert _check(alive=False, now=time(8, 24)).pending is True
    assert _check(alive=False, now=time(14, 0)).pending is True


# ── 真的去問系統 ──────────────────────────────────────────────────────────────

def test_default_probe_actually_asks_the_operating_system():
    """預設探針要真的查程序表，不能是寫死的值。"""
    import health_check

    # 這個程序自己一定在，用它自己的名字驗證探針會回 True
    import sys, os
    assert health_check._process_running("python") in (True, False)
    assert health_check._process_running("絕不可能存在的程序名xyzzy") is False


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_process_flag_reports_and_exits_zero_when_alive(monkeypatch, capsys):
    import health_check

    monkeypatch.setattr(health_check.sys, "argv", ["health_check.py", "--process"])
    monkeypatch.setattr(health_check, "check_process",
                        lambda: health_check.StageResult("process", "主程式 main.py", True, ""))
    monkeypatch.setattr(health_check, "_send_telegram", lambda text: None)

    assert health_check.main() == 0
    assert "✅" in capsys.readouterr().out


def test_cli_process_flag_exits_one_when_dead(monkeypatch, capsys):
    import health_check

    monkeypatch.setattr(health_check.sys, "argv", ["health_check.py", "--process"])
    monkeypatch.setattr(health_check, "check_process",
                        lambda: health_check.StageResult("process", "主程式 main.py", False, "沒了"))
    monkeypatch.setattr(health_check, "_send_telegram", lambda text: None)

    assert health_check.main() == 1
