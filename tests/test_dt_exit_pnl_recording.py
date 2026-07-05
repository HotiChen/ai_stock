"""tests/test_dt_exit_pnl_recording.py — DT 出場已實現損益記錄完整性.

涵蓋（Tim 已核准的缺口修復）：
  1. monitor_agent.AlertWorker 的 pnl lot multiplier 修正：
     common 1 張 = 1000 股，pnl 必須乘 1000；零股維持股數。
  2. main._run_dt_sell_alerts（5 分鐘輪詢出場路徑）賣單成功後寫入
     daily_trades sell 記錄（note="auto_exit"、exit_reason=alert_type、含 pnl）。
  3. 防重複記錄：同一檔同一天 tick 路徑與輪詢路徑都可能執行，
     兩邊寫入前都檢查今日是否已有該 code 的 auto_exit sell 記錄。
  4. 熔斷整合：輪詢出場虧損 → get_today_dt_realized_pnl 看得到 →
     累積達門檻觸發熔斷。
"""
from __future__ import annotations

import queue
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest


def _seed_active(path, code="2330", name="台積電", entry_price=100.0,
                 quantity=1, lot_type="common", stop_loss=None):
    from daytrading_monitor import DaytradingPosition, save_daytrading_positions
    save_daytrading_positions([
        DaytradingPosition(
            code=code, name=name, entry_low=None, entry_high=None,
            target_price=None, stop_loss=stop_loss, dt_score=90, status="active",
            entry_price=entry_price, peak_price=entry_price,
            quantity=quantity, lot_type=lot_type,
        ),
    ], path=path)


def _sell_alert(code="2330", name="台積電", alert_type="stop_loss", price=95.0):
    from daytrading_monitor import DaytradingAlert
    return DaytradingAlert(
        code=code, name=name, alert_type=alert_type, price=price,
        message="出場訊號", time=datetime.now(), sell_required=True,
    )


def _cfg(**kw):
    from daytrading_config import DaytradingConfig
    base = dict(force_close_time="23:59")
    base.update(kw)
    return DaytradingConfig(**base)


# ── 1. AlertWorker lot multiplier ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_dt_store(tmp_path, monkeypatch):
    """隔離 dt_position_store 的預設路徑：AlertWorker 出場前的 CAS claim 會讀
    持倉狀態機，不得受 repo data/ 下殘留的執行期檔案影響。"""
    import dt_position_store as dps
    monkeypatch.setattr(dps, "_DB_PATH", str(tmp_path / "dtpos.db"))
    monkeypatch.setattr(dps, "_JSON_MIRROR", str(tmp_path / "dtpos.json"))
    dps._migrated.clear()

class TestAlertWorkerLotMultiplier:
    def _run_worker(self, db_path, watchlist, alert):
        from monitor_agent import AlertWorker
        q = queue.Queue()
        worker = AlertWorker(
            q, db_path=db_path, telegram_chat_id=None,
            auto_execute=True, api=MagicMock(), watchlist=watchlist,
        )
        q.put(alert)
        q.put(None)
        with patch("monitor_agent.force_stop_loss", return_value=True), \
                patch("notifier.notify_price_alert"):
            worker.run()

    def _alert(self, current_price=842.0):
        return dict(
            code="2330", name="台積電", alert_type="stop_loss",
            message="觸發", severity="high",
            created_at=datetime.now(), current_price=current_price,
        )

    def test_common_lot_pnl_multiplied_by_1000(self, tmp_path):
        """common 持倉 quantity=1（張）：pnl = (exit-entry) × 1 × 1000。"""
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        wl = [dict(code="2330", name="台積電", quantity=1,
                   lot_type="common", entry_price=820.0)]
        self._run_worker(db_path, wl, self._alert(current_price=842.0))

        sells = [r for r in load_daily_trades(date.today(), db_path)
                 if r["action"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["pnl"] == pytest.approx(22_000.0)   # (842-820)*1*1000

    def test_common_lot_loss_pnl(self, tmp_path):
        """common 持倉虧損：pnl 為負且乘 1000。"""
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        wl = [dict(code="2330", name="台積電", quantity=2,
                   lot_type="common", entry_price=820.0)]
        self._run_worker(db_path, wl, self._alert(current_price=815.0))

        sells = [r for r in load_daily_trades(date.today(), db_path)
                 if r["action"] == "sell"]
        assert sells[0]["pnl"] == pytest.approx(-10_000.0)  # (815-820)*2*1000

    def test_odd_lot_pnl_uses_share_count(self, tmp_path):
        """零股持倉 quantity=100（股）：pnl = (exit-entry) × 100（不乘 1000）。"""
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        wl = [dict(code="2330", name="台積電", quantity=100,
                   lot_type="intraday_odd", entry_price=820.0)]
        self._run_worker(db_path, wl, self._alert(current_price=842.0))

        sells = [r for r in load_daily_trades(date.today(), db_path)
                 if r["action"] == "sell"]
        assert sells[0]["pnl"] == pytest.approx(2_200.0)    # (842-820)*100

    def test_skips_duplicate_when_exit_already_recorded(self, tmp_path):
        """今日該 code 已有 auto_exit sell 記錄（例如輪詢路徑先寫）→ 不重複寫入。"""
        from research_db import init_db, save_daily_trade, load_daily_trades
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "sell", "quantity": 1, "price": 842.0,
            "pnl": 22_000.0, "lot_type": "common", "sector": "當沖",
            "note": "auto_exit", "exit_reason": "stop_loss",
        }, db_path)

        wl = [dict(code="2330", name="台積電", quantity=1,
                   lot_type="common", entry_price=820.0)]
        self._run_worker(db_path, wl, self._alert())

        sells = [r for r in load_daily_trades(date.today(), db_path)
                 if r["action"] == "sell"]
        assert len(sells) == 1   # 只有預先寫入的那筆


