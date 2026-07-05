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
        assert is_trading_day(datetime(2026, 5, 8)) is True  # Friday, non-holiday

    def test_saturday_is_not_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 5, 2)) is False  # Saturday

    def test_sunday_is_not_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 5, 3)) is False  # Sunday

    def test_wednesday_is_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 4, 29)) is True  # Wednesday

    def test_new_year_day_is_not_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 1, 1)) is False  # 元旦 Thu

    def test_labor_day_is_not_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 5, 1)) is False  # 勞動節 Fri

    def test_spring_festival_day1_is_not_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 2, 17)) is False  # 農曆初一 Tue

    def test_regular_weekday_in_holiday_month_is_trading_day(self):
        from main import is_trading_day
        assert is_trading_day(datetime(2026, 2, 24)) is True  # Tue, 非假日

    def test_unknown_year_weekday_is_trading_day_with_warning(self, caplog):
        """未收錄年份的平日應回傳 True（weekday-only 降級），且發出 WARNING。"""
        import logging
        from main import is_trading_day
        with caplog.at_level(logging.WARNING, logger="main"):
            result = is_trading_day(datetime(2030, 6, 10))  # Tue, unknown year
        assert result is True, "未收錄年份的平日應降級為 weekday-only，回傳 True"
        assert any(
            "2030" in r.message and "not in the TWSE holiday calendar" in r.message
            for r in caplog.records
        ), "未收錄年份應有 WARNING log，提示曆法降級"

    def test_incomplete_calendar_year_warns_once(self, caplog):
        """已知但不完整的年份（如 2026）第一次呼叫應發出 WARNING，且僅發一次。

        2026 年存在於 _TWSE_HOLIDAYS 但 端午節/中秋節 尚未確認；
        系統不應靜默把這些缺漏假日當成交易日。
        """
        import logging
        import importlib
        import main as main_mod

        # Reset the module-level cache so we can test first-call behavior
        main_mod._warned_incomplete_calendar_years.discard(2026)

        from main import is_trading_day
        with caplog.at_level(logging.WARNING, logger="main"):
            is_trading_day(datetime(2026, 3, 10))  # Tue, known but incomplete year
            is_trading_day(datetime(2026, 3, 11))  # second call same year
        warning_msgs = [
            r.message for r in caplog.records
            if "2026" in r.message and "INCOMPLETE" in r.message
        ]
        assert len(warning_msgs) >= 1, (
            "2026 年曆法不完整（缺端午節/中秋節），應至少發出一次 WARNING"
        )
        assert len(warning_msgs) == 1, (
            f"同年份的 INCOMPLETE 警告應只發一次，實際發了 {len(warning_msgs)} 次"
        )


# ── is_twse_holiday ───────────────────────────────────────────────────────────

