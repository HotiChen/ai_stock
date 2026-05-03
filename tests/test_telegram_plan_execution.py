from __future__ import annotations

import json
import tempfile
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from strategy_planner import PlanSet, StrategyPlan, StockPick
from portfolio import SimulatedPortfolio

TODAY = date(2026, 5, 3)

# ── helpers ───────────────────────────────────────────────────────────────────

def _pick(code, name, action, qty=1, is_frac=False, shares=0,
          switch_from="", reason="測試") -> StockPick:
    return StockPick(
        code=code, name=name, action=action,
        quantity=qty, hold_days=3,
        expected_return_pct=5.0, reason=reason, confidence=7,
        is_fractional=is_frac, shares=shares,
        switch_from=switch_from,
    )

def _plan(plan_type, picks) -> StrategyPlan:
    return StrategyPlan(
        plan_type=plan_type, description="test",
        picks=picks, capital_deployed_pct=0.5,
        expected_return_pct=5.0, risk_note="",
    )

def _planset(agg=None, bal=None, con=None) -> PlanSet:
    return PlanSet(
        aggressive=_plan("aggressive", agg or []),
        balanced=_plan("balanced", bal or []),
        conservative=_plan("conservative", con or []),
        generated_at=TODAY,
    )

def _portfolio(capital=500_000.0) -> SimulatedPortfolio:
    return SimulatedPortfolio(initial_capital=capital)


# ── imports under test ────────────────────────────────────────────────────────

from telegram_bot import (
    save_pending_planset,
    load_pending_planset,
    execute_plan,
    handle_select_plan,
)


# ── save / load pending planset ───────────────────────────────────────────────

class TestPendingPlanset:
    def test_save_and_load_roundtrip(self, tmp_path):
        ps = _planset(agg=[_pick("2330", "台積電", "open")])
        save_pending_planset(ps, path=str(tmp_path / "pending.json"))
        loaded = load_pending_planset(path=str(tmp_path / "pending.json"))
        assert loaded is not None
        assert len(loaded.aggressive.picks) == 1
        assert loaded.aggressive.picks[0].code == "2330"

    def test_load_returns_none_if_missing(self, tmp_path):
        result = load_pending_planset(path=str(tmp_path / "not_exist.json"))
        assert result is None

    def test_saves_all_three_plans(self, tmp_path):
        ps = _planset(
            agg=[_pick("2330", "台積電", "open")],
            bal=[_pick("2454", "聯發科", "open")],
            con=[],
        )
        save_pending_planset(ps, path=str(tmp_path / "p.json"))
        loaded = load_pending_planset(path=str(tmp_path / "p.json"))
        assert loaded.balanced.picks[0].code == "2454"
        assert loaded.conservative.picks == []

    def test_overwrite_existing(self, tmp_path):
        p = str(tmp_path / "p.json")
        save_pending_planset(_planset(agg=[_pick("2330", "A", "open")]), path=p)
        save_pending_planset(_planset(agg=[_pick("2454", "B", "open")]), path=p)
        loaded = load_pending_planset(path=p)
        assert loaded.aggressive.picks[0].code == "2454"


# ── execute_plan ──────────────────────────────────────────────────────────────

