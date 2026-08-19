"""時間還沒到的階段不可報成故障。

2026-08-19 09:20 實際情況
-------------------------
在早上跑 `health_check.py --summary`，推給 Tim 的訊息是：

    ⚠️ 今日有階段未完成
    ✅ 盤前當沖預測
    ✅ 開盤確認
    ❌ 收盤複盤   13:30–14:05 之間沒有任何紀錄（main.py 可能未啟動，或排程未觸發）

收盤複盤當然沒有紀錄——那時候才早上九點。但訊息長得跟真的故障一模一樣，
而且 Tim 在工地上，他只看得到「❌」和「main.py 可能未啟動」。

這是這個專案反覆出現的同一種毛病：把「還不知道」呈現成一個看起來確定的
壞消息。監控自己誤報，比不監控更糟——真的故障來的時候會被當成又一次誤報。

三種狀態要分開：
    ok       ：時間到了，紀錄也在
    pending  ：時段還沒結束，現在沒紀錄很正常
    failed   ：時段過了，卻沒有紀錄（或出現失敗標記）
"""

from __future__ import annotations

from datetime import time

import pytest

_TODAY = "2026-08-19"


def _lines(*entries: str) -> list[str]:
    return list(entries)


def _premarket_ok() -> str:
    return f"{_TODAY} 08:36:13,214 [__main__] INFO: Premarket: 0 approved, 0 rejected"


# ── 這是 2026-08-19 09:20 誤報的那一則 ────────────────────────────────────────

def test_stage_whose_window_has_not_ended_is_pending_not_failed():
    import health_check

    r = health_check.check_stage(
        "settlement", _lines(_premarket_ok()), _TODAY, now=time(9, 20),
    )

    assert not r.ok
    assert r.pending, "收盤複盤在早上九點應該是『尚未到時間』，不是故障"


def test_pending_stage_does_not_alert():
    """誤報會稀釋真告警的可信度，pending 一律不推播。"""
    import health_check

    sent = []
    r = health_check.check_stage(
        "settlement", _lines(), _TODAY, now=time(9, 20),
    )
    health_check.maybe_alert(r, _TODAY, send=sent.append)

    assert sent == [], f"pending 階段不該送出告警，卻送了：{sent}"


def test_pending_stage_reads_as_waiting_in_the_summary():
    import health_check

    r = health_check.check_stage("settlement", _lines(), _TODAY, now=time(9, 20))
    text = health_check.build_summary([r], _TODAY)

    assert "❌" not in text, f"pending 不可印成 ❌：\n{text}"
    assert "尚未" in text or "⏳" in text, text
    assert "有階段未完成" not in text, f"還沒到時間不算未完成：\n{text}"


# ── 時段過了就是真的故障，不可被 pending 吃掉 ─────────────────────────────────

def test_missing_record_after_the_window_is_a_real_failure():
    import health_check

    r = health_check.check_stage("settlement", _lines(), _TODAY, now=time(14, 30))

    assert not r.ok
    assert not r.pending, "14:30 還沒有收盤複盤紀錄，這是真的故障"


def test_a_real_failure_after_the_window_still_alerts():
    import health_check

    sent = []
    r = health_check.check_stage("premarket", _lines(), _TODAY, now=time(14, 30))
    health_check.maybe_alert(r, _TODAY, send=sent.append)

    assert len(sent) == 1, "時段過了卻沒紀錄，必須告警"
    assert "系統告警" in sent[0]


def test_success_is_still_success():
    import health_check

    r = health_check.check_stage(
        "premarket", _lines(_premarket_ok()), _TODAY, now=time(9, 20),
    )

    assert r.ok and not r.pending


# ── 預設行為不變 ──────────────────────────────────────────────────────────────

def test_now_defaults_to_the_current_clock():
    """不傳 now 時要沿用現在時間，既有呼叫端不必改。"""
    import health_check

    r = health_check.check_stage("premarket", _lines(_premarket_ok()), _TODAY)

    assert r.ok


# ── CLI 結束碼 ────────────────────────────────────────────────────────────────

def test_cli_exit_code_treats_pending_as_not_a_failure(monkeypatch, capsys):
    """launchctl list 會顯示最後的結束碼，pending 不可長得像真故障。"""
    import health_check

    monkeypatch.setattr(health_check, "_read_log", list)
    monkeypatch.setattr(health_check.sys, "argv", ["health_check.py", "--stage", "settlement"])
    monkeypatch.setattr(health_check, "_send_telegram", lambda text: None)

    class _Now:
        @staticmethod
        def now():
            import datetime as _dt
            return _dt.datetime.combine(_dt.date.today(), time(9, 20))
    monkeypatch.setattr(health_check, "datetime", _Now)

    assert health_check.main() == 0
    assert "⏳" in capsys.readouterr().out
