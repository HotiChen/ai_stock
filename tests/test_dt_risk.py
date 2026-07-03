"""tests/test_dt_risk.py — 當日虧損熔斷（circuit breaker）.

涵蓋：
  - DaytradingConfig 新欄位 daily_max_loss / risk_per_trade_pct（含 env 對應）
  - dt_risk.get_today_dt_realized_pnl：加總今日當沖已實現損益（過濾規則）
  - dt_risk.check_circuit_breaker：門檻判斷
  - dt_risk 旗標持久化（is_circuit_breaker_active / set_circuit_breaker_flag /
    get_circuit_breaker_flag）：當日只觸發一次、跨日自動失效
  - main.py 接線：_maybe_trigger_circuit_breaker（全平倉 + 旗標 + Telegram）、
    _dt_poll_tick 節流點呼叫熔斷檢查、_auto_buy_dt_positions 熔斷時拒絕買單
  - telegram_bot._handle_dt_buy 熔斷時拒絕買單
"""
from __future__ import annotations

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest


# ── DaytradingConfig 新欄位 ───────────────────────────────────────────────────

class TestDaytradingConfigRiskFields:
    def test_defaults(self):
        from daytrading_config import DaytradingConfig
        cfg = DaytradingConfig()
        assert cfg.daily_max_loss == 3000.0
        assert cfg.risk_per_trade_pct == 1.0

    def test_env_overrides(self):
        with patch.dict(os.environ, {
            "DT_DAILY_MAX_LOSS": "5000",
            "DT_RISK_PER_TRADE_PCT": "2.5",
        }):
            from daytrading_config import load_daytrading_config
            cfg = load_daytrading_config()
        assert cfg.daily_max_loss == 5000.0
        assert cfg.risk_per_trade_pct == 2.5

    def test_defaults_when_no_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DT_DAILY_MAX_LOSS", None)
            os.environ.pop("DT_RISK_PER_TRADE_PCT", None)
            from daytrading_config import load_daytrading_config
            cfg = load_daytrading_config()
        assert cfg.daily_max_loss == 3000.0
        assert cfg.risk_per_trade_pct == 1.0


# ── get_today_dt_realized_pnl ─────────────────────────────────────────────────

def _seed_trade(db_path, **overrides):
    from research_db import save_daily_trade
    trade = {
        "trade_date": date.today(),
        "code": "2330",
        "name": "台積電",
        "action": "sell",
        "quantity": 1,
        "price": 95.0,
        "amount": 95000.0,
        "pnl": -1000.0,
        "lot_type": "common",
        "sector": "當沖",
        "note": "auto_exit",
        "exit_reason": "stop_loss",
    }
    trade.update(overrides)
    save_daily_trade(trade, db_path)


class TestGetTodayDtRealizedPnl:
    def test_sums_auto_exit_sells(self, tmp_path):
        from research_db import init_db
        from dt_risk import get_today_dt_realized_pnl
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, code="2330", pnl=-1000.0, note="auto_exit")
        _seed_trade(db_path, code="2454", pnl=500.0, note="auto_exit")
        assert get_today_dt_realized_pnl(db_path) == -500.0

    def test_sums_force_close_simulation_when_sector_dt(self, tmp_path):
        from research_db import init_db
        from dt_risk import get_today_dt_realized_pnl
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, code="2330", pnl=-2000.0,
                    note="force_close_simulation", sector="當沖")
        assert get_today_dt_realized_pnl(db_path) == -2000.0

    def test_ignores_force_close_simulation_for_wave_sector(self, tmp_path):
        """波段部位（sector 非「當沖」）的 force_close_simulation 不應計入 DT 熔斷。"""
        from research_db import init_db
        from dt_risk import get_today_dt_realized_pnl
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, code="2330", pnl=-9000.0,
                    note="force_close_simulation", sector="半導體")
        assert get_today_dt_realized_pnl(db_path) == 0.0

    def test_ignores_buy_actions(self, tmp_path):
        from research_db import init_db
        from dt_risk import get_today_dt_realized_pnl
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, code="2330", action="buy", pnl=None, note="daytrade_buy")
        assert get_today_dt_realized_pnl(db_path) == 0.0

    def test_ignores_null_pnl(self, tmp_path):
        """賣單記錄若 pnl 為 None 不計（例如 force_close_requested 未確認成交）。"""
        from research_db import init_db
        from dt_risk import get_today_dt_realized_pnl
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, code="2330", pnl=None, note="auto_exit")
        assert get_today_dt_realized_pnl(db_path) == 0.0

    def test_ignores_unrelated_note(self, tmp_path):
        """不屬於 DT 出場機制寫入的 sell 記錄（未知 note）不計入。"""
        from research_db import init_db
        from dt_risk import get_today_dt_realized_pnl
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, code="2330", pnl=-3000.0, note="manual_sell")
        assert get_today_dt_realized_pnl(db_path) == 0.0

    def test_no_trades_returns_zero(self, tmp_path):
        from research_db import init_db
        from dt_risk import get_today_dt_realized_pnl
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        assert get_today_dt_realized_pnl(db_path) == 0.0