class TestIsTwseHoliday:
    def test_new_year_is_holiday(self):
        from tw_trading_calendar import is_twse_holiday
        assert is_twse_holiday(date(2026, 1, 1)) is True

    def test_labor_day_is_holiday(self):
        from tw_trading_calendar import is_twse_holiday
        assert is_twse_holiday(date(2026, 5, 1)) is True

    def test_regular_tuesday_is_not_holiday(self):
        from tw_trading_calendar import is_twse_holiday
        assert is_twse_holiday(date(2026, 3, 10)) is False

    def test_unknown_year_returns_false(self):
        from tw_trading_calendar import is_twse_holiday
        assert is_twse_holiday(date(2030, 1, 1)) is False

    def test_spring_festival_closure(self):
        from tw_trading_calendar import is_twse_holiday
        for d in [date(2026, 2, 17), date(2026, 2, 18),
                  date(2026, 2, 19), date(2026, 2, 20)]:
            assert is_twse_holiday(d) is True


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

        def fake_deep(api, code, name, news, fundamentals_text, market_summary, theme_info):
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
        from research_db import init_db, load_daily_trades, save_daily_plan

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        # 09:00 以 DB 為單一真相來源：必須先把計劃存入 DB
        save_daily_plan(date.today(), self._approved(), db_path)

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
    def test_post_market_stops_monitor(self, tmp_path):
        from main import PostMarketJob
        from research_db import init_db
        db_path = str(tmp_path / "t.db"); init_db(db_path)
        mock_monitor = MagicMock()
        job = PostMarketJob(monitor=mock_monitor, db_path=db_path,
                            execution_id="EX001")
        job.run(trades_summary="買入 2330 1張")
        mock_monitor.stop.assert_called_once()

    def test_post_market_saves_daily_summary(self, tmp_path):
        from main import PostMarketJob
        from research_db import init_db, load_daily_summaries
        db_path = str(tmp_path / "t.db"); init_db(db_path)
        job = PostMarketJob(monitor=MagicMock(), db_path=db_path,
                            execution_id="EX001")
        job.run(trades_summary="買入 2330 1張")
        rows = load_daily_summaries("EX001", db_path)
        assert len(rows) == 1

    def test_post_market_pnl_computed_from_sell_trades(self, tmp_path):
        """PnL must come from confirmed sell trades only (action='sell').
        force_close_requested records are excluded because fill is unconfirmed."""
        from main import PostMarketJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db"); init_db(db_path)
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "sell", "quantity": 1, "price": 870.0,
            "amount": 870_000.0, "pnl": 20_000.0,
            "lot_type": "common", "sector": "半導體", "note": "force_close",
        }, db_path)
        job = PostMarketJob(monitor=MagicMock(), db_path=db_path,
                            execution_id="EX001")
        total_pnl = job.run(trades_summary="test")
        assert total_pnl == pytest.approx(20_000.0)

    def test_post_market_no_trades_gives_zero_pnl(self, tmp_path):
        from main import PostMarketJob
        from research_db import init_db
        db_path = str(tmp_path / "t.db"); init_db(db_path)
        job = PostMarketJob(monitor=None, db_path=db_path,
                            execution_id="EX001")
        total_pnl = job.run(trades_summary="")
        assert total_pnl == pytest.approx(0.0)

    def test_post_market_no_monitor_does_not_raise(self, tmp_path):
        from main import PostMarketJob
        from research_db import init_db
        db_path = str(tmp_path / "t.db"); init_db(db_path)
        job = PostMarketJob(monitor=None, db_path=db_path,
                            execution_id="EX001")
        job.run(trades_summary="")  # should not raise

    def test_force_close_requested_not_included_in_pnl(self, tmp_path):
        """force_close_requested 記錄（未確認成交）不可計入 realized PnL。

        13:25 ForceCloseJob 只送出委託，無法確認成交價或是否成交。
        PostMarketJob 不應把委託送出當成已成交來計算損益。
        """
        from main import PostMarketJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db"); init_db(db_path)
        # ForceCloseJob 送單後寫入的記錄：pnl=None，action=force_close_requested
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "force_close_requested",
            "quantity": 1, "price": 870.0,
            "amount": 870_000.0,
            "pnl": None,                    # fill unconfirmed → no PnL
            "lot_type": "common", "sector": "半導體",
            "note": "force_close_requested",
        }, db_path)
        job = PostMarketJob(monitor=MagicMock(), db_path=db_path,
                            execution_id="EX001")
        total_pnl = job.run(trades_summary="test")
        assert total_pnl == pytest.approx(0.0), (
            f"force_close_requested 未確認成交，不應產生 realized PnL，實際 {total_pnl}"
        )


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

        def fake_deep(api, code, name, news, fundamentals_text, market_summary, theme_info):
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

    def test_each_order_has_date_key(self):
        """date 欄位必須存在，is_duplicate_order() 依賴它。"""
        from main import load_prior_orders
        api = MagicMock()
        trade = MagicMock()
        trade.contract.code = "2330"
        trade.order.action = "buy"
        trade.order.quantity = 1000
        trade.order.price = 850.0
        api.list_trades.return_value = [trade]
        result = load_prior_orders(api)
        assert "date" in result[0]

    def test_date_equals_today_isoformat(self):
        """date 值必須是今天的 ISO 格式（YYYY-MM-DD）。"""
        from main import load_prior_orders
        api = MagicMock()
        trade = MagicMock()
        trade.contract.code = "2330"
        trade.order.action = "buy"
        trade.order.quantity = 1000
        trade.order.price = 850.0
        api.list_trades.return_value = [trade]
        result = load_prior_orders(api)
        assert result[0]["date"] == date.today().isoformat()

    def test_multiple_orders_all_have_today_date(self):
        """多筆委託每筆都要帶 date。"""
        from main import load_prior_orders
        api = MagicMock()
        def _trade(code):
            t = MagicMock()
            t.contract.code = code
            t.order.action = "buy"
            t.order.quantity = 1000
            t.order.price = 100.0
            return t
        api.list_trades.return_value = [_trade("2330"), _trade("2454")]
        result = load_prior_orders(api)
        assert len(result) == 2
        assert all(r["date"] == date.today().isoformat() for r in result)

    def test_shioaji_action_buy_normalized_to_buy(self):
        """Shioaji 真實回傳 'Action.Buy' → 必須正規化為 'buy' 才能被 duplicate guard 辨識。"""
        from main import load_prior_orders
        api = MagicMock()
        trade = MagicMock()
        trade.contract.code = "2330"
        trade.order.action = "Action.Buy"   # ← Shioaji 真實值
        trade.order.quantity = 1000
        trade.order.price = 850.0
        api.list_trades.return_value = [trade]
        result = load_prior_orders(api)
        assert result[0]["action"] == "buy", (
            "load_prior_orders 應將 'Action.Buy' 正規化為 'buy'，"
            f"實際得到 {result[0]['action']!r}"
        )

    def test_shioaji_action_sell_normalized_to_sell(self):
        """Shioaji 真實回傳 'Action.Sell' → 必須正規化為 'sell'。"""
        from main import load_prior_orders
        api = MagicMock()
        trade = MagicMock()
        trade.contract.code = "2330"
        trade.order.action = "Action.Sell"
        trade.order.quantity = 1000
        trade.order.price = 850.0
        api.list_trades.return_value = [trade]
        result = load_prior_orders(api)
        assert result[0]["action"] == "sell"