class TestExecutePlan:
    @patch("telegram_bot._fetch_stock_price", return_value=850.0)
    def test_open_creates_position(self, mock_price):
        port = _portfolio()
        plan = _plan("aggressive", [_pick("2330", "台積電", "open", qty=1)])
        execute_plan(plan, port, TODAY)
        assert port.get_position("2330") is not None

    @patch("telegram_bot._fetch_stock_price", return_value=850.0)
    def test_open_deducts_cash(self, mock_price):
        port = _portfolio(500_000.0)
        plan = _plan("aggressive", [_pick("2330", "台積電", "open", qty=1)])
        execute_plan(plan, port, TODAY)
        # 850 * 1000 = 850,000 → cash = 500,000 - 850,000 = -350,000 (simulation allows negative)
        assert port.get_available_capital() < 500_000.0

    @patch("telegram_bot._fetch_stock_price", return_value=850.0)
    def test_returns_log_messages(self, mock_price):
        port = _portfolio()
        plan = _plan("balanced", [_pick("2330", "台積電", "open", qty=1)])
        msgs = execute_plan(plan, port, TODAY)
        assert isinstance(msgs, list)
        assert len(msgs) >= 1

    @patch("telegram_bot._fetch_stock_price", return_value=850.0)
    def test_open_message_contains_code(self, mock_price):
        port = _portfolio()
        plan = _plan("balanced", [_pick("2330", "台積電", "open", qty=1)])
        msgs = execute_plan(plan, port, TODAY)
        assert any("2330" in m for m in msgs)

    @patch("telegram_bot._fetch_stock_price", return_value=0.0)
    def test_open_with_zero_price_still_creates_position(self, mock_price):
        """價格抓不到（0）時仍建倉（模擬模式）"""
        port = _portfolio()
        plan = _plan("balanced", [_pick("2330", "台積電", "open", qty=1)])
        execute_plan(plan, port, TODAY)
        assert port.get_position("2330") is not None

    @patch("telegram_bot._fetch_stock_price", return_value=500.0)
    def test_hold_does_not_open_position(self, mock_price):
        port = _portfolio()
        plan = _plan("balanced", [_pick("2330", "台積電", "hold")])
        execute_plan(plan, port, TODAY)
        assert port.get_position("2330") is None

    @patch("telegram_bot._fetch_stock_price", return_value=500.0)
    def test_hold_message_indicates_no_action(self, mock_price):
        port = _portfolio()
        plan = _plan("balanced", [_pick("2330", "台積電", "hold")])
        msgs = execute_plan(plan, port, TODAY)
        assert any("持倉" in m or "hold" in m.lower() or "不動" in m for m in msgs)

    @patch("telegram_bot._fetch_stock_price", return_value=520.0)
    def test_close_removes_position(self, mock_price):
        port = _portfolio()
        port.open_position("2454", "聯發科", quantity=1, price=500.0,
                           is_fractional=False, shares=0,
                           reason="test", entry_date=TODAY)
        plan = _plan("conservative", [_pick("2454", "聯發科", "close")])
        execute_plan(plan, port, TODAY)
        assert port.get_position("2454") is None

    @patch("telegram_bot._fetch_stock_price", return_value=520.0)
    def test_close_increases_cash(self, mock_price):
        port = _portfolio(200_000.0)
        port.open_position("2454", "聯發科", quantity=1, price=500.0,
                           is_fractional=False, shares=0,
                           reason="test", entry_date=TODAY)
        cash_before = port.get_available_capital()
        plan = _plan("conservative", [_pick("2454", "聯發科", "close")])
        execute_plan(plan, port, TODAY)
        assert port.get_available_capital() > cash_before

    @patch("telegram_bot._fetch_stock_price", return_value=520.0)
    def test_add_increases_quantity(self, mock_price):
        port = _portfolio()
        port.open_position("2454", "聯發科", quantity=1, price=500.0,
                           is_fractional=False, shares=0,
                           reason="test", entry_date=TODAY)
        plan = _plan("aggressive", [_pick("2454", "聯發科", "add", qty=1)])
        execute_plan(plan, port, TODAY)
        assert port.get_position("2454").quantity == 2

    @patch("telegram_bot._fetch_stock_price", return_value=520.0)
    def test_reduce_decreases_quantity(self, mock_price):
        port = _portfolio()
        port.open_position("2454", "聯發科", quantity=2, price=500.0,
                           is_fractional=False, shares=0,
                           reason="test", entry_date=TODAY)
        plan = _plan("conservative", [_pick("2454", "聯發科", "reduce", qty=1)])
        execute_plan(plan, port, TODAY)
        assert port.get_position("2454").quantity == 1

    @patch("telegram_bot._fetch_stock_price", side_effect=lambda code: 850.0 if code == "2330" else 520.0)
    def test_switch_closes_old_and_opens_new(self, mock_price):
        port = _portfolio()
        port.open_position("2454", "聯發科", quantity=1, price=500.0,
                           is_fractional=False, shares=0,
                           reason="test", entry_date=TODAY)
        pick = _pick("2330", "台積電", "switch", qty=1, switch_from="2454")
        plan = _plan("balanced", [pick])
        execute_plan(plan, port, TODAY)
        assert port.get_position("2454") is None   # 舊的平掉
        assert port.get_position("2330") is not None  # 新的建立

    @patch("telegram_bot._fetch_stock_price", return_value=850.0)
    def test_multiple_picks_all_executed(self, mock_price):
        port = _portfolio()
        plan = _plan("aggressive", [
            _pick("2330", "台積電", "open", qty=1),
            _pick("2454", "聯發科", "open", qty=1),
        ])
        execute_plan(plan, port, TODAY)
        assert port.get_position("2330") is not None
        assert port.get_position("2454") is not None

    @patch("telegram_bot._fetch_stock_price", return_value=850.0)
    def test_failed_pick_logged_not_raised(self, mock_price):
        """單一 pick 失敗不應導致整個執行中斷"""
        port = _portfolio()
        # close 不存在的倉位 → 應 log 失敗但繼續
        plan = _plan("aggressive", [
            _pick("9999", "不存在", "close"),
            _pick("2330", "台積電", "open", qty=1),
        ])
        msgs = execute_plan(plan, port, TODAY)
        assert port.get_position("2330") is not None  # 後面那個還是執行
        assert any("失敗" in m or "⚠️" in m for m in msgs)