# ── check_circuit_breaker ─────────────────────────────────────────────────────

class TestCheckCircuitBreaker:
    def test_below_threshold_not_triggered(self, tmp_path):
        from research_db import init_db
        from daytrading_config import DaytradingConfig
        from dt_risk import check_circuit_breaker
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, pnl=-1000.0)
        cfg = DaytradingConfig(daily_max_loss=3000.0)
        result = check_circuit_breaker(cfg, db_path)
        assert result.triggered is False
        assert result.realized_pnl == -1000.0

    def test_at_threshold_triggered(self, tmp_path):
        from research_db import init_db
        from daytrading_config import DaytradingConfig
        from dt_risk import check_circuit_breaker
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, pnl=-3000.0)
        cfg = DaytradingConfig(daily_max_loss=3000.0)
        result = check_circuit_breaker(cfg, db_path)
        assert result.triggered is True
        assert result.realized_pnl == -3000.0
        assert result.message

    def test_beyond_threshold_triggered(self, tmp_path):
        from research_db import init_db
        from daytrading_config import DaytradingConfig
        from dt_risk import check_circuit_breaker
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, pnl=-5000.0)
        cfg = DaytradingConfig(daily_max_loss=3000.0)
        result = check_circuit_breaker(cfg, db_path)
        assert result.triggered is True

    def test_profitable_day_not_triggered(self, tmp_path):
        from research_db import init_db
        from daytrading_config import DaytradingConfig
        from dt_risk import check_circuit_breaker
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        _seed_trade(db_path, pnl=8000.0)
        cfg = DaytradingConfig(daily_max_loss=3000.0)
        result = check_circuit_breaker(cfg, db_path)
        assert result.triggered is False


# ── 旗標持久化 ─────────────────────────────────────────────────────────────────