# ── 2. _run_dt_sell_alerts 寫入 daily_trades ──────────────────────────────────

class TestRunDtSellAlertsRecordsExit:
    def test_success_writes_sell_row_common_lot(self, tmp_path):
        import main
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_active(pos_path, entry_price=100.0, quantity=1, lot_type="common")

        with patch("main.force_stop_loss", return_value=True):
            main._run_dt_sell_alerts(
                MagicMock(), [_sell_alert(alert_type="stop_loss", price=95.0)],
                _cfg(), dt_path=pos_path, db_path=db_path)

        sells = [r for r in load_daily_trades(date.today(), db_path)
                 if r["action"] == "sell"]
        assert len(sells) == 1
        row = sells[0]
        assert row["code"] == "2330"
        assert row["note"] == "auto_exit"
        assert row["exit_reason"] == "stop_loss"
        assert row["quantity"] == 1
        assert row["lot_type"] == "common"
        assert row["price"] == pytest.approx(95.0)
        assert row["pnl"] == pytest.approx(-5_000.0)   # (95-100)*1*1000
        assert row["amount"] == pytest.approx(95_000.0)

    def test_success_writes_sell_row_odd_lot(self, tmp_path):
        import main
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_active(pos_path, entry_price=100.0, quantity=300, lot_type="intraday_odd")

        with patch("main.force_stop_loss", return_value=True):
            main._run_dt_sell_alerts(
                MagicMock(), [_sell_alert(alert_type="trailing_stop", price=95.0)],
                _cfg(), dt_path=pos_path, db_path=db_path)

        sells = [r for r in load_daily_trades(date.today(), db_path)
                 if r["action"] == "sell"]
        assert sells[0]["pnl"] == pytest.approx(-1_500.0)   # (95-100)*300
        assert sells[0]["exit_reason"] == "trailing_stop"

    def test_pnl_none_when_entry_price_missing(self, tmp_path):
        import main
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_active(pos_path, entry_price=None, quantity=1, lot_type="common")

        with patch("main.force_stop_loss", return_value=True):
            main._run_dt_sell_alerts(
                MagicMock(), [_sell_alert(price=95.0)],
                _cfg(), dt_path=pos_path, db_path=db_path)

        sells = [r for r in load_daily_trades(date.today(), db_path)
                 if r["action"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["pnl"] is None

    def test_sell_failure_writes_no_record(self, tmp_path):
        import main
        from research_db import init_db, load_daily_trades
        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_active(pos_path)

        with patch("main.force_stop_loss", return_value=False):
            main._run_dt_sell_alerts(
                MagicMock(), [_sell_alert()],
                _cfg(), dt_path=pos_path, db_path=db_path)

        assert load_daily_trades(date.today(), db_path) == []

    def test_db_failure_does_not_break_flow(self, tmp_path):
        """save_daily_trade 拋例外 → 不 raise，持倉仍轉 closed。"""
        import main
        from daytrading_monitor import load_daytrading_positions
        from research_db import init_db
        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_active(pos_path)

        with patch("main.force_stop_loss", return_value=True), \
             patch("main.save_daily_trade", side_effect=Exception("db down")):
            main._run_dt_sell_alerts(
                MagicMock(), [_sell_alert()],
                _cfg(), dt_path=pos_path, db_path=db_path)

        positions = load_daytrading_positions(path=pos_path)
        assert positions[0].status == "closed"

    def test_skips_duplicate_when_tick_path_recorded_first(self, tmp_path):
        """tick 路徑（AlertWorker）已寫 auto_exit sell → 輪詢路徑不重複寫。"""
        import main
        from research_db import init_db, save_daily_trade, load_daily_trades
        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_active(pos_path)
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "sell", "quantity": 1, "price": 96.0,
            "pnl": -4_000.0, "lot_type": "common", "sector": "未知",
            "note": "auto_exit", "exit_reason": "stop_loss",
        }, db_path)

        with patch("main.force_stop_loss", return_value=True):
            main._run_dt_sell_alerts(
                MagicMock(), [_sell_alert(price=95.0)],
                _cfg(), dt_path=pos_path, db_path=db_path)

        sells = [r for r in load_daily_trades(date.today(), db_path)
                 if r["action"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["pnl"] == pytest.approx(-4_000.0)   # 保留 tick 路徑那筆

    def test_buy_record_does_not_block_recording(self, tmp_path):
        """同 code 今日的 buy 記錄不影響出場記錄寫入（只 dedup auto_exit sell）。"""
        import main
        from research_db import init_db, save_daily_trade, load_daily_trades
        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        init_db(db_path)
        _seed_active(pos_path)
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "buy", "quantity": 1, "price": 100.0,
            "pnl": None, "lot_type": "common", "sector": "當沖",
            "note": "daytrade_buy",
        }, db_path)

        with patch("main.force_stop_loss", return_value=True):
            main._run_dt_sell_alerts(
                MagicMock(), [_sell_alert(price=95.0)],
                _cfg(), dt_path=pos_path, db_path=db_path)

        sells = [r for r in load_daily_trades(date.today(), db_path)
                 if r["action"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["pnl"] == pytest.approx(-5_000.0)


# ── 4. 熔斷整合：輪詢出場虧損 → 熔斷看得到 → 觸發 ────────────────────────────

class TestCircuitBreakerSeesPollExitLosses:
    def test_poll_exit_loss_feeds_breaker_and_triggers(self, tmp_path, monkeypatch):
        import main
        import dt_risk
        from dt_risk import get_today_dt_realized_pnl
        from research_db import init_db

        db_path = str(tmp_path / "research.db")
        pos_path = str(tmp_path / "pos.json")
        flag_path = str(tmp_path / "cb.json")
        init_db(db_path)
        monkeypatch.setattr(dt_risk, "_FLAG_PATH", flag_path)
        monkeypatch.setattr(main, "TELEGRAM_CHAT_ID", "12345")

        # 持倉：1 張 @100，停損 3%；現價 95 → 停損觸發，虧 5000（達 3000 門檻）
        _seed_active(pos_path, entry_price=100.0, quantity=1, lot_type="common")
        cfg = _cfg(stop_loss_pct=3.0, daily_max_loss=3000.0)

        # 第一輪：熔斷檢查（pnl=0，不觸發）→ 輪詢出場觸發停損 → 寫入虧損記錄
        with patch("daytrading_monitor.fetch_current_price", return_value=95.0), \
             patch("main.force_stop_loss", return_value=True), \
             patch("telegram_bot.send_text"):
            sells = main._dt_poll_tick(MagicMock(), cfg, dt_path=pos_path, db_path=db_path)

        assert sells and sells[0].alert_type == "stop_loss"
        assert get_today_dt_realized_pnl(db_path) == pytest.approx(-5_000.0)
        assert dt_risk.is_circuit_breaker_active(path=flag_path) is False  # 本輪還沒檢查到

        # 第二輪：熔斷檢查看到 -5000 <= -3000 → 觸發、寫旗標
        with patch("daytrading_monitor.fetch_current_price", return_value=95.0), \
             patch("main.force_stop_loss", return_value=True), \
             patch("telegram_bot.send_text") as mock_send:
            main._dt_poll_tick(MagicMock(), cfg, dt_path=pos_path, db_path=db_path)

        assert dt_risk.is_circuit_breaker_active(path=flag_path) is True
        assert any("熔斷" in c.args[1] for c in mock_send.call_args_list if len(c.args) > 1)