# ── handle_select_plan with execution ────────────────────────────────────────

class TestHandleSelectPlanExecution:
    def _callback(self, plan_type: str) -> dict:
        return {
            "id": "cq1",
            "data": f"select_plan:{plan_type}",
            "message": {"chat": {"id": "999"}},
        }

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    @patch("telegram_bot.execute_plan", return_value=["🟢 2330 台積電 建倉 1張 @850.0"])
    @patch("telegram_bot.load_pending_planset")
    @patch("telegram_bot.SimulatedPortfolio")
    def test_executes_plan_when_planset_available(
        self, mock_port_cls, mock_load, mock_exec, mock_save, mock_send
    ):
        mock_load.return_value = _planset(
            bal=[_pick("2330", "台積電", "open")]
        )
        mock_port = MagicMock()
        mock_port_cls.load.return_value = mock_port

        handle_select_plan(self._callback("balanced"))
        assert mock_exec.called

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    @patch("telegram_bot.execute_plan", return_value=["🟢 2330 台積電 建倉 1張 @850.0"])
    @patch("telegram_bot.load_pending_planset")
    @patch("telegram_bot.SimulatedPortfolio")
    def test_confirmation_shows_executed_picks(
        self, mock_port_cls, mock_load, mock_exec, mock_save, mock_send
    ):
        mock_load.return_value = _planset(
            bal=[_pick("2330", "台積電", "open")]
        )
        mock_port_cls.load.return_value = MagicMock()
        handle_select_plan(self._callback("balanced"))
        text = mock_send.call_args[0][1]
        assert "2330" in text or "建倉" in text

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    @patch("telegram_bot.load_pending_planset", return_value=None)
    @patch("telegram_bot.SimulatedPortfolio")
    def test_no_planset_sends_warning(
        self, mock_port_cls, mock_load, mock_save, mock_send
    ):
        """沒有待執行的計劃時，送出警告而非崩潰"""
        handle_select_plan(self._callback("balanced"))
        text = mock_send.call_args[0][1]
        assert "找不到" in text or "無計劃" in text or "⚠️" in text

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    @patch("telegram_bot.execute_plan", return_value=["🟢 done"])
    @patch("telegram_bot.load_pending_planset")
    @patch("telegram_bot.SimulatedPortfolio")
    def test_portfolio_saved_after_execution(
        self, mock_port_cls, mock_load, mock_exec, mock_save, mock_send
    ):
        mock_load.return_value = _planset(
            agg=[_pick("2330", "台積電", "open")]
        )
        mock_port = MagicMock()
        mock_port_cls.load.return_value = mock_port
        handle_select_plan(self._callback("aggressive"))
        assert mock_port.save.called
