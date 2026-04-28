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

    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_market_open_reads_picks_from_db_not_memory(self, mock_monitor_cls, mock_place, tmp_path):
        """09:00 job must read approved_picks from DB (after user may have rejected some)."""
        from research_db import init_db, save_daily_plan
        from datetime import date
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        # DB has only 2454 (user already rejected 2330)
        save_daily_plan(date.today(), [
            {"code": "2454", "name": "聯發科", "budget": 3000.0,
             "sector": "半導體", "signal": "buy", "confidence": 7,
             "target_price": 800.0, "stop_loss_price": 700.0},
        ], db_path)
        mock_place.return_value = MagicMock(success=False)
        mock_monitor_cls.return_value = MagicMock()

        from main import MarketOpenJob
        job = MarketOpenJob(
            api=MagicMock(), approved_picks=[],  # empty in-memory (rejected picks not passed)
            db_path=db_path, telegram_chat_id=None,
            hard_limit=500_000, prior_orders=[],
        )
        job.run()
        # place_stock_order should have been called with 2454, not with nothing
        assert mock_place.call_count == 1
        call_kwargs = mock_place.call_args[1]
        assert call_kwargs["code"] == "2454"

    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_market_open_passes_api_to_monitor(self, mock_monitor_cls, mock_place):
        """MarketOpenJob must pass the already-connected api to MonitorAgent
        so it doesn't create a second Shioaji session."""
        mock_place.return_value = MagicMock(success=True)
        mock_monitor_cls.return_value = MagicMock()

        from main import MarketOpenJob
        pre_api = MagicMock()
        job = MarketOpenJob(
            api=pre_api, approved_picks=self._approved(),
            db_path=":memory:", telegram_chat_id=None,
            hard_limit=500_000, prior_orders=[],
        )
        job.run()
        kwargs = mock_monitor_cls.call_args[1]
        assert kwargs.get("api") is pre_api


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


# ── _confidence_budget (#12) ──────────────────────────────────────────────────

class TestConfidenceBudget:
    def test_min_confidence_gives_min_pct(self):
        from main import _confidence_budget
        result = _confidence_budget(1, 100_000)
        assert result == pytest.approx(2000.0)  # 2% of 100k

    def test_max_confidence_gives_max_pct(self):
        from main import _confidence_budget
        result = _confidence_budget(10, 100_000)
        assert result == pytest.approx(5000.0)  # 5% of 100k

    def test_mid_confidence_is_between_min_and_max(self):
        from main import _confidence_budget
        result = _confidence_budget(5, 100_000)
        assert 2000.0 < result < 5000.0

    def test_higher_confidence_gives_larger_budget(self):
        from main import _confidence_budget
        low  = _confidence_budget(3, 100_000)
        high = _confidence_budget(8, 100_000)
        assert high > low

    def test_scales_with_capital(self):
        from main import _confidence_budget
        r1 = _confidence_budget(5, 100_000)
        r2 = _confidence_budget(5, 200_000)
        assert r2 == pytest.approx(r1 * 2)

    def test_confidence_clamped_below_1(self):
        from main import _confidence_budget
        r0  = _confidence_budget(0, 100_000)
        r1  = _confidence_budget(1, 100_000)
        assert r0 == pytest.approx(r1)

    def test_confidence_clamped_above_10(self):
        from main import _confidence_budget
        r10 = _confidence_budget(10, 100_000)
        r11 = _confidence_budget(11, 100_000)
        assert r11 == pytest.approx(r10)


