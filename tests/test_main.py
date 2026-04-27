from __future__ import annotations

"""
TDD tests for main.py scheduled jobs.

Three jobs:
  08:30 PremarketJob  → strategy planner + risk_guard + send Telegram confirmation
  09:00 MarketOpenJob → execute approved picks via executor + start MonitorAgent
  13:35 PostMarketJob → stop MonitorAgent + save daily summary to DB
"""

from datetime import datetime, date
from unittest.mock import MagicMock, patch, call
import pytest


# ── is_trading_day ────────────────────────────────────────────────────────────

class TestIsTradingDay:
    def test_monday_is_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 4, 27)) is True  # Monday

    def test_friday_is_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 5, 1)) is True  # Friday

    def test_saturday_is_not_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 5, 2)) is False  # Saturday

    def test_sunday_is_not_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 5, 3)) is False  # Sunday

    def test_wednesday_is_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 4, 29)) is True  # Wednesday


# ── PremarketJob ──────────────────────────────────────────────────────────────

class TestPremarketJob:
    def _make_approved_picks(self):
        return [
            {"code": "2330", "name": "台積電", "budget": 5000.0,
             "sector": "半導體", "signal": "buy", "confidence": 8,
             "target_price": 900.0, "stop_loss_price": 800.0},
        ]

    @patch("main.send_confirmation")
    @patch("main.validate_plan")
    @patch("main.run_deep_analysis")
    @patch("main.save_daily_plan")
    def test_premarket_saves_approved_picks_to_db(
        self, mock_save, mock_deep, mock_validate, mock_send
    ):
        mock_deep.return_value = MagicMock(signal="buy", confidence=8,
                                           summary="good", factors=MagicMock(),
                                           hold_days=3, target_price=900.0,
                                           stop_loss_price=800.0)
        mock_validate.return_value = {
            "approved": self._make_approved_picks(), "rejected": []
        }
        mock_send.return_value = 1

        from main import PremarketJob
        job = PremarketJob(
            candidates=[{"code": "2330", "name": "台積電"}],
            capital=100_000.0,
            db_path=":memory:",
            telegram_chat_id="12345",
            current_positions=[],
        )
        job.run(market_summary="市場穩定", theme_info="AI題材")
        mock_save.assert_called_once()

    @patch("main.send_confirmation")
    @patch("main.validate_plan")
    @patch("main.run_deep_analysis")
    @patch("main.save_daily_plan")
    def test_premarket_sends_telegram_when_picks_exist(
        self, mock_save, mock_deep, mock_validate, mock_send
    ):
        mock_deep.return_value = MagicMock(signal="buy", confidence=8,
                                           summary="good", factors=MagicMock(),
                                           hold_days=3, target_price=900.0,
                                           stop_loss_price=800.0)
        mock_validate.return_value = {
            "approved": self._make_approved_picks(), "rejected": []
        }
        mock_send.return_value = 1

        from main import PremarketJob
        job = PremarketJob(
            candidates=[{"code": "2330", "name": "台積電"}],
            capital=100_000.0,
            db_path=":memory:",
            telegram_chat_id="12345",
            current_positions=[],
        )
        job.run(market_summary="市場穩定", theme_info="AI題材")
        mock_send.assert_called_once()

    @patch("main.send_confirmation")
    @patch("main.validate_plan")
    @patch("main.run_deep_analysis")
    @patch("main.save_daily_plan")
    def test_premarket_no_telegram_when_no_approved(
        self, mock_save, mock_deep, mock_validate, mock_send
    ):
        mock_deep.return_value = MagicMock(signal="hold", confidence=3,
                                           summary="weak", factors=MagicMock(),
                                           hold_days=1, target_price=None,
                                           stop_loss_price=None)
        mock_validate.return_value = {"approved": [], "rejected": []}

        from main import PremarketJob
        job = PremarketJob(
            candidates=[{"code": "2330", "name": "台積電"}],
            capital=100_000.0,
            db_path=":memory:",
            telegram_chat_id="12345",
            current_positions=[],
        )
        job.run(market_summary="市場穩定", theme_info="AI題材")
        mock_send.assert_not_called()

    @patch("main.send_confirmation")
    @patch("main.validate_plan")
    @patch("main.run_deep_analysis")
    @patch("main.save_daily_plan")
    def test_premarket_filters_only_buy_signals(
        self, mock_save, mock_deep, mock_validate, mock_send
    ):
        """Only pass 'buy' signals to validate_plan."""
        call_signals = []

        def fake_deep(code, name, news, fundamentals_text, market_summary, theme_info):
            sig = "buy" if code == "2330" else "hold"
            m = MagicMock()
            m.signal = sig
            m.confidence = 8
            m.target_price = 900.0 if sig == "buy" else None
            m.stop_loss_price = 800.0 if sig == "buy" else None
            return m

        mock_deep.side_effect = fake_deep
        mock_validate.return_value = {
            "approved": self._make_approved_picks(), "rejected": []
        }
        mock_send.return_value = 1

        from main import PremarketJob
        job = PremarketJob(
            candidates=[
                {"code": "2330", "name": "台積電"},
                {"code": "2454", "name": "聯發科"},
            ],
            capital=100_000.0,
            db_path=":memory:",
            telegram_chat_id="12345",
            current_positions=[],
        )
        job.run(market_summary="市場穩定", theme_info="AI題材")
        # validate_plan should only receive the buy pick
        picks_passed = mock_validate.call_args[0][0]
        codes_passed = [p["code"] for p in picks_passed]
        assert "2330" in codes_passed
        assert "2454" not in codes_passed

    @patch("main.send_confirmation")
    @patch("main.validate_plan")
    @patch("main.run_deep_analysis")
    @patch("main.save_daily_plan")
    def test_premarket_returns_approved_picks(
        self, mock_save, mock_deep, mock_validate, mock_send
    ):
        approved = self._make_approved_picks()
        mock_deep.return_value = MagicMock(signal="buy", confidence=8,
                                           summary="good", factors=MagicMock(),
                                           hold_days=3, target_price=900.0,
                                           stop_loss_price=800.0)
        mock_validate.return_value = {"approved": approved, "rejected": []}
        mock_send.return_value = 1

        from main import PremarketJob
        job = PremarketJob(
            candidates=[{"code": "2330", "name": "台積電"}],
            capital=100_000.0,
            db_path=":memory:",
            telegram_chat_id="12345",
            current_positions=[],
        )
        result = job.run(market_summary="市場穩定", theme_info="AI題材")
        assert result == approved


