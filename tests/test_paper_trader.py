"""tests/test_paper_trader.py — TDD tests for paper_trader.py（紙上追蹤模式）

紙上追蹤：9:10 以當前市價「紙上進場」watching 持倉，用與真實當沖完全相同的
出場邏輯（check_position_alerts / check_trailing_stop）追蹤，觸發出場時記錄到
獨立帳本，收盤後彙總損益。不下任何真實/模擬委託，與真實下單路徑完全隔離。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest


def _pos(code="2337", name="旺宏", status="watching",
         entry_price=None, peak_price=None, quantity=0,
         entry_low=None, entry_high=None, target_price=None, stop_loss=None):
    from daytrading_monitor import DaytradingPosition
    return DaytradingPosition(
        code=code, name=name,
        entry_low=entry_low, entry_high=entry_high,
        target_price=target_price, stop_loss=stop_loss,
        dt_score=8, status=status,
        entry_price=entry_price, peak_price=peak_price,
        quantity=quantity, lot_type="common",
    )


def _config(stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
            force_close_time="13:00"):
    from daytrading_config import DaytradingConfig
    return DaytradingConfig(
        stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
        trailing_start_pct=trailing_start_pct, trailing_gap_pct=trailing_gap_pct,
        force_close_time=force_close_time,
    )


# ── paper_enter ───────────────────────────────────────────────────────────────

class TestPaperEnter:
    def test_enters_watching_at_market_price(self):
        import paper_trader
        positions = [_pos("2337", "旺宏"), _pos("2449", "京元電子")]
        with patch("paper_trader.fetch_current_price", side_effect=lambda code, api=None: 186.0):
            entered = paper_trader.paper_enter(positions, api=None, quantity=1)
        assert len(entered) == 2
        for p in positions:
            assert p.status == "active"
            assert p.entry_price == 186.0
            assert p.peak_price == 186.0
            assert p.quantity == 1
            assert p.lot_type == "common"

    def test_skips_when_no_quote(self):
        import paper_trader
        positions = [_pos("2337", "旺宏")]
        with patch("paper_trader.fetch_current_price", return_value=None):
            entered = paper_trader.paper_enter(positions, api=None)
        assert entered == []
        # 無報價 → 維持 watching，不可亂設 entry_price
        assert positions[0].status == "watching"
        assert positions[0].entry_price is None

    def test_only_touches_watching(self):
        import paper_trader
        already_active = _pos("2330", "台積電", status="active",
                              entry_price=600.0, quantity=1)
        positions = [already_active, _pos("2337", "旺宏")]
        with patch("paper_trader.fetch_current_price", return_value=186.0):
            entered = paper_trader.paper_enter(positions, api=None)
        # active 的不可被重設 entry
        assert already_active.entry_price == 600.0
        assert {p.code for p in entered} == {"2337"}

    def test_skip_pick_with_none_prices_still_enters(self):
        """AI 判 skip（entry/target/stop 全 None）的持倉也能紙上進場。"""
        import paper_trader
        pos = _pos("2449", "京元電子")  # target/stop 皆 None
        with patch("paper_trader.fetch_current_price", return_value=100.0):
            paper_trader.paper_enter([pos], api=None)
        assert pos.status == "active"
        assert pos.entry_price == 100.0


# ── record_paper_exit / load_paper_trades ─────────────────────────────────────

class TestLedger:
    def test_pnl_common_lot_times_1000(self, tmp_path):
        import paper_trader
        pos = _pos("2337", "旺宏", status="active",
                   entry_price=100.0, quantity=1)
        trade = paper_trader.record_paper_exit(
            pos, exit_price=105.0, reason="take_profit_ceiling",
            day=date(2026, 6, 23), ddir=str(tmp_path))
        # (105 - 100) * 1 張 * 1000 股 = 5000
        assert trade["pnl"] == 5000.0
        assert trade["reason"] == "take_profit_ceiling"
        assert trade["code"] == "2337"

    def test_loss_pnl_negative(self, tmp_path):
        import paper_trader
        pos = _pos("2337", "旺宏", status="active",
                   entry_price=100.0, quantity=1)
        trade = paper_trader.record_paper_exit(
            pos, exit_price=97.0, reason="stop_loss",
            day=date(2026, 6, 23), ddir=str(tmp_path))
        assert trade["pnl"] == -3000.0

    def test_ledger_roundtrip_and_append(self, tmp_path):
        import paper_trader
        day = date(2026, 6, 23)
        p1 = _pos("2337", "旺宏", status="active", entry_price=100.0, quantity=1)
        p2 = _pos("2449", "京元電子", status="active", entry_price=50.0, quantity=1)
        paper_trader.record_paper_exit(p1, 105.0, "take_profit_ceiling", day=day, ddir=str(tmp_path))
        paper_trader.record_paper_exit(p2, 48.0, "stop_loss", day=day, ddir=str(tmp_path))
        loaded = paper_trader.load_paper_trades(day, ddir=str(tmp_path))
        assert len(loaded) == 2
        assert {t["code"] for t in loaded} == {"2337", "2449"}

    def test_load_missing_returns_empty(self, tmp_path):
        import paper_trader
        assert paper_trader.load_paper_trades(date(2026, 6, 23), ddir=str(tmp_path)) == []


# ── paper_monitor_pass（複用真實出場邏輯）─────────────────────────────────────

class TestPaperMonitorPass:
    def test_stop_loss_closes_and_records(self, tmp_path):
        import paper_trader
        pos = _pos("2337", "旺宏", status="active",
                   entry_price=100.0, peak_price=100.0, quantity=1)
        # 現價 96 → 跌 4% ≤ 停損 -3% → 出場
        with patch("paper_trader.fetch_current_price", return_value=96.0):
            changed, closed = paper_trader.paper_monitor_pass(
                [pos], api=None, config=_config(stop_loss_pct=3.0),
                day=date(2026, 6, 23), ddir=str(tmp_path))
        assert changed is True
        assert len(closed) == 1
        assert closed[0]["reason"] == "stop_loss"
        assert pos.status == "closed"
        # 寫入帳本
        assert len(paper_trader.load_paper_trades(date(2026, 6, 23), ddir=str(tmp_path))) == 1

    def test_no_trigger_stays_active(self, tmp_path):
        import paper_trader
        pos = _pos("2337", "旺宏", status="active",
                   entry_price=100.0, peak_price=100.0, quantity=1)
        # 現價 101 → 漲 1%，未達停損/停利/追蹤門檻；force_close 設未來避免時間觸發
        with patch("paper_trader.fetch_current_price", return_value=101.0):
            changed, closed = paper_trader.paper_monitor_pass(
                [pos], api=None, config=_config(force_close_time="23:59"),
                day=date(2026, 6, 23), ddir=str(tmp_path))
        assert closed == []
        assert pos.status == "active"

    def test_force_close_by_time(self, tmp_path):
        import paper_trader
        pos = _pos("2337", "旺宏", status="active",
                   entry_price=100.0, peak_price=100.0, quantity=1)
        # force_close_time 設為過去（00:00）→ 現在必 >= 它 → 強制平倉
        with patch("paper_trader.fetch_current_price", return_value=100.5):
            changed, closed = paper_trader.paper_monitor_pass(
                [pos], api=None, config=_config(force_close_time="00:00"),
                day=date(2026, 6, 23), ddir=str(tmp_path))
        assert len(closed) == 1
        assert closed[0]["reason"] == "force_close"
        assert pos.status == "closed"

    def test_take_profit_positive_pnl(self, tmp_path):
        import paper_trader
        pos = _pos("2337", "旺宏", status="active",
                   entry_price=100.0, peak_price=100.0, quantity=1)
        # 現價 110 → 漲 10% ≥ 天花板 9% → 停利
        with patch("paper_trader.fetch_current_price", return_value=110.0):
            changed, closed = paper_trader.paper_monitor_pass(
                [pos], api=None, config=_config(take_profit_pct=9.0),
                day=date(2026, 6, 23), ddir=str(tmp_path))
        assert closed[0]["reason"] == "take_profit_ceiling"
        assert closed[0]["pnl"] == 10000.0  # (110-100)*1*1000

    def test_closed_positions_ignored(self, tmp_path):
        import paper_trader
        pos = _pos("2337", "旺宏", status="closed",
                   entry_price=100.0, quantity=1)
        with patch("paper_trader.fetch_current_price", return_value=50.0) as mock_price:
            changed, closed = paper_trader.paper_monitor_pass(
                [pos], api=None, config=_config(), day=date(2026, 6, 23), ddir=str(tmp_path))
        assert closed == []
        mock_price.assert_not_called()


# ── build_paper_summary / messages ────────────────────────────────────────────

class TestSummary:
    def test_empty_summary_returns_blank(self, tmp_path):
        import paper_trader
        assert paper_trader.build_paper_summary(date(2026, 6, 23), ddir=str(tmp_path)) == ""

    def test_summary_total_pnl_and_winrate(self, tmp_path):
        import paper_trader
        day = date(2026, 6, 23)
        p1 = _pos("2337", "旺宏", status="active", entry_price=100.0, quantity=1)
        p2 = _pos("2449", "京元電子", status="active", entry_price=50.0, quantity=1)
        paper_trader.record_paper_exit(p1, 105.0, "take_profit_ceiling", day=day, ddir=str(tmp_path))
        paper_trader.record_paper_exit(p2, 48.0, "stop_loss", day=day, ddir=str(tmp_path))
        s = paper_trader.build_paper_summary(day, ddir=str(tmp_path))
        # 合計 +5000 -2000 = +3000
        assert "3,000" in s or "3000" in s
        assert "2337" in s and "2449" in s

    def test_entry_message_contains_code_and_price(self):
        import paper_trader
        pos = _pos("2337", "旺宏", status="active", entry_price=186.0, quantity=1)
        msg = paper_trader.build_entry_message([pos])
        assert "2337" in msg and "186" in msg

    def test_exit_message_contains_reason(self, tmp_path):
        import paper_trader
        pos = _pos("2337", "旺宏", status="active", entry_price=100.0, quantity=1)
        trade = paper_trader.record_paper_exit(pos, 97.0, "stop_loss",
                                               day=date(2026, 6, 23), ddir=str(tmp_path))
        msg = paper_trader.build_exit_message([trade])
        assert "2337" in msg


# ── DaytradingConfig flag ─────────────────────────────────────────────────────

class TestConfigFlag:
    def test_paper_trade_only_default_false(self):
        from daytrading_config import DaytradingConfig
        assert DaytradingConfig().paper_trade_only is False

    def test_env_enables_paper_only(self, monkeypatch, tmp_path):
        from daytrading_config import load_daytrading_config
        monkeypatch.setenv("DT_PAPER_ONLY", "true")
        # 指向不存在的 JSON，確保走 env → default 路徑
        cfg = load_daytrading_config(path=str(tmp_path / "nope.json"))
        assert cfg.paper_trade_only is True