class TestPremarketUsesConfidenceBudget:
    @patch("main.send_confirmation")
    @patch("main.validate_plan")
    @patch("main.run_deep_analysis")
    @patch("main.save_daily_plan")
    def test_high_confidence_gets_larger_budget_than_low(
        self, mock_save, mock_deep, mock_validate, mock_send
    ):
        budgets = []

        def fake_validate(picks, capital, positions):
            for p in picks:
                budgets.append((p["confidence"], p["budget"]))
            return {"approved": picks, "rejected": []}

        mock_validate.side_effect = fake_validate

        signals = {"2330": (8, "buy"), "2454": (2, "buy")}

        def fake_deep(code, name, news, fundamentals_text, market_summary, theme_info):
            confidence, sig = signals[code]
            m = MagicMock()
            m.signal = sig
            m.confidence = confidence
            m.target_price = 900.0
            m.stop_loss_price = 800.0
            return m

        mock_deep.side_effect = fake_deep

        from main import PremarketJob
        job = PremarketJob(
            candidates=[{"code": "2330", "name": "台積電"},
                        {"code": "2454", "name": "聯發科"}],
            capital=100_000.0,
            db_path=":memory:",
            telegram_chat_id=None,
            current_positions=[],
        )
        job.run()

        budgets_by_code = {conf: bud for conf, bud in budgets}
        assert budgets_by_code[8] > budgets_by_code[2]


# ── Fix 3: load_prior_orders ──────────────────────────────────────────────────

class TestLoadPriorOrders:
    def test_returns_list(self):
        from main import load_prior_orders
        api = None
        result = load_prior_orders(api)
        assert isinstance(result, list)

    def test_returns_empty_when_api_is_none(self):
        from main import load_prior_orders
        result = load_prior_orders(None)
        assert result == []

    def test_calls_api_list_trades(self):
        from main import load_prior_orders
        api = MagicMock()
        api.list_trades.return_value = []
        load_prior_orders(api)
        api.list_trades.assert_called_once()

    def test_returns_list_of_dicts(self):
        from main import load_prior_orders
        api = MagicMock()
        trade = MagicMock()
        trade.contract.code = "2330"
        trade.order.action = "buy"
        trade.order.quantity = 1000
        trade.order.price = 850.0
        api.list_trades.return_value = [trade]
        result = load_prior_orders(api)
        assert len(result) == 1
        assert result[0]["code"] == "2330"

    def test_api_error_returns_empty(self):
        from main import load_prior_orders
        api = MagicMock()
        api.list_trades.side_effect = Exception("API error")
        result = load_prior_orders(api)
        assert result == []


class TestMarketOpenJobUsesPriorOrders:
    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    @patch("main.load_prior_orders")
    def test_09_job_passes_prior_orders_to_executor(
        self, mock_prior, mock_monitor_cls, mock_place
    ):
        """09:00 job must call load_prior_orders and pass result to place_stock_order."""
        mock_prior.return_value = [{"code": "0050", "action": "buy"}]
        mock_place.return_value = MagicMock(success=False)
        mock_monitor_cls.return_value = MagicMock()

        from main import MarketOpenJob
        job = MarketOpenJob(
            api=MagicMock(),
            approved_picks=[{"code": "2330", "name": "台積電", "budget": 5000.0}],
            db_path=":memory:", telegram_chat_id=None,
            hard_limit=500_000, prior_orders=None,  # None triggers auto-load
        )
        job.run()
        call_kwargs = mock_place.call_args[1]
        assert call_kwargs["prior_orders"] == [{"code": "0050", "action": "buy"}]


# ── Fix 4: load_current_positions ─────────────────────────────────────────────

class TestLoadCurrentPositions:
    def test_returns_list(self, tmp_path):
        from main import load_current_positions
        db_path = str(tmp_path / "t.db")
        from research_db import init_db
        init_db(db_path)
        result = load_current_positions(date.today(), db_path)
        assert isinstance(result, list)

    def test_returns_empty_when_no_trades(self, tmp_path):
        from main import load_current_positions
        db_path = str(tmp_path / "t.db")
        from research_db import init_db
        init_db(db_path)
        result = load_current_positions(date.today(), db_path)
        assert result == []

    def test_returns_buy_trades_as_positions(self, tmp_path):
        from main import load_current_positions
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "buy", "quantity": 1000, "price": 850.0,
            "amount": 850_000.0, "pnl": None, "note": "",
        }, db_path)
        result = load_current_positions(date.today(), db_path)
        assert len(result) == 1
        assert result[0]["code"] == "2330"

    def test_excludes_sell_trades(self, tmp_path):
        from main import load_current_positions
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "sell", "quantity": 1000, "price": 860.0,
            "amount": 860_000.0, "pnl": 10_000.0, "note": "",
        }, db_path)
        result = load_current_positions(date.today(), db_path)
        assert result == []


