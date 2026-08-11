"""morning_strategy.run() 在策略生成失敗時的防禦行為。

為什麼需要這個測試：

`generate_strategy_plans()` 有 7 個 `return None` 的路徑（LLM 回應解析失敗、
候選股為空、API 逾時等）。2026-08-10 實測時 Shioaji 登入失敗導致股價全為 0，
候選股算不出來，`plan_set` 回傳 None，然後被原封不動傳進
`send_morning_push()` → `save_pending_planset()` → `planset_to_dict()`，
炸在 `AttributeError: 'NoneType' object has no attribute 'aggressive'`。

這不是環境問題：只要哪天生成失敗（LLM 抽風、盤前無資料、券商斷線），
同一條路徑就會再炸一次。呼叫端有責任在推播前確認真的有東西可以推。

另外，炸掉的位置在 `save_pending_planset()`——那是「把待執行計畫存檔」的動作。
若沒有守住，異常發生前有機會把壞掉的狀態寫進 data/pending_planset.json，
影響後續使用者從 Telegram 點選執行的流程。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def goal_dir(tmp_path, monkeypatch):
    """建一個只有 strategy_goal.json 的臨時工作目錄並切過去。

    用真的 save_goal() 寫檔而不是手刻 JSON，這樣格式若哪天改了，
    測試會跟著走而不是悄悄失效。
    """
    from strategy_tracker import StrategyGoal, save_goal

    # conftest 的 _isolate_data_dir 已經建過 data/ 並 chdir 過來，
    # 這裡只需確保存在即可。
    (tmp_path / "data").mkdir(exist_ok=True)
    save_goal(
        StrategyGoal(
            target_multiplier=2.0,
            start_date=date(2026, 5, 30),
            end_date=date(2026, 12, 31),
            initial_capital=30000.0,
            approach="測試用目標",
        ),
        str(tmp_path / "data" / "strategy_goal.json"),
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def push_spy(monkeypatch):
    """攔截 send_morning_push，記錄它有沒有被呼叫。"""
    import telegram_bot

    spy = MagicMock()
    monkeypatch.setattr(telegram_bot, "send_morning_push", spy)
    return spy


def _neutralise_market(monkeypatch):
    """把外部相依（券商 API、候選股）換掉，讓測試不碰網路。"""
    import morning_strategy

    monkeypatch.setattr(morning_strategy, "_is_trading_day", lambda: True)
    monkeypatch.setattr(morning_strategy, "_get_api", lambda: MagicMock())
    monkeypatch.setattr(morning_strategy, "build_candidates", lambda api=None: [])


def test_no_push_when_plan_generation_returns_none(goal_dir, push_spy, monkeypatch):
    """生成失敗時不可推播——這是原本會炸的那條路徑。"""
    import morning_strategy
    import strategy_planner

    _neutralise_market(monkeypatch)
    monkeypatch.setattr(
        strategy_planner, "generate_strategy_plans", lambda *a, **kw: None
    )

    morning_strategy.run()  # 不可拋出例外

    assert not push_spy.called, (
        "plan_set 為 None 時仍呼叫了 send_morning_push——"
        "會在 planset_to_dict() 炸 AttributeError"
    )


def test_failure_is_logged_not_swallowed(goal_dir, push_spy, monkeypatch, caplog):
    """不推播還不夠，必須留下紀錄，否則排程靜默失敗沒人知道。"""
    import logging

    import morning_strategy
    import strategy_planner

    _neutralise_market(monkeypatch)
    monkeypatch.setattr(
        strategy_planner, "generate_strategy_plans", lambda *a, **kw: None
    )

    with caplog.at_level(logging.WARNING):
        morning_strategy.run()

    assert any(
        rec.levelno >= logging.WARNING for rec in caplog.records
    ), "策略生成失敗卻沒有任何 WARNING/ERROR 紀錄"


def test_push_still_happens_on_success(goal_dir, push_spy, monkeypatch):
    """反向確認：正常情況照樣推播，守衛不能把好路徑一起擋掉。"""
    import morning_strategy
    import strategy_planner

    _neutralise_market(monkeypatch)
    plan_set = MagicMock()
    monkeypatch.setattr(
        strategy_planner, "generate_strategy_plans", lambda *a, **kw: plan_set
    )

    morning_strategy.run()

    assert push_spy.called, "策略生成成功時卻沒有推播"
    assert push_spy.call_args.kwargs.get("plan_set") is plan_set