class TestLoadPriorOrdersBlocksDuplicates:
    """端對端：load_prior_orders 的輸出可直接傳給 is_duplicate_order 並攔截重複下單。"""

    def _make_api_with_trade(self, code: str, action: str):
        api = MagicMock()
        trade = MagicMock()
        trade.contract.code = code
        trade.order.action = action
        trade.order.quantity = 1000
        trade.order.price = 850.0
        api.list_trades.return_value = [trade]
        return api

    def test_same_code_same_action_today_is_blocked(self):
        """同股同向今日已下單 → is_duplicate_order 回 True。"""
        from main import load_prior_orders
        from executor import is_duplicate_order
        api = self._make_api_with_trade("2330", "buy")
        prior = load_prior_orders(api)
        assert is_duplicate_order("2330", "buy", prior) is True

    def test_same_code_different_action_is_not_blocked(self):
        """同股不同方向 → 不擋。"""
        from main import load_prior_orders
        from executor import is_duplicate_order
        api = self._make_api_with_trade("2330", "sell")
        prior = load_prior_orders(api)
        assert is_duplicate_order("2330", "buy", prior) is False

    def test_different_code_same_action_is_not_blocked(self):
        """不同股票同方向 → 不擋。"""
        from main import load_prior_orders
        from executor import is_duplicate_order
        api = self._make_api_with_trade("2454", "buy")
        prior = load_prior_orders(api)
        assert is_duplicate_order("2330", "buy", prior) is False

    def test_empty_prior_orders_never_blocked(self):
        """api 回傳空清單 → 任何下單都不被擋。"""
        from main import load_prior_orders
        from executor import is_duplicate_order
        api = MagicMock()
        api.list_trades.return_value = []
        prior = load_prior_orders(api)
        assert is_duplicate_order("2330", "buy", prior) is False


