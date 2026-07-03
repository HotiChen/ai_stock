"""tests/test_dt_daily_trades.py — Task 2: 當沖買入寫入 daily_trades."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _ok_result(code="2330", name="台積電"):
    return SimpleNamespace(
        success=True, code=code, name=name, action="buy",
        quantity=1, price=100.0, amount=100_000.0,
        lot_type="common", order_id="ORD1", reason="",
    )


def _fail_result(code="2330", name="台積電"):
    return SimpleNamespace(
        success=False, code=code, name=name, action="buy",
        quantity=0, price=0.0, amount=0.0,
        lot_type="common", order_id=None, reason="rejected",
    )


def _seed_positions(path, code="2330", name="台積電"):
    from daytrading_monitor import DaytradingPosition, save_daytrading_positions
    save_daytrading_positions([
        DaytradingPosition(code=code, name=name, entry_low=None, entry_high=None,
                           target_price=None, stop_loss=None, dt_score=90, status="watching"),
    ], path=path)


class TestMainAutoBuyDailyTrade:
    def test_buy_success_writes_and_visible_in_positions(self, tmp_path):
        import main
        from research_db import init_db, load_daily_trades
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import load_daytrading_positions

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_positions(pos_path)

        watching = load_daytrading_positions(path=pos_path)
        cfg = DaytradingConfig(budget_per_stock=100_000.0)

        with patch("main.place_stock_order", return_value=_ok_result()), \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0):
            main._auto_buy_dt_positions(MagicMock(), watching, cfg,
                                        dt_path=pos_path, db_path=db_path)

        trades = load_daily_trades(date.today(), db_path)
        assert any(t["code"] == "2330" and t["action"] == "buy" for t in trades)
        # 13:25 ForceCloseJob 讀取來源
        positions = main.load_current_positions(date.today(), db_path)
        assert any(p["code"] == "2330" for p in positions)

    def test_buy_failure_does_not_write(self, tmp_path):
        import main
        from research_db import init_db, load_daily_trades
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import load_daytrading_positions

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_positions(pos_path)
        watching = load_daytrading_positions(path=pos_path)
        cfg = DaytradingConfig(budget_per_stock=100_000.0)

        with patch("main.place_stock_order", return_value=_fail_result()), \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0):
            main._auto_buy_dt_positions(MagicMock(), watching, cfg,
                                        dt_path=pos_path, db_path=db_path)

        trades = load_daily_trades(date.today(), db_path)
        assert not any(t["code"] == "2330" for t in trades)


class TestTelegramDtBuyDailyTrade:
    def test_buy_success_writes_daily_trade(self, tmp_path):
        import telegram_bot
        from research_db import init_db, load_daily_trades

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_positions(pos_path)

        with patch("telegram_bot._get_sj_api", return_value=MagicMock()), \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0), \
             patch("executor.place_stock_order", return_value=_ok_result()), \
             patch("telegram_bot.send_text"):
            telegram_bot._handle_dt_buy("999", "2330", dt_path=pos_path, db_path=db_path)

        trades = load_daily_trades(date.today(), db_path)
        assert any(t["code"] == "2330" and t["action"] == "buy" for t in trades)

    def test_buy_failure_does_not_write(self, tmp_path):
        import telegram_bot
        from research_db import init_db, load_daily_trades

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_positions(pos_path)

        with patch("telegram_bot._get_sj_api", return_value=MagicMock()), \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0), \
             patch("executor.place_stock_order", return_value=_fail_result()), \
             patch("telegram_bot.send_text"):
            telegram_bot._handle_dt_buy("999", "2330", dt_path=pos_path, db_path=db_path)

        trades = load_daily_trades(date.today(), db_path)
        assert not any(t["code"] == "2330" for t in trades)
