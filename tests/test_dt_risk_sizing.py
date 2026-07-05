"""tests/test_dt_risk_sizing.py — 風險額倉位法（risk-based position sizing）.

涵蓋：
  - executor.calc_risk_quantity 純函數：手算案例（整張/零股邊界、budget cap、
    hard limit cap、stop 缺失 fallback）
  - main.py _auto_buy_dt_positions 接線：用 risk sizing 覆蓋 budget
  - telegram_bot._handle_dt_buy 接線：用 risk sizing 覆蓋 budget，成功訊息附風險額
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ── calc_risk_quantity 純函數 ─────────────────────────────────────────────────

class TestCalcRiskQuantity:
    def test_basic_case_returns_shares_from_risk_amount(self):
        """total_budget=100000, risk_pct=1% → risk_amount=1000
        entry=100, stop=95 → per_share_risk=5 → shares=200（未觸及 cap）。"""
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=100_000, risk_pct=1.0,
            entry_price=100.0, stop_loss_price=95.0,
            budget_cap=1_000_000, hard_limit=1_000_000,
        )
        assert qty == 200
        assert reason == ""

    def test_floors_fractional_shares(self):
        """risk_amount=1000, per_share_risk=3 → 333.33 → 向下取整 333 股。"""
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=100_000, risk_pct=1.0,
            entry_price=100.0, stop_loss_price=97.0,
            budget_cap=1_000_000, hard_limit=1_000_000,
        )
        assert qty == 333

    def test_stop_loss_none_falls_back(self):
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=100_000, risk_pct=1.0,
            entry_price=100.0, stop_loss_price=None,
            budget_cap=30_000, hard_limit=150_000,
        )
        assert qty == 0
        assert reason

    def test_entry_price_none_falls_back(self):
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=100_000, risk_pct=1.0,
            entry_price=None, stop_loss_price=95.0,
            budget_cap=30_000, hard_limit=150_000,
        )
        assert qty == 0
        assert reason

    def test_stop_loss_above_entry_falls_back(self):
        """停損價高於（或等於）進場價 → 每股風險 <= 0，無意義，退回舊法。"""
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=100_000, risk_pct=1.0,
            entry_price=100.0, stop_loss_price=105.0,
            budget_cap=30_000, hard_limit=150_000,
        )
        assert qty == 0
        assert reason

    def test_stop_loss_equal_entry_falls_back(self):
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=100_000, risk_pct=1.0,
            entry_price=100.0, stop_loss_price=100.0,
            budget_cap=30_000, hard_limit=150_000,
        )
        assert qty == 0
        assert reason

    def test_zero_shares_from_tiny_risk_amount_falls_back(self):
        """risk_amount 太小，連 1 股都買不起 → 0 股 + fallback 說明。"""
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=100, risk_pct=1.0,      # risk_amount = 1 元
            entry_price=100.0, stop_loss_price=50.0,   # per_share_risk=50
            budget_cap=30_000, hard_limit=150_000,
        )
        assert qty == 0
        assert reason

    def test_budget_cap_limits_shares_odd_lot(self):
        """風險額算出的股數金額超過 budget_cap，且 cap 金額不足 1 張 → 零股上限。
        entry=850, budget_cap=10000 → 10000/850=11 股（零股）。"""
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=10_000_000, risk_pct=5.0,   # risk_amount=500000（極大，必觸頂）
            entry_price=850.0, stop_loss_price=800.0,  # per_share_risk=50
            budget_cap=10_000, hard_limit=1_000_000,
        )
        # 未 cap 前 shares = 500000/50 = 10000 股，金額遠超 10000 元 cap
        assert qty == 11          # calc_quantity(10000, 850, "intraday_odd") = 11
        assert "上限" in reason

    def test_budget_cap_limits_shares_common_lot(self):
        """cap 金額足夠買到整張時，回傳的股數應是張數換算的股數（1 張=1000 股）。"""
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=10_000_000, risk_pct=5.0,
            entry_price=50.0, stop_loss_price=45.0,   # per_share_risk=5
            budget_cap=120_000, hard_limit=1_000_000,
        )
        # 未 cap shares = 500000/5=100000 股，金額遠超 cap。
        # cap=120000 → calc_lot_type(120000,50)="common"，
        # calc_quantity(120000,50,"common")=2 張 → 2000 股。
        assert qty == 2000
        assert "上限" in reason

    def test_hard_limit_is_stricter_than_budget_cap(self):
        """hard_limit 比 budget_cap 更嚴格時，取更小者。"""
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=10_000_000, risk_pct=5.0,
            entry_price=50.0, stop_loss_price=45.0,
            budget_cap=120_000, hard_limit=10_000,   # hard_limit 更嚴格
        )
        # amount_cap = min(120000, 10000) = 10000
        # calc_lot_type(10000, 50): one_lot_cost=50000 > 10000 → intraday_odd
        # calc_quantity(10000, 50, intraday_odd) = 200 股
        assert qty == 200
        assert "上限" in reason

    def test_zero_entry_price_falls_back(self):
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=100_000, risk_pct=1.0,
            entry_price=0.0, stop_loss_price=-1.0,
            budget_cap=30_000, hard_limit=150_000,
        )
        assert qty == 0
        assert reason

    def test_realistic_dt_config_case(self):
        """真實情境：CAPITAL=100000, risk_pct=1%（風險額=1000），
        entry=101.5, stop=98.5（每股風險=3），budget_cap=30000（dt 預設）。"""
        from executor import calc_risk_quantity
        qty, reason = calc_risk_quantity(
            total_budget=100_000, risk_pct=1.0,
            entry_price=101.5, stop_loss_price=98.5,
            budget_cap=30_000, hard_limit=150_000,
        )
        # shares = 1000/3 = 333.33 → 333 股；金額=333*101.5=33799.5 > 30000 cap
        # → 依 cap 重算：calc_lot_type(30000,101.5): one_lot_cost=101500>30000 → odd
        # calc_quantity(30000,101.5,"intraday_odd") = 295 股
        assert qty == 295
        assert "上限" in reason


# ── main.py _auto_buy_dt_positions 接線 ───────────────────────────────────────

def _ok_result(code="2330", name="台積電", quantity=1, price=100.0):
    return SimpleNamespace(
        success=True, code=code, name=name, action="buy",
        quantity=quantity, price=price, amount=quantity * price * 1000,
        lot_type="common", order_id="ORD1", reason="",
    )


class TestAutoBuyUsesRiskSizing:
    def test_uses_risk_based_budget_when_stop_loss_present(self, tmp_path):
        import main
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions, load_daytrading_positions
        from research_db import init_db

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=95.0, dt_score=90, status="watching"),
        ], path=pos_path)
        watching = load_daytrading_positions(path=pos_path)
        cfg = DaytradingConfig(budget_per_stock=30_000.0, risk_per_trade_pct=1.0)

        with patch("main.place_stock_order", return_value=_ok_result()) as mock_place, \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0), \
             patch("main.CAPITAL", 100_000.0), \
             patch("dt_risk.is_circuit_breaker_active", return_value=False):
            main._auto_buy_dt_positions(MagicMock(), watching, cfg, dt_path=pos_path, db_path=db_path)

        assert mock_place.called
        _, kwargs = mock_place.call_args
        # risk_amount=1000, per_share_risk=5 → shares=200 → budget=200*100=20000
        assert kwargs["budget"] == 20_000.0

    def test_falls_back_to_fixed_budget_when_stop_loss_missing(self, tmp_path):
        import main
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions, load_daytrading_positions
        from research_db import init_db

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=90, status="watching"),
        ], path=pos_path)
        watching = load_daytrading_positions(path=pos_path)
        cfg = DaytradingConfig(budget_per_stock=30_000.0, risk_per_trade_pct=1.0)

        with patch("main.place_stock_order", return_value=_ok_result()) as mock_place, \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0), \
             patch("main.CAPITAL", 100_000.0), \
             patch("dt_risk.is_circuit_breaker_active", return_value=False):
            main._auto_buy_dt_positions(MagicMock(), watching, cfg, dt_path=pos_path, db_path=db_path)

        _, kwargs = mock_place.call_args
        assert kwargs["budget"] == 30_000.0   # 舊 budget 法


# ── telegram_bot._handle_dt_buy 接線 ──────────────────────────────────────────

class TestTelegramDtBuyUsesRiskSizing:
    def test_risk_based_budget_passed_and_message_includes_risk_info(self, tmp_path, monkeypatch):
        import telegram_bot
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions
        from research_db import init_db

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=95.0, dt_score=90, status="watching"),
        ], path=pos_path)

        monkeypatch.setenv("BUDGET", "100000")
        monkeypatch.setenv("ORDER_HARD_LIMIT", "150000")

        with patch("dt_risk.is_circuit_breaker_active", return_value=False), \
             patch("telegram_bot._get_sj_api", return_value=MagicMock()), \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0), \
             patch("executor.place_stock_order", return_value=_ok_result(quantity=200, price=100.0)) as mock_place, \
             patch("telegram_bot.send_text") as mock_send:
            telegram_bot._handle_dt_buy("999", "2330", dt_path=pos_path, db_path=db_path)

        assert mock_place.called
        _, kwargs = mock_place.call_args
        assert kwargs["budget"] == 20_000.0  # risk_amount=1000, per_share_risk=5 -> 200*100

        # 成功訊息應附上風險額資訊
        success_msgs = [c.args[1] for c in mock_send.call_args_list if len(c.args) > 1]
        assert any("風險額" in m for m in success_msgs)
        assert any("每股風險" in m for m in success_msgs)

    def test_falls_back_when_stop_loss_missing(self, tmp_path):
        import telegram_bot
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions
        from research_db import init_db

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=90, status="watching"),
        ], path=pos_path)

        with patch("dt_risk.is_circuit_breaker_active", return_value=False), \
             patch("telegram_bot._get_sj_api", return_value=MagicMock()), \
             patch("daytrading_monitor.fetch_current_price", return_value=100.0), \
             patch("executor.place_stock_order", return_value=_ok_result()) as mock_place, \
             patch("telegram_bot.send_text") as mock_send:
            telegram_bot._handle_dt_buy("999", "2330", dt_path=pos_path, db_path=db_path)

        _, kwargs = mock_place.call_args
        from daytrading_config import load_daytrading_config
        assert kwargs["budget"] == load_daytrading_config().budget_per_stock