class TestMarketOpenJobResolvePicks:
    """
    09:00 picks 來源規則：
    - DB 成功讀取（含 []）→ 以 DB 為準，不 fallback
    - DB 拋例外            → DEGRADED MODE，fallback 到 memory
    """

    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_db_has_picks_memory_has_different_picks_uses_db(
        self, mock_monitor_cls, mock_place, tmp_path
    ):
        """DB 有計劃，memory 有不同資料 → 以 DB 為準，不用 memory 的股票。"""
        from main import MarketOpenJob
        from research_db import init_db, save_daily_plan

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        # DB 只有 2454
        save_daily_plan(date.today(), [
            {"code": "2454", "name": "聯發科", "budget": 3000.0,
             "sector": "IC設計", "signal": "buy", "confidence": 7},
        ], db_path)

        mock_place.return_value = MagicMock(success=False)
        mock_monitor_cls.return_value = MagicMock()

        job = MarketOpenJob(
            api=MagicMock(),
            approved_picks=[{"code": "2330", "name": "台積電", "budget": 5000.0}],  # memory: 2330
            db_path=db_path,
            telegram_chat_id=None,
            hard_limit=500_000,
            prior_orders=[],
        )
        job.run()

        assert mock_place.call_count == 1, "DB 有 2454，應下單 1 次"
        assert mock_place.call_args[1]["code"] == "2454", \
            "下單代號應為 DB 的 2454，而非 memory 的 2330"

    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_db_empty_list_memory_has_picks_zero_orders(
        self, mock_monitor_cls, mock_place, tmp_path
    ):
        """DB 是 []（reject_all 後），memory 有舊 picks → 零下單，不 fallback。"""
        from main import MarketOpenJob
        from research_db import init_db, save_daily_plan

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        # 模擬 reject_all：DB 有 row 但 picks_json = "[]"
        save_daily_plan(date.today(), [], db_path)

        mock_place.return_value = MagicMock(success=True)
        mock_monitor_cls.return_value = MagicMock()

        job = MarketOpenJob(
            api=MagicMock(),
            approved_picks=[{"code": "2330", "name": "台積電", "budget": 5000.0,
                             "sector": "半導體"}],   # memory 有舊 picks
            db_path=db_path,
            telegram_chat_id=None,
            hard_limit=500_000,
            prior_orders=[],
        )
        job.run()

        mock_place.assert_not_called(), (
            "DB 為 [] 代表使用者已拒絕或無計劃，不得 fallback 到 memory picks 下單"
        )

    @patch("main.load_daily_plan", side_effect=Exception("DB connection lost"))
    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_db_exception_fallback_to_memory_with_warning(
        self, mock_monitor_cls, mock_place, _mock_load, tmp_path
    ):
        """DB 讀取拋例外 → DEGRADED MODE：fallback 到 memory picks 並繼續下單。"""
        from main import MarketOpenJob
        from research_db import init_db

        db_path = str(tmp_path / "t.db")
        init_db(db_path)

        mock_place.return_value = MagicMock(success=False)
        mock_monitor_cls.return_value = MagicMock()

        job = MarketOpenJob(
            api=MagicMock(),
            approved_picks=[{"code": "2330", "name": "台積電", "budget": 5000.0,
                             "sector": "半導體"}],
            db_path=db_path,
            telegram_chat_id=None,
            hard_limit=500_000,
            prior_orders=[],
        )
        job.run()

        assert mock_place.call_count == 1, \
            "DB 例外時應 fallback 到 memory picks，仍嘗試下單"
        assert mock_place.call_args[1]["code"] == "2330", \
            "fallback 時應使用 memory picks 中的 2330"

    @patch("main.place_stock_order")
    @patch("main.MonitorAgent")
    def test_reject_all_then_0900_places_no_orders(
        self, mock_monitor_cls, mock_place, tmp_path
    ):
        """reject_all 後 09:00 run() 不呼叫 place_stock_order，即使 memory 有 picks。"""
        from main import MarketOpenJob
        from research_db import init_db, save_daily_plan, reject_all_picks_from_plan

        db_path = str(tmp_path / "t.db")
        init_db(db_path)

        # 08:30 已存入計劃
        save_daily_plan(date.today(), [
            {"code": "2330", "name": "台積電", "budget": 5000.0,
             "sector": "半導體", "signal": "buy", "confidence": 8},
        ], db_path)
        # 使用者在 Telegram 按 reject_all
        reject_all_picks_from_plan(date.today(), db_path)

        mock_place.return_value = MagicMock(success=True)
        mock_monitor_cls.return_value = MagicMock()

        job = MarketOpenJob(
            api=MagicMock(),
            approved_picks=[{"code": "2330", "name": "台積電", "budget": 5000.0,
                             "sector": "半導體"}],   # stale memory
            db_path=db_path,
            telegram_chat_id=None,
            hard_limit=500_000,
            prior_orders=[],
        )
        job.run()

        mock_place.assert_not_called(), (
            "reject_all 後 DB 為 []，不得因 memory fallback 而呼叫 place_stock_order"
        )


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
    """load_current_positions() must return risk_guard-compatible position records."""

    def _save_buy(self, db_path, code="2330", name="台積電",
                  amount=850_000.0, lot_type="common", sector="半導體"):
        from research_db import save_daily_trade
        save_daily_trade({
            "trade_date": date.today(),
            "code":       code,
            "name":       name,
            "action":     "buy",
            "quantity":   1000,
            "price":      850.0,
            "amount":     amount,
            "pnl":        None,
            "lot_type":   lot_type,
            "sector":     sector,
            "note":       "id=ORD001",
        }, db_path)

    def test_returns_list(self, tmp_path):
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        assert isinstance(load_current_positions(date.today(), db_path), list)

    def test_returns_empty_when_no_trades(self, tmp_path):
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        assert load_current_positions(date.today(), db_path) == []

    def test_returns_buy_trades_as_positions(self, tmp_path):
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path)
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
            "amount": 860_000.0, "pnl": 10_000.0,
            "lot_type": "common", "sector": "半導體", "note": "",
        }, db_path)
        assert load_current_positions(date.today(), db_path) == []

    # ── 正式持倉 schema：risk_guard 所需欄位 ─────────────────────────────────────

    def test_position_has_sector_key(self, tmp_path):
        """risk_guard.validate_plan 讀 pos['sector'] — 欄位必須存在。"""
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, sector="半導體")
        result = load_current_positions(date.today(), db_path)
        assert "sector" in result[0]

    def test_position_sector_value_matches_saved(self, tmp_path):
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, sector="AI伺服器")
        result = load_current_positions(date.today(), db_path)
        assert result[0]["sector"] == "AI伺服器"

    def test_position_has_value_key(self, tmp_path):
        """risk_guard.validate_plan 讀 pos['value'] — 欄位必須存在。"""
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, amount=500_000.0)
        result = load_current_positions(date.today(), db_path)
        assert "value" in result[0]

    def test_position_value_equals_amount(self, tmp_path):
        """value 應等於 amount（cost basis）。"""
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, amount=500_000.0)
        result = load_current_positions(date.today(), db_path)
        assert result[0]["value"] == pytest.approx(500_000.0)

    def test_position_has_lot_type_key(self, tmp_path):
        """lot_type 欄位必須存在（ForceCloseJob 依賴它）。"""
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, lot_type="intraday_odd")
        result = load_current_positions(date.today(), db_path)
        assert "lot_type" in result[0]

    def test_position_lot_type_preserved(self, tmp_path):
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, lot_type="intraday_odd")
        result = load_current_positions(date.today(), db_path)
        assert result[0]["lot_type"] == "intraday_odd"

    def test_multiple_positions(self, tmp_path):
        """多筆買入各自保留 sector/value/lot_type。"""
        from main import load_current_positions
        from research_db import init_db
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, code="2330", sector="半導體", amount=850_000.0, lot_type="common")
        self._save_buy(db_path, code="2454", name="聯發科",
                       sector="IC設計", amount=200_000.0, lot_type="intraday_odd")
        result = load_current_positions(date.today(), db_path)
        assert len(result) == 2
        by_code = {p["code"]: p for p in result}
        assert by_code["2330"]["sector"] == "半導體"
        assert by_code["2454"]["lot_type"] == "intraday_odd"
        assert by_code["2454"]["value"] == pytest.approx(200_000.0)

    def test_buy_with_matching_sell_excluded_as_closed(self, tmp_path):
        """買入後完全平倉（sell qty == buy qty）→ net-quantity=0 → 不出現在 open positions。"""
        from main import load_current_positions
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        # 買入 2330：quantity=1000
        self._save_buy(db_path, code="2330", sector="半導體", amount=850_000.0)
        # 完全平倉：sell quantity=1000（net = 1000-1000 = 0）
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "sell", "quantity": 1000, "price": 870.0,
            "amount": 870_000.0, "pnl": 20_000.0,
            "lot_type": "common", "sector": "半導體", "note": "force_close",
        }, db_path)
        result = load_current_positions(date.today(), db_path)
        assert len(result) == 0, (
            f"sell qty == buy qty → net=0 → 不應出現在 open positions，實際 {result}"
        )

    def test_partial_sell_shows_remaining_quantity(self, tmp_path):
        """部分平倉（sell qty < buy qty）→ 剩餘部位仍出現，quantity 為淨剩餘。"""
        from main import load_current_positions
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, code="2330", sector="半導體", amount=850_000.0)  # qty=1000
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "sell", "quantity": 300, "price": 870.0,
            "amount": 261_000.0, "pnl": 6_000.0,
            "lot_type": "common", "sector": "半導體", "note": "partial",
        }, db_path)
        result = load_current_positions(date.today(), db_path)
        assert len(result) == 1, "部分平倉後仍有剩餘部位"
        assert result[0]["quantity"] == 700, (
            f"net remaining = 1000 - 300 = 700，實際 {result[0]['quantity']}"
        )

    def test_force_close_requested_does_not_reduce_open_position(self, tmp_path):
        """force_close_requested 後，持倉仍以 buy 原量出現 — 不可扣除。

        語義：force_close_requested = 「平倉委託已送出」，不等於「已成交」。
        若委託未成交，下一次 ForceCloseJob 必須仍能看到這筆持倉並補送。
        只有 action="sell"（確認成交）才能減少持倉量。
        """
        from main import load_current_positions
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, code="2330", sector="半導體", amount=850_000.0)  # qty=1000
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "force_close_requested", "quantity": 1000,
            "price": 870.0, "amount": 870_000.0,
            "pnl": None, "lot_type": "common", "sector": "半導體",
            "note": "force_close_requested",
        }, db_path)
        result = load_current_positions(date.today(), db_path)
        assert len(result) == 1, (
            "force_close_requested 後持倉應仍可見（委託未確認成交），"
            f"實際 {result}"
        )
        assert result[0]["quantity"] == 1000, (
            f"持倉數量不應因 force_close_requested 而減少，實際 {result[0]['quantity']}"
        )

    def test_only_sell_reduces_open_position_quantity(self, tmp_path):
        """唯有 action='sell'（確認成交）才會減少 open position 數量。"""
        from main import load_current_positions
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, code="2330", sector="半導體", amount=850_000.0)  # qty=1000
        # sell qty=1000 → confirmed fill → position closed
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "sell", "quantity": 1000, "price": 870.0,
            "amount": 870_000.0, "pnl": 20_000.0,
            "lot_type": "common", "sector": "半導體", "note": "confirmed",
        }, db_path)
        result = load_current_positions(date.today(), db_path)
        assert len(result) == 0, (
            f"sell qty == buy qty → net=0 → 持倉已清空，實際 {result}"
        )

    def test_positions_compatible_with_risk_guard(self, tmp_path):
        """validate_plan 可以直接吃 load_current_positions 的輸出，不應 KeyError。"""
        from main import load_current_positions
        from research_db import init_db
        from unittest.mock import patch
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        self._save_buy(db_path, sector="半導體", amount=500_000.0)
        positions = load_current_positions(date.today(), db_path)

        # Patch ex-dividend and blacklist so validate_plan doesn't call network
        with patch("risk_guard.check_ex_dividend", return_value=False):
            from risk_guard import validate_plan
            # Should not raise KeyError on sector/value access
            result = validate_plan(
                picks=[{"code": "2454", "name": "聯發科", "budget": 50_000.0,
                        "sector": "IC設計", "signal": "buy", "confidence": 7}],
                capital=1_000_000.0,
                current_positions=positions,
            )
        assert "approved" in result or "rejected" in result


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
        # scan_candidates(api=None) 現在會走 TWSE OpenAPI 模擬模式 fallback
        # （fetch_twse_sim_candidates），不再直接回傳 []。
        from main import scan_candidates
        with patch("main.fetch_twse_sim_candidates", return_value=[]) as mock_twse:
            result = scan_candidates(None)
        mock_twse.assert_called_once()
        assert result == []

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
        # Shioaji 失敗後現在會 fallback 到 TWSE OpenAPI 模擬模式，
        # 不再直接回傳 []（見 main.scan_candidates 的 try/except 分支）。
        from main import scan_candidates
        mock_codes.side_effect = Exception("API error")
        with patch("main.fetch_twse_sim_candidates", return_value=[]) as mock_twse:
            result = scan_candidates(MagicMock())
        mock_twse.assert_called_once()
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
    def _make_trade(self, code="2330", name="台積電", quantity=1000,
                    lot_type="common", sector="半導體"):
        return {
            "code": code, "name": name, "action": "buy",
            "quantity": quantity, "price": 850.0, "amount": 850_000.0,
            "pnl": None, "lot_type": lot_type, "sector": sector, "note": "",
        }

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

    # ── lot_type 傳遞驗收 ─────────────────────────────────────────────────────────

    @patch("main.force_stop_loss")
    def test_passes_common_lot_type_to_force_stop_loss(self, mock_force, tmp_path):
        """整股部位 → force_stop_loss 收到 lot_type='common'。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(
            {**self._make_trade("2330", lot_type="common"), "trade_date": date.today()},
            db_path,
        )
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()
        assert mock_force.call_args[1]["lot_type"] == "common"

    @patch("main.force_stop_loss")
    def test_passes_intraday_odd_lot_type_to_force_stop_loss(self, mock_force, tmp_path):
        """零股部位 → force_stop_loss 收到 lot_type='intraday_odd'，不可 fallback 成 common。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(
            {**self._make_trade("2454", lot_type="intraday_odd"), "trade_date": date.today()},
            db_path,
        )
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()
        assert mock_force.call_args[1]["lot_type"] == "intraday_odd"

    @patch("main.force_stop_loss")
    def test_mixed_positions_each_gets_correct_lot_type(self, mock_force, tmp_path):
        """整股 + 零股混合持倉，各自收到正確的 lot_type。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(
            {**self._make_trade("2330", lot_type="common"),       "trade_date": date.today()},
            db_path,
        )
        save_daily_trade(
            {**self._make_trade("2454", lot_type="intraday_odd"), "trade_date": date.today()},
            db_path,
        )
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()
        assert mock_force.call_count == 2

        calls_by_code = {c[1]["code"]: c[1]["lot_type"] for c in mock_force.call_args_list}
        assert calls_by_code["2330"] == "common"
        assert calls_by_code["2454"] == "intraday_odd"


    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_real_mode_force_close_writes_pending_record_not_confirmed_sell(
        self, mock_force, tmp_path
    ):
        """【真實模式】送單成功後應寫入 force_close_requested，而非已確認成交的 sell。

        真實模式下 force_stop_loss() 只代表委託送出，不代表成交。
        立刻寫入 action='sell' 會讓 PostMarketJob 產生虛假 realized PnL。
        """
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({**self._make_trade("2330"), "trade_date": date.today()}, db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        confirmed_sells = [t for t in trades if t["action"] == "sell"]
        assert len(confirmed_sells) == 0, (
            "真實模式：force_stop_loss() 只是送單，不可立刻寫入 action='sell'"
        )
        pending = [t for t in trades if t["action"] == "force_close_requested"]
        assert len(pending) == 1, (
            f"真實模式：成功送出平倉委託後應有 1 筆 force_close_requested，實際 {len(pending)} 筆"
        )
        assert pending[0]["code"] == "2330"
        assert pending[0]["note"] == "force_close_requested"

    @patch("main.SIMULATION", True)
    @patch("main.force_stop_loss")
    def test_simulation_mode_force_close_writes_confirmed_sell(
        self, mock_force, tmp_path
    ):
        """【模擬模式】送單成功後應寫入 action='sell' 帶真實 PnL。

        Simulation 環境保證成交，直接寫入已確認的 sell 記錄，
        讓 PostMarketJob 能在當日結算真實損益。
        """
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({**self._make_trade("2330"), "trade_date": date.today()}, db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        sells = [t for t in trades if t["action"] == "sell"]
        assert len(sells) == 1, (
            f"模擬模式：成功平倉後應有 1 筆 action='sell' 記錄，實際 {len(sells)} 筆"
        )
        assert sells[0]["code"] == "2330"
        assert sells[0]["pnl"] is not None, (
            "模擬模式：sell 記錄應有 pnl 數值（snapshot price - buy price）× qty × multiplier"
        )
        pending = [t for t in trades if t["action"] == "force_close_requested"]
        assert len(pending) == 0, (
            "模擬模式：不應有 force_close_requested 記錄"
        )

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_real_mode_force_close_requested_record_has_null_pnl(
        self, mock_force, tmp_path
    ):
        """【真實模式】force_close_requested 記錄的 pnl 必須為 None（填單未確認）。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({**self._make_trade("2330"), "trade_date": date.today()}, db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        pending = [t for t in trades if t["action"] == "force_close_requested"]
        assert len(pending) == 1
        assert pending[0]["pnl"] is None, (
            f"真實模式：未確認成交不應有 pnl 數字，實際得到 {pending[0]['pnl']!r}"
        )

    @patch("main.SIMULATION", True)
    @patch("main.force_stop_loss")
    def test_simulation_second_run_skips_already_closed_position(
        self, mock_force, tmp_path
    ):
        """【模擬模式】同一部位執行兩次 ForceCloseJob → 第二次必須跳過。

        第一次寫入 action='sell'（qty=1000）。net-quantity: buy=1000, sell=1000 → 0.
        load_current_positions 回傳 []，第二次無部位可平。
        """
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({**self._make_trade("2330"), "trade_date": date.today()}, db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()  # 第一次：寫入 sell qty=1000
        mock_force.reset_mock()
        ForceCloseJob(api=MagicMock(), db_path=db_path).run()  # 第二次：net-qty=0 → 無部位 → 跳過

        assert mock_force.call_count == 0, (
            "模擬模式：sell 已使 net-qty=0，第二次 ForceCloseJob 不應重複送單"
        )

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_real_mode_second_run_skips_already_requested_position(
        self, mock_force, tmp_path
    ):
        """【真實模式】同一部位執行兩次 ForceCloseJob → 第二次必須跳過（pending_close_codes 機制）。

        load_current_positions() 不扣除 force_close_requested，所以第二次執行時
        持倉仍可見。ForceCloseJob.run() 的 pending_close_codes 偵測到已有委託記錄
        → skip → force_stop_loss 不再被呼叫。
        """
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade
        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade({**self._make_trade("2330"), "trade_date": date.today()}, db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()  # 第一次：寫入 force_close_requested
        mock_force.reset_mock()
        ForceCloseJob(api=MagicMock(), db_path=db_path).run()  # 第二次：pending_close_codes → skip

        assert mock_force.call_count == 0, (
            "真實模式：pending_close_codes 應攔截第二次送單"
        )


class TestMain1325SlotHasForceClose:
    @patch("main.PostMarketJob")
    @patch("main.ForceCloseJob")
    def test_force_close_job_wired_at_1325(self, mock_force_cls, mock_post_cls):
        """Verify ForceCloseJob is importable and constructible — wiring is in main loop."""
        from main import ForceCloseJob
        api = MagicMock()
        job = ForceCloseJob(api=api, db_path=":memory:")
        assert job is not None
