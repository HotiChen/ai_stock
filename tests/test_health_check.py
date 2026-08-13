"""交易日各階段的健康檢查。

為什麼需要
----------
Tim 在盤中於工地工作，唯一的訊息管道是 Telegram。系統目前所有失敗都只寫
進 logs/ai_stock.log：

  - 2026-08-12 大盤指數取不到而回 0.0，AI 判定「無方向」放棄全部 8 檔
  - 2026-08-13 當沖報告在評分階段 TypeError 直接中斷，整天沒有推播

兩天都是「Telegram 一片安靜」，只能靠**沒收到訊息**反推系統壞了。這在
盤中無法即時處理的情境下等於沒有監控。

本模組在每個階段結束後檢查 log，只要該出現的標記沒出現、或出現已知的失敗
字樣，就主動推一則 Telegram 告警。設計成獨立腳本由 launchd 執行，
不依賴 main.py 是否還活著——main.py 本身死掉正是要偵測的情況之一。
"""

from __future__ import annotations

from datetime import date

import pytest


TODAY = date(2026, 8, 13).isoformat()


def _line(time: str, text: str, level: str = "INFO") -> str:
    return f"{TODAY} {time},000 [__main__] {level}: {text}"


# ── 階段判定 ─────────────────────────────────────────────────────────────────

def test_premarket_healthy_when_report_pushed():
    from health_check import check_stage

    lines = [
        _line("08:30:04", "=== morning_strategy 開始 ==="),
        _line("08:32:26", "Premarket: 3 approved, 1 rejected"),
    ]
    result = check_stage("premarket", lines, today=TODAY)

    assert result.ok is True


def test_premarket_unhealthy_when_report_push_failed():
    """重現 2026-08-13：報告在評分階段炸掉。"""
    from health_check import check_stage

    lines = [
        _line("08:32:33",
              "DayTrading report push failed: '>=' not supported between "
              "instances of 'NoneType' and 'float'", level="WARNING"),
    ]
    result = check_stage("premarket", lines, today=TODAY)

    assert result.ok is False
    assert "DayTrading report push failed" in result.detail


def test_premarket_unhealthy_when_nothing_ran_at_all():
    """整個階段沒有任何紀錄——main.py 沒起來、或排程沒觸發。"""
    from health_check import check_stage

    result = check_stage("premarket", [], today=TODAY)

    assert result.ok is False
    assert "沒有任何紀錄" in result.detail


def test_entry_stage_healthy_with_confirmations():
    from health_check import check_stage

    lines = [_line("09:05:17", "DT 9:05 確認：2 繼續 / 6 放棄")]
    assert check_stage("entry", lines, today=TODAY).ok is True


def test_entry_stage_healthy_even_when_all_skipped():
    """全數放棄是合法的交易判斷，不是故障——不可誤報。"""
    from health_check import check_stage

    lines = [_line("09:05:17", "DT 9:05 確認：0 繼續 / 8 放棄")]
    result = check_stage("entry", lines, today=TODAY)

    assert result.ok is True, "全部放棄可能只是當天沒有好標的"


def test_settlement_stage_healthy():
    from health_check import check_stage

    lines = [_line("13:35:10", "PostMarket: pnl=+0 saved")]
    assert check_stage("settlement", lines, today=TODAY).ok is True


# ── 只看當日 ─────────────────────────────────────────────────────────────────

def test_ignores_other_days():
    """昨天的成功紀錄不能讓今天看起來健康。"""
    from health_check import check_stage

    lines = [f"2026-08-12 08:32:26,000 [__main__] INFO: Premarket: 3 approved, 1 rejected"]
    result = check_stage("premarket", lines, today=TODAY)

    assert result.ok is False


# ── 告警內容 ─────────────────────────────────────────────────────────────────

def test_alert_message_names_the_stage_and_reason():
    """在工地只會看一眼——訊息要直接說哪一段壞了、為什麼。"""
    from health_check import build_alert, StageResult

    msg = build_alert(StageResult(
        stage="premarket",
        label="盤前當沖預測",
        ok=False,
        detail="DayTrading report push failed: TypeError",
    ), today=TODAY)

    assert "盤前當沖預測" in msg
    assert "TypeError" in msg
    assert TODAY in msg


def test_healthy_stage_produces_no_alert():
    from health_check import maybe_alert, StageResult

    sent = []
    maybe_alert(
        StageResult(stage="entry", label="開盤確認", ok=True, detail=""),
        today=TODAY,
        send=sent.append,
    )
    assert sent == [], "健康時不可發訊息，否則會變成雜訊而被忽略"


def test_unhealthy_stage_sends_exactly_one_alert():
    from health_check import maybe_alert, StageResult

    sent = []
    maybe_alert(
        StageResult(stage="premarket", label="盤前當沖預測", ok=False, detail="炸了"),
        today=TODAY,
        send=sent.append,
    )
    assert len(sent) == 1


# ── 每日摘要 ─────────────────────────────────────────────────────────────────

def test_daily_summary_lists_every_stage():
    from health_check import build_summary, StageResult

    results = [
        StageResult("premarket", "盤前當沖預測", True, ""),
        StageResult("entry", "開盤確認", False, "無紀錄"),
        StageResult("settlement", "收盤複盤", True, ""),
    ]
    msg = build_summary(results, today=TODAY)

    for label in ("盤前當沖預測", "開盤確認", "收盤複盤"):
        assert label in msg
    assert "無紀錄" in msg


def test_daily_summary_marks_overall_failure():
    from health_check import build_summary, StageResult

    ok_only = build_summary(
        [StageResult("premarket", "盤前當沖預測", True, "")], today=TODAY)
    with_fail = build_summary(
        [StageResult("premarket", "盤前當沖預測", False, "炸了")], today=TODAY)

    assert ok_only != with_fail
    assert "⚠️" in with_fail


# ── 時間窗 ───────────────────────────────────────────────────────────────────
#
# 只比對「當天有沒有這個標記」是不夠的：正式 log 曾混入凌晨的測試紀錄
# （2026-08-14 00:38 有一筆 "Premarket: 1 approved, 0 rejected"），
# 讓健康檢查在什麼都還沒跑的凌晨就回報一切正常——假陰性比沒有檢查更糟。
# 每個階段只採計它自己時段內的紀錄。

def test_marker_outside_stage_window_is_ignored():
    """凌晨 00:38 的 Premarket 紀錄不能當成 08:30 那次執行。"""
    from health_check import check_stage

    lines = [_line("00:38:12", "Premarket: 1 approved, 0 rejected")]
    result = check_stage("premarket", lines, today=TODAY)

    assert result.ok is False


def test_marker_inside_stage_window_counts():
    from health_check import check_stage

    lines = [_line("08:32:26", "Premarket: 3 approved, 1 rejected")]
    assert check_stage("premarket", lines, today=TODAY).ok is True


def test_entry_window_excludes_premarket_time():
    """08:32 的紀錄不能用來證明 09:05 的開盤確認跑過。"""
    from health_check import check_stage

    lines = [_line("08:32:26", "DT 9:05 確認：2 繼續 / 6 放棄")]
    assert check_stage("entry", lines, today=TODAY).ok is False


def test_settlement_window_excludes_morning():
    from health_check import check_stage

    lines = [_line("09:00:00", "PostMarket: pnl=+0 saved")]
    assert check_stage("settlement", lines, today=TODAY).ok is False
