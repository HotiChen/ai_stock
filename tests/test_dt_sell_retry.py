"""tests/test_dt_sell_retry.py — Task 3: 賣單失敗後同規則仍會重試 + 升級告警.

語意：警報去重只擋「通知重複」，不擋「賣單重試」。
  - force_stop_loss 成功才 mark_closed
  - 失敗則 record_sell_attempt，下一輪輪詢同規則仍會再觸發賣單
  - sell_attempts >= 3 仍失敗 → Telegram 升級告警（人工介入）
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def _seed_active(path, code="2330", entry_price=100.0, quantity=1000):
    from daytrading_monitor import DaytradingPosition, save_daytrading_positions
    save_daytrading_positions([
        DaytradingPosition(
            code=code, name="台積電", entry_low=None, entry_high=None,
            target_price=None, stop_loss=None, dt_score=90, status="active",
            entry_price=entry_price, peak_price=entry_price,
            quantity=quantity, lot_type="common",
        ),
    ], path=path)


def _cfg(**kw):
    from daytrading_config import DaytradingConfig
    base = dict(force_close_time="00:00", paper_trade_only=False)
    base.update(kw)
    return DaytradingConfig(**base)


class TestSellRetry:
    def test_sell_alert_refires_each_poll_until_success(self, tmp_path):
        """前兩次 force_stop_loss=False（維持 active、重試），第三次 True → closed。"""
        import main
        from daytrading_monitor import load_daytrading_positions
        from unittest.mock import MagicMock
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)

        with patch("daytrading_monitor.fetch_current_price", return_value=101.0), \
             patch("main.force_stop_loss", side_effect=[False, False, True]) as mock_sell:
            # 兩輪失敗 → 仍 active
            for _ in range(2):
                sells = main._dt_poll_tick(MagicMock(), _cfg(), dt_path=pos_path)
                assert sells and sells[0].alert_type == "force_close"
            assert load_daytrading_positions(path=pos_path)[0].status == "active"
            # 第三輪成功 → closed
            main._dt_poll_tick(MagicMock(), _cfg(), dt_path=pos_path)

        assert mock_sell.call_count == 3
        pos = load_daytrading_positions(path=pos_path)[0]
        assert pos.status == "closed"

    def test_sell_attempts_recorded_on_failure(self, tmp_path):
        import main
        from daytrading_monitor import load_daytrading_positions
        from unittest.mock import MagicMock
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        with patch("daytrading_monitor.fetch_current_price", return_value=101.0), \
             patch("main.force_stop_loss", return_value=False):
            main._dt_poll_tick(MagicMock(), _cfg(), dt_path=pos_path)
            main._dt_poll_tick(MagicMock(), _cfg(), dt_path=pos_path)
        pos = load_daytrading_positions(path=pos_path)[0]
        assert pos.status == "active"
        assert pos.sell_attempts == 2

    def test_escalation_alert_after_three_failures(self, tmp_path):
        """連續 3 次失敗 → 第 3 次發升級告警（需人工介入）。"""
        import main
        from unittest.mock import MagicMock
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        with patch("daytrading_monitor.fetch_current_price", return_value=101.0), \
             patch("main.force_stop_loss", return_value=False), \
             patch("main.TELEGRAM_CHAT_ID", "999"), \
             patch("telegram_bot.send_text") as mock_send:
            for _ in range(3):
                main._dt_poll_tick(MagicMock(), _cfg(), dt_path=pos_path)
        msgs = [c.args[1] for c in mock_send.call_args_list]
        assert any("需人工介入" in m for m in msgs)

    def test_success_marks_closed_and_notifies(self, tmp_path):
        import main
        from unittest.mock import MagicMock
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        with patch("daytrading_monitor.fetch_current_price", return_value=101.0), \
             patch("main.force_stop_loss", return_value=True), \
             patch("main.TELEGRAM_CHAT_ID", "999"), \
             patch("telegram_bot.send_text") as mock_send:
            main._dt_poll_tick(MagicMock(), _cfg(), dt_path=pos_path)
        msgs = [c.args[1] for c in mock_send.call_args_list]
        assert any("出場已送出" in m for m in msgs)
