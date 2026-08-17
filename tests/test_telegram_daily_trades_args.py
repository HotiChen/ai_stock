"""Telegram 的「今日狀態」「持倉」不可因缺參數而爆掉。

2026-08-17 13:01 正式環境
-------------------------
    ERROR: Polling error: load_daily_trades() missing 2 required
           positional arguments: 'trade_date' and 'path'

``research_db.load_daily_trades(trade_date: date, path: str)`` 兩個參數都是必填。
專案裡十處呼叫，只有 telegram_bot 的兩處沒傳：

    telegram_bot.py:153  handle_status()    trades = load_daily_trades()
    telegram_bot.py:180  handle_holdings()  trades = load_daily_trades()

這兩個正是使用者在 Telegram 上點按鈕會走到的路徑。Tim 盤中在工地，手機是
他唯一的介面——按下去只會拿到一則錯誤，或什麼都沒有。

而且它是在 polling 迴圈裡拋出的，錯誤被記成 "Polling error" 後整輪 update
處理就中止，同一批的其他訊息（例如當沖買入確認）也一起沒被處理。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest


@pytest.fixture
def sent():
    """攔截 send_text，避免測試真的打 Telegram API。"""
    import telegram_bot
    out = []
    with patch.object(telegram_bot, "send_text", lambda cid, text, *a, **kw: out.append(text)):
        yield out


def _trades() -> list[dict]:
    return [
        {"code": "2324", "name": "仁寶", "action": "buy", "quantity": 1,
         "price": 43.4, "amount": 43400, "pnl": 0, "lot_type": "common"},
        {"code": "2324", "name": "仁寶", "action": "sell", "quantity": 1,
         "price": 44.2, "amount": 44200, "pnl": 800, "lot_type": "common"},
    ]


# ── 這是 2026-08-17 炸掉的兩條路徑 ────────────────────────────────────────────

def test_handle_holdings_does_not_raise(sent):
    import telegram_bot

    with patch.object(telegram_bot, "load_daily_trades", return_value=_trades()) as m:
        telegram_bot.handle_holdings("12345")

    assert m.called, "應該有去讀今日交易"
    args, kwargs = m.call_args
    assert args or kwargs, "load_daily_trades 需要 trade_date 與 path，不可空呼叫"
    assert sent, "應該有送出訊息"


def test_handle_status_does_not_raise(sent):
    import telegram_bot

    with patch.object(telegram_bot, "load_daily_trades", return_value=_trades()), \
         patch("sim_position_store.load_sim_positions", return_value=[]), \
         patch("daily_tracker.load_day_record", return_value=None):
        telegram_bot.handle_status("12345")

    assert sent
    assert "今日狀態" in sent[0]


def test_holdings_passes_today_and_a_db_path(sent):
    """傳的必須是今天的日期與一個資料庫路徑，不是隨便補兩個值。"""
    import telegram_bot

    with patch.object(telegram_bot, "load_daily_trades", return_value=[]) as m:
        telegram_bot.handle_holdings("12345")

    args, kwargs = m.call_args
    passed = list(args) + list(kwargs.values())
    assert any(isinstance(v, date) and v == date.today() for v in passed), \
        f"沒有傳入今天的日期：{passed}"
    assert any(isinstance(v, str) and v.endswith(".db") for v in passed), \
        f"沒有傳入資料庫路徑：{passed}"


# ── 空資料時的行為 ────────────────────────────────────────────────────────────

def test_holdings_with_no_trades_says_so(sent):
    import telegram_bot

    with patch.object(telegram_bot, "load_daily_trades", return_value=[]):
        telegram_bot.handle_holdings("12345")

    assert sent
    assert "尚無交易" in sent[0]


def test_status_survives_empty_everything(sent):
    """完全沒資料時仍要回一則可讀的訊息，不可拋例外。"""
    import telegram_bot

    with patch.object(telegram_bot, "load_daily_trades", return_value=[]), \
         patch("sim_position_store.load_sim_positions", return_value=[]), \
         patch("daily_tracker.load_day_record", return_value=None):
        telegram_bot.handle_status("12345")

    assert sent
    assert "今日狀態" in sent[0]