class TestCircuitBreakerFlag:
    def test_inactive_by_default(self, tmp_path):
        from dt_risk import is_circuit_breaker_active
        flag_path = str(tmp_path / "cb.json")
        assert is_circuit_breaker_active(path=flag_path) is False

    def test_active_after_set(self, tmp_path):
        from dt_risk import is_circuit_breaker_active, set_circuit_breaker_flag
        flag_path = str(tmp_path / "cb.json")
        set_circuit_breaker_flag(-3500.0, "熔斷觸發", path=flag_path)
        assert is_circuit_breaker_active(path=flag_path) is True

    def test_get_flag_returns_message_and_pnl(self, tmp_path):
        from dt_risk import get_circuit_breaker_flag, set_circuit_breaker_flag
        flag_path = str(tmp_path / "cb.json")
        set_circuit_breaker_flag(-4200.0, "已觸發熔斷", path=flag_path)
        flag = get_circuit_breaker_flag(path=flag_path)
        assert flag["realized_pnl"] == -4200.0
        assert flag["message"] == "已觸發熔斷"
        assert flag["triggered"] is True
        assert flag["date"] == date.today().isoformat()

    def test_flag_expires_next_day(self, tmp_path):
        """跨日旗標自動失效：檔案內日期非今日 → 視為未觸發。"""
        import json
        from dt_risk import is_circuit_breaker_active, get_circuit_breaker_flag
        flag_path = str(tmp_path / "cb.json")
        with open(flag_path, "w", encoding="utf-8") as f:
            json.dump({
                "date": "2020-01-01", "triggered": True,
                "realized_pnl": -9999.0, "message": "old",
            }, f)
        assert is_circuit_breaker_active(path=flag_path) is False
        assert get_circuit_breaker_flag(path=flag_path) == {}

    def test_missing_file_inactive(self, tmp_path):
        from dt_risk import is_circuit_breaker_active
        flag_path = str(tmp_path / "does_not_exist.json")
        assert is_circuit_breaker_active(path=flag_path) is False

    def test_corrupt_file_inactive(self, tmp_path):
        from dt_risk import is_circuit_breaker_active
        flag_path = str(tmp_path / "cb.json")
        with open(flag_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        assert is_circuit_breaker_active(path=flag_path) is False


# ── main.py 接線：_maybe_trigger_circuit_breaker ──────────────────────────────

def _seed_active_dt_position(path, code="2330", entry_price=100.0, quantity=1000):
    from daytrading_monitor import DaytradingPosition, save_daytrading_positions
    save_daytrading_positions([
        DaytradingPosition(
            code=code, name="台積電", entry_low=None, entry_high=None,
            target_price=None, stop_loss=90.0, dt_score=90, status="active",
            entry_price=entry_price, peak_price=entry_price,
            quantity=quantity, lot_type="common",
        ),
    ], path=path)


class TestMaybeTriggerCircuitBreaker:
    def test_triggers_force_close_and_flag_and_telegram(self, tmp_path, monkeypatch):
        import main
        import dt_risk
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import load_daytrading_positions
        from research_db import init_db

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        flag_path = str(tmp_path / "cb.json")
        init_db(db_path)
        _seed_trade(db_path, pnl=-5000.0)
        _seed_active_dt_position(pos_path)

        cfg = DaytradingConfig(daily_max_loss=3000.0)
        monkeypatch.setattr(dt_risk, "_FLAG_PATH", flag_path)

        with patch("main.force_stop_loss", return_value=True) as mock_sell, \
             patch("telegram_bot.send_text") as mock_send, \
             patch.object(main, "TELEGRAM_CHAT_ID", "999"):
            main._maybe_trigger_circuit_breaker(MagicMock(), cfg, dt_path=pos_path, db_path=db_path)

        assert mock_sell.called
        positions = {p.code: p for p in load_daytrading_positions(path=pos_path)}
        assert positions["2330"].status == "closed"
        assert dt_risk.is_circuit_breaker_active(path=flag_path) is True
        assert mock_send.called
        assert "熔斷" in mock_send.call_args[0][1]

    def test_does_not_trigger_below_threshold(self, tmp_path, monkeypatch):
        import main
        import dt_risk
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import load_daytrading_positions
        from research_db import init_db

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        flag_path = str(tmp_path / "cb.json")
        init_db(db_path)
        _seed_trade(db_path, pnl=-500.0)
        _seed_active_dt_position(pos_path)

        cfg = DaytradingConfig(daily_max_loss=3000.0)
        monkeypatch.setattr(dt_risk, "_FLAG_PATH", flag_path)

        with patch("main.force_stop_loss", return_value=True) as mock_sell:
            main._maybe_trigger_circuit_breaker(MagicMock(), cfg, dt_path=pos_path, db_path=db_path)

        assert not mock_sell.called
        assert dt_risk.is_circuit_breaker_active(path=flag_path) is False
        positions = {p.code: p for p in load_daytrading_positions(path=pos_path)}
        assert positions["2330"].status == "active"

    def test_only_triggers_once_per_day(self, tmp_path, monkeypatch):
        import main
        import dt_risk
        from daytrading_config import DaytradingConfig
        from research_db import init_db

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        flag_path = str(tmp_path / "cb.json")
        init_db(db_path)
        _seed_trade(db_path, pnl=-5000.0)
        _seed_active_dt_position(pos_path)

        cfg = DaytradingConfig(daily_max_loss=3000.0)
        monkeypatch.setattr(dt_risk, "_FLAG_PATH", flag_path)

        with patch("main.force_stop_loss", return_value=True) as mock_sell, \
             patch("telegram_bot.send_text") as mock_send:
            main._maybe_trigger_circuit_breaker(MagicMock(), cfg, dt_path=pos_path, db_path=db_path)
            first_sell_calls = mock_sell.call_count
            first_send_calls = mock_send.call_count
            # 第二輪：仍是虧損日，但旗標已存在 → 不應重複全平倉 / 重複告警
            main._maybe_trigger_circuit_breaker(MagicMock(), cfg, dt_path=pos_path, db_path=db_path)

        assert mock_sell.call_count == first_sell_calls
        assert mock_send.call_count == first_send_calls

    def test_no_active_positions_still_sets_flag(self, tmp_path, monkeypatch):
        """無 active 持倉也應正常設旗標（不因空清單而例外）。"""
        import main
        import dt_risk
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import save_daytrading_positions
        from research_db import init_db

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        flag_path = str(tmp_path / "cb.json")
        init_db(db_path)
        save_daytrading_positions([], path=pos_path)
        _seed_trade(db_path, pnl=-5000.0)

        cfg = DaytradingConfig(daily_max_loss=3000.0)
        monkeypatch.setattr(dt_risk, "_FLAG_PATH", flag_path)

        with patch("telegram_bot.send_text"):
            main._maybe_trigger_circuit_breaker(MagicMock(), cfg, dt_path=pos_path, db_path=db_path)

        assert dt_risk.is_circuit_breaker_active(path=flag_path) is True


# ── _dt_poll_tick 節流點呼叫熔斷檢查 ───────────────────────────────────────────

class TestDtPollTickCallsCircuitBreaker:
    def test_dt_poll_tick_invokes_circuit_breaker_check(self, tmp_path):
        import main
        pos_path = str(tmp_path / "pos.json")
        from daytrading_monitor import save_daytrading_positions
        save_daytrading_positions([], path=pos_path)

        from daytrading_config import DaytradingConfig
        cfg = DaytradingConfig(force_close_time="23:59")

        with patch("main._maybe_trigger_circuit_breaker") as mock_cb:
            main._dt_poll_tick(MagicMock(), cfg, dt_path=pos_path)
        assert mock_cb.called

    def test_circuit_breaker_check_failure_does_not_block_poll(self, tmp_path):
        """熔斷檢查本身失敗（例如 DB 不可用）不得阻擋既有輪詢出場路徑。"""
        import main
        pos_path = str(tmp_path / "pos.json")
        from daytrading_monitor import save_daytrading_positions
        save_daytrading_positions([], path=pos_path)
        from daytrading_config import DaytradingConfig
        cfg = DaytradingConfig(force_close_time="23:59")

        with patch("main._maybe_trigger_circuit_breaker", side_effect=RuntimeError("db down")):
            result = main._dt_poll_tick(MagicMock(), cfg, dt_path=pos_path)
        assert result == []


# ── _auto_buy_dt_positions 熔斷時拒絕買單 ─────────────────────────────────────

class TestAutoBuyRespectsCircuitBreaker:
    def test_skips_all_buys_when_breaker_active(self, tmp_path):
        import main
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions
        from research_db import init_db, load_daily_trades

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=90, status="watching"),
        ], path=pos_path)

        from daytrading_monitor import load_daytrading_positions
        watching = load_daytrading_positions(path=pos_path)
        cfg = DaytradingConfig(budget_per_stock=100_000.0)

        with patch("dt_risk.is_circuit_breaker_active", return_value=True), \
             patch("main.place_stock_order") as mock_place:
            main._auto_buy_dt_positions(MagicMock(), watching, cfg, dt_path=pos_path, db_path=db_path)

        assert not mock_place.called
        trades = load_daily_trades(date.today(), db_path)
        assert not any(t["code"] == "2330" for t in trades)

    def test_buys_normally_when_breaker_inactive(self, tmp_path):
        import main
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions, load_daytrading_positions
        from research_db import init_db, load_daily_trades
        from types import SimpleNamespace

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=90, status="watching"),
        ], path=pos_path)
        watching = load_daytrading_positions(path=pos_path)
        cfg = DaytradingConfig(budget_per_stock=100_000.0)

        ok_result = SimpleNamespace(
            success=True, code="2330", name="台積電", action="buy",
            quantity=1, price=100.0, amount=100_000.0,
            lot_type="common", order_id="ORD1", reason="",
        )

        with patch("dt_risk.is_circuit_breaker_active", return_value=False), \
             patch("main.place_stock_order", return_value=ok_result), \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0):
            main._auto_buy_dt_positions(MagicMock(), watching, cfg, dt_path=pos_path, db_path=db_path)

        trades = load_daily_trades(date.today(), db_path)
        assert any(t["code"] == "2330" and t["action"] == "buy" for t in trades)