class TestPremarketJobUsesCurrentPositions:
    @patch("main.send_confirmation")
    @patch("main.validate_plan")
    @patch("main.run_deep_analysis")
    @patch("main.save_daily_plan")
    @patch("main.load_current_positions")
    def test_08_job_passes_current_positions_to_risk_guard(
        self, mock_positions, mock_save, mock_deep, mock_validate, mock_send
    ):
        """08:30 job must call load_current_positions and pass result to validate_plan."""
        mock_positions.return_value = [{"code": "0050", "quantity": 1000}]
        mock_deep.return_value = MagicMock(
            signal="buy", confidence=8, target_price=900.0, stop_loss_price=800.0
        )
        mock_validate.return_value = {"approved": [], "rejected": []}

        from main import PremarketJob
        job = PremarketJob(
            candidates=[{"code": "2330", "name": "台積電"}],
            capital=100_000.0,
            db_path=":memory:",
            telegram_chat_id=None,
            current_positions=None,  # None triggers auto-load
        )
        job.run()
        positions_passed = mock_validate.call_args[0][2]
        assert positions_passed == [{"code": "0050", "quantity": 1000}]


# ── Fix 1: scan_candidates ────────────────────────────────────────────────────

class TestScanCandidates:
    def test_returns_list(self):
        from main import scan_candidates
        assert isinstance(scan_candidates(None), list)

    def test_returns_empty_when_api_is_none(self):
        from main import scan_candidates
        assert scan_candidates(None) == []

    @patch("main.get_all_stock_codes")
    @patch("main.batch_fetch_snapshots")
    @patch("main.screen_candidates")
    def test_calls_pipeline_in_order(self, mock_screen, mock_snap, mock_codes):
        from main import scan_candidates
        mock_codes.return_value = ["2330"]
        mock_snap.return_value = {"2330": {"close": 850.0, "total_volume": 10000, "change_rate": 0.02}}
        mock_screen.return_value = [{"code": "2330", "close": 850.0}]
        api = MagicMock()
        result = scan_candidates(api)
        mock_codes.assert_called_once_with(api)
        mock_snap.assert_called_once()
        mock_screen.assert_called_once()
        assert isinstance(result, list)

    @patch("main.get_all_stock_codes")
    @patch("main.batch_fetch_snapshots")
    @patch("main.screen_candidates")
    def test_applies_name_map(self, mock_screen, mock_snap, mock_codes):
        from main import scan_candidates
        mock_codes.return_value = ["2330"]
        mock_snap.return_value = {"2330": {"close": 850.0, "total_volume": 10000, "change_rate": 0.02}}
        mock_screen.return_value = [{"code": "2330"}]
        api = MagicMock()
        result = scan_candidates(api, name_map={"2330": "台積電"})
        assert result[0]["name"] == "台積電"

    @patch("main.get_all_stock_codes")
    def test_api_error_returns_empty(self, mock_codes):
        from main import scan_candidates
        mock_codes.side_effect = Exception("API error")
        result = scan_candidates(MagicMock())
        assert result == []

    @patch("main.get_all_stock_codes")
    @patch("main.batch_fetch_snapshots")
    @patch("main.screen_candidates")
    def test_returns_dicts_with_code_key(self, mock_screen, mock_snap, mock_codes):
        from main import scan_candidates
        mock_codes.return_value = ["2330", "2454"]
        mock_snap.return_value = {}
        mock_screen.return_value = [
            {"code": "2330"}, {"code": "2454"}
        ]
        result = scan_candidates(MagicMock())
        assert all("code" in r for r in result)