# ── MarketOpenJob ─────────────────────────────────────────────────────────────

class TestMarketOpenJob:
    def _approved(self):
        return [
            {"code": "2330", "name": "台積電", "budget": 5000.0,
             "sector": "半導體", "signal": "buy", "confidence": 8,
             "target_price": 900.0, "stop_loss_price": 800.0},
        ]

    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_market_open_places_orders(self, mock_monitor_cls, mock_place):
        mock_place.return_value = MagicMock(success=True, order_id="X1",
                                            code="2330", action="buy")
        mock_monitor = MagicMock()
        mock_monitor_cls.return_value = mock_monitor

        from main import MarketOpenJob
        api = MagicMock()
        job = MarketOpenJob(
            api=api, approved_picks=self._approved(),
            db_path=":memory:", telegram_chat_id=None,
            hard_limit=500_000, prior_orders=[],
        )
        job.run()
        mock_place.assert_called_once()

    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_market_open_starts_monitor(self, mock_monitor_cls, mock_place):
        mock_place.return_value = MagicMock(success=True)
        mock_monitor = MagicMock()
        mock_monitor_cls.return_value = mock_monitor

        from main import MarketOpenJob
        api = MagicMock()
        job = MarketOpenJob(
            api=api, approved_picks=self._approved(),
            db_path=":memory:", telegram_chat_id=None,
            hard_limit=500_000, prior_orders=[],
        )
        job.run()
        mock_monitor.start.assert_called_once()

    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_market_open_saves_trade_to_db(self, mock_monitor_cls, mock_place, tmp_path):
        from main import MarketOpenJob
        from research_db import init_db, load_daily_trades

        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.order_id = "ORD001"
        mock_result.code = "2330"
        mock_result.name = "台積電"
        mock_result.action = "buy"
        mock_result.quantity = 1
        mock_result.price = 850.0
        mock_result.amount = 850_000.0
        mock_result.lot_type = "common"
        mock_place.return_value = mock_result
        mock_monitor_cls.return_value = MagicMock()

        api = MagicMock()
        snapshot = MagicMock()
        snapshot.close = 850.0
        api.snapshots.return_value = [snapshot]

        job = MarketOpenJob(
            api=api, approved_picks=self._approved(),
            db_path=db_path, telegram_chat_id=None,
            hard_limit=500_000, prior_orders=[],
        )
        job.run()

        trades = load_daily_trades(date.today(), db_path)
        assert len(trades) == 1
        assert trades[0]["code"] == "2330"

    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_market_open_returns_monitor_agent(self, mock_monitor_cls, mock_place):
        mock_place.return_value = MagicMock(success=True)
        mock_monitor = MagicMock()
        mock_monitor_cls.return_value = mock_monitor

        from main import MarketOpenJob
        job = MarketOpenJob(
            api=MagicMock(), approved_picks=self._approved(),
            db_path=":memory:", telegram_chat_id=None,
            hard_limit=500_000, prior_orders=[],
        )
        monitor = job.run()
        assert monitor is mock_monitor


# ── PostMarketJob ─────────────────────────────────────────────────────────────

class TestPostMarketJob:
    @patch("main.save_daily_summary")
    def test_post_market_stops_monitor(self, mock_save):
        from main import PostMarketJob
        mock_monitor = MagicMock()
        job = PostMarketJob(monitor=mock_monitor, db_path=":memory:",
                            execution_id="EX001")
        job.run(total_pnl=1500.0, trades_summary="買入 2330 1張")
        mock_monitor.stop.assert_called_once()

    @patch("main.save_daily_summary")
    def test_post_market_saves_daily_summary(self, mock_save):
        from main import PostMarketJob
        job = PostMarketJob(monitor=MagicMock(), db_path=":memory:",
                            execution_id="EX001")
        job.run(total_pnl=1500.0, trades_summary="買入 2330 1張")
        mock_save.assert_called_once()

    @patch("main.save_daily_summary")
    def test_post_market_summary_has_correct_pnl(self, mock_save):
        from main import PostMarketJob
        job = PostMarketJob(monitor=MagicMock(), db_path=":memory:",
                            execution_id="EX001")
        job.run(total_pnl=2000.0, trades_summary="test")
        row = mock_save.call_args[0][0]
        assert row.total_pnl == pytest.approx(2000.0)

    @patch("main.save_daily_summary")
    def test_post_market_no_monitor_does_not_raise(self, mock_save):
        from main import PostMarketJob
        job = PostMarketJob(monitor=None, db_path=":memory:",
                            execution_id="EX001")
        job.run(total_pnl=0.0, trades_summary="")  # should not raise
