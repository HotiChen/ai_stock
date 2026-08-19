"""AI 沒分析到的股票，不可記成「AI 說放棄」。

2026-08-19 資料庫實況
---------------------
    2026-08-12  ai_summary 空 12/20
    2026-08-14  ai_summary 空 15/25
    2026-08-17  ai_summary 空 12/20
    2026-08-19  ai_summary 空 12/20

每天都有六成的預測沒有任何 AI 說明。原因在 daytrading_report.py:510：

    action=ai.action if ai else "skip",

報告只對前 analysis_count 檔做 AI 分析，卻把 display_count 檔全部落庫。
沒被分析到的那些，`ai` 是 None，於是 action 被寫成 "skip"，
target_price / stop_loss / ai_summary 一併是 None 與空字串。

落庫之後這兩種列一模一樣：

    AI 看過、判斷不做      action=skip  ai_summary="大盤-1.19%…等外資回頭"
    AI 根本沒看           action=skip  ai_summary=""

這是這個專案反覆出現的同一種毛病：把「不知道」寫成一個看起來確定的答案。
這裡的代價是學習資料——勝率統計只要哪天把 skip 納入分母，六成從未被分析
的列就會被當成 AI 的判斷去評分。
"""

from __future__ import annotations

import pytest


def test_unanalyzed_has_its_own_action():
    """要有一個跟 skip 分得開的值。"""
    import daytrading_db

    assert hasattr(daytrading_db, "ACTION_NOT_ANALYZED")
    assert daytrading_db.ACTION_NOT_ANALYZED != "skip"


def test_it_is_not_long_either():
    """不可被勝率統計當成有效預測。"""
    import daytrading_db

    assert daytrading_db.ACTION_NOT_ANALYZED != "long"


def test_a_stock_without_ai_analysis_is_marked_not_analyzed():
    from daytrading_report import _prediction_action

    assert _prediction_action(None) == "not_analyzed"


def test_a_stock_the_ai_rejected_keeps_skip():
    """AI 真的看過並判斷不做，仍然是 skip——這條資訊不可被稀釋。"""
    from daytrading_report import _prediction_action

    class _AI:
        action = "skip"

    assert _prediction_action(_AI()) == "skip"


def test_a_stock_the_ai_recommended_keeps_long():
    from daytrading_report import _prediction_action

    class _AI:
        action = "long"

    assert _prediction_action(_AI()) == "long"


# ── 落庫時真的用到 ────────────────────────────────────────────────────────────

def test_the_saved_rows_distinguish_the_two(monkeypatch):
    """同一批預測裡，有分析與沒分析的必須落成不同的 action。"""
    import daytrading_report

    src = (daytrading_report.__file__)
    with open(src, encoding="utf-8") as f:
        code = "\n".join(l for l in f if not l.lstrip().startswith("#"))

    assert 'action=ai.action if ai else "skip"' not in code, \
        "還在把沒分析到的寫成 skip"
    assert "_prediction_action(" in code, \
        "落庫時沒有用 _prediction_action"