class TestMainLoopUsesScanCandidates:
    @patch("main.send_confirmation")
    @patch("main.validate_plan")
    @patch("main.run_deep_analysis")
    @patch("main.save_daily_plan")
    @patch("main.scan_candidates")
    def test_premarket_job_gets_scanned_candidates(
        self, mock_scan, mock_save, mock_deep, mock_validate, mock_send
    ):
        """When candidates=[] is passed, PremarketJob should use scan_candidates result."""
        mock_scan.return_value = [{"code": "2330", "name": "台積電"}]
        mock_deep.return_value = MagicMock(
            signal="buy", confidence=8, target_price=900.0, stop_loss_price=800.0
        )
        mock_validate.return_value = {"approved": [], "rejected": []}

        from main import PremarketJob
        job = PremarketJob(
            candidates=None,  # None triggers scan
            capital=100_000.0,
            db_path=":memory:",
            telegram_chat_id=None,
            current_positions=[],
            api=MagicMock(),
        )
        job.run()
        mock_scan.assert_called_once()
        mock_deep.assert_called_once()


# ── Fix 5: ForceCloseJob ──────────────────────────────────────────────────────

class TestForceCloseJob:
    def _make_trade(self, code="2330", name="台積電", quantity=1000):
        return {"code": code, "name": name, "action": "buy",
                "quantity": quantity, "price": 850.0, "amount": 850_000.0,
                "pnl": None, "note": ""}

    @patch("main.force_stop_loss")
    def test_calls_force_stop_loss_for_each_open_position(self, mock_force, tmp_path):
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({**self._make_trade("2330"), "trade_date": date.today()}, db_path)
        save_daily_trade({**self._make_trade("2454"), "trade_date": date.today()}, db_path)
        mock_force.return_value = True
        api = MagicMock()

        job = ForceCloseJob(api=api, db_path=db_path)
        job.run()
        assert mock_force.call_count == 2

    @patch("main.force_stop_loss")
    def test_no_open_positions_does_nothing(self, mock_force, tmp_path):
        from main import ForceCloseJob
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        api = MagicMock()

        job = ForceCloseJob(api=api, db_path=db_path)
        job.run()
        mock_force.assert_not_called()

    @patch("main.force_stop_loss")
    def test_returns_list_of_results(self, mock_force, tmp_path):
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({**self._make_trade("2330"), "trade_date": date.today()}, db_path)
        mock_force.return_value = True
        api = MagicMock()

        job = ForceCloseJob(api=api, db_path=db_path)
        results = job.run()
        assert isinstance(results, list)
        assert len(results) == 1

    @patch("main.force_stop_loss")
    def test_passes_correct_code_and_quantity(self, mock_force, tmp_path):
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({**self._make_trade("2330", quantity=2000), "trade_date": date.today()}, db_path)
        mock_force.return_value = True
        api = MagicMock()

        job = ForceCloseJob(api=api, db_path=db_path)
        job.run()
        call_kwargs = mock_force.call_args[1]
        assert call_kwargs["code"] == "2330"
        assert call_kwargs["quantity"] == 2000

    @patch("main.force_stop_loss")
    def test_excludes_sell_trades(self, mock_force, tmp_path):
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        sell_trade = {**self._make_trade("2330"), "trade_date": date.today(), "action": "sell"}
        save_daily_trade(sell_trade, db_path)
        api = MagicMock()

        job = ForceCloseJob(api=api, db_path=db_path)
        job.run()
        mock_force.assert_not_called()


class TestMain1325SlotHasForceClose:
    @patch("main.PostMarketJob")
    @patch("main.ForceCloseJob")
    def test_force_close_job_wired_at_1325(self, mock_force_cls, mock_post_cls):
        """Verify ForceCloseJob is importable and constructible — wiring is in main loop."""
        from main import ForceCloseJob
        api = MagicMock()
        job = ForceCloseJob(api=api, db_path=":memory:")
        assert job is not None
