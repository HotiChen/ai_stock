"""Tests for planset expiry check in handle_select_plan()."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def test_expired_planset_rejected(monkeypatch):
    """昨天的 planset 被拒絕執行。"""
    import telegram_bot as bot

    monkeypatch.setattr(bot, "CHAT_ID", "123")
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()

    fake_planset = MagicMock()
    fake_planset.created_at = yesterday

    with patch("telegram_bot.load_pending_planset", return_value=fake_planset), \
         patch("telegram_bot.send_text") as mock_send:
        bot.handle_select_plan("123", "balanced")
        text = mock_send.call_args[0][1]
        assert "過期" in text or "已過期" in text


def test_today_planset_accepted(monkeypatch):
    """今天的 planset 正常執行（不發送過期訊息）。"""
    import telegram_bot as bot

    monkeypatch.setattr(bot, "CHAT_ID", "123")
    now_str = datetime.now().isoformat()

    from strategy_planner import PlanSet, StrategyPlan
    fake_plan = MagicMock(spec=StrategyPlan)
    fake_plan.picks = []
    fake_planset = MagicMock(spec=PlanSet)
    fake_planset.created_at = now_str
    fake_planset.balanced = fake_plan
    fake_planset.aggressive = fake_plan
    fake_planset.conservative = fake_plan

    sent_texts = []

    def capture_send(chat_id, text, **kw):
        sent_texts.append(text)

    with patch("telegram_bot.load_pending_planset", return_value=fake_planset), \
         patch("telegram_bot.execute_plan", return_value=[]), \
         patch("telegram_bot.send_text", side_effect=capture_send), \
         patch("telegram_bot.save_day_record"), \
         patch("telegram_bot.SimulatedPortfolio") as mock_portfolio_cls:
        mock_portfolio_cls.load.return_value = MagicMock()
        try:
            bot.handle_select_plan("123", "balanced")
        except Exception:
            pass  # other errors OK
    # 確認沒有送出「過期」訊息
    assert not any("過期" in t for t in sent_texts), f"Unexpected expiry message in: {sent_texts}"