# ── telegram_bot._handle_dt_buy 熔斷時拒絕買單 ────────────────────────────────

class TestTelegramDtBuyRespectsCircuitBreaker:
    def test_rejects_buy_when_breaker_active(self, tmp_path):
        import telegram_bot
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=90, status="watching"),
        ], path=pos_path)

        with patch("dt_risk.is_circuit_breaker_active", return_value=True), \
             patch("dt_risk.get_circuit_breaker_flag", return_value={"message": "已觸發熔斷"}), \
             patch("telegram_bot._get_sj_api") as mock_api, \
             patch("executor.place_stock_order") as mock_place, \
             patch("telegram_bot.send_text") as mock_send:
            telegram_bot._handle_dt_buy("999", "2330", dt_path=pos_path, db_path=db_path)

        assert not mock_place.called
        assert not mock_api.called
        assert mock_send.called
        assert "熔斷" in mock_send.call_args[0][1]

    def test_buys_normally_when_breaker_inactive(self, tmp_path):
        import telegram_bot
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions
        from research_db import init_db, load_daily_trades
        from types import SimpleNamespace

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=90, status="watching"),
        ], path=pos_path)

        ok_result = SimpleNamespace(
            success=True, code="2330", name="台積電", action="buy",
            quantity=1, price=100.0, amount=100_000.0,
            lot_type="common", order_id="ORD1", reason="",
        )

        with patch("dt_risk.is_circuit_breaker_active", return_value=False), \
             patch("telegram_bot._get_sj_api", return_value=MagicMock()), \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0), \
             patch("executor.place_stock_order", return_value=ok_result), \
             patch("telegram_bot.send_text"):
            telegram_bot._handle_dt_buy("999", "2330", dt_path=pos_path, db_path=db_path)

        trades = load_daily_trades(date.today(), db_path)
        assert any(t["code"] == "2330" and t["action"] == "buy" for t in trades)
