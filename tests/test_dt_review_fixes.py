"""
tests/test_dt_review_fixes.py — Wave 5 總 code review 的 8 個正確性修復

F1/F2/F4: 出場 CAS claim 制（消滅 tick/輪詢/13:25 三路徑重複下單）
F3:       mark_* 0 列無聲成功 → 回傳 rowcount + 告警
F5:       legacy JSON 分支存子集不得刪其他持倉（merge-by-code）
F6:       升級告警只在第 3 次發一次，之後不再發 Telegram
F7:       跨日 JSON 鏡像不得復活為今日持倉（mtime 防護）
F8:       calc_risk_quantity 股數正規化為實際會成交的數量
"""
import json
import os
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from daytrading_monitor import DaytradingPosition


def _pos(code="2330", status="active", **kw):
    d = dict(
        code=code, name="台積電", entry_low=99.0, entry_high=101.0,
        target_price=105.0, stop_loss=97.0, dt_score=8, status=status,
        entry_price=100.0, peak_price=100.0, quantity=1, lot_type="common",
    )
    d.update(kw)
    return DaytradingPosition(**d)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """把 dt_position_store 的預設 DB/鏡像導到 tmp，並讓 daytrading_monitor
    的預設 path 與之對齊（走 store 分支）。"""
    import dt_position_store as dps
    import daytrading_monitor as dm

    db = str(tmp_path / "pos.db")
    mirror = str(tmp_path / "pos.json")
    monkeypatch.setattr(dps, "_DB_PATH", db)
    monkeypatch.setattr(dps, "_JSON_MIRROR", mirror)
    monkeypatch.setattr(dm, "_DEFAULT_PATH", mirror)
    dps._migrated.clear()
    return dps, dm, db, mirror


# ── F1: claim_for_close / revert_to_active（store 層 CAS）────────────────────

class TestClaimForClose:
    def test_claim_active_succeeds_once(self, store):
        dps, dm, db, mirror = store
        dps.save_positions([_pos()], db_path=db, json_path=mirror)
        assert dps.claim_for_close("2330", db_path=db, json_path=mirror) is True
        # 第二次 claim（已 closed）必須失敗 → 不會有第二筆賣單
        assert dps.claim_for_close("2330", db_path=db, json_path=mirror) is False

    def test_claim_watching_fails(self, store):
        dps, dm, db, mirror = store
        dps.save_positions([_pos(status="watching")], db_path=db, json_path=mirror)
        assert dps.claim_for_close("2330", db_path=db, json_path=mirror) is False

    def test_claim_missing_row_fails(self, store):
        dps, dm, db, mirror = store
        assert dps.claim_for_close("9999", db_path=db, json_path=mirror) is False

    def test_revert_to_active(self, store):
        dps, dm, db, mirror = store
        dps.save_positions([_pos()], db_path=db, json_path=mirror)
        assert dps.claim_for_close("2330", db_path=db, json_path=mirror) is True
        assert dps.revert_to_active("2330", db_path=db, json_path=mirror) is True
        # 回滾後可再次 claim（下一輪重試）
        assert dps.claim_for_close("2330", db_path=db, json_path=mirror) is True

    def test_get_status(self, store):
        dps, dm, db, mirror = store
        dps.save_positions([_pos()], db_path=db, json_path=mirror)
        assert dps.get_status("2330", db_path=db) == "active"
        assert dps.get_status("9999", db_path=db) is None


# ── F3: mark_* 回傳 rowcount ─────────────────────────────────────────────────

class TestMarkRowcount:
    def test_mark_entered_missing_row_returns_false(self, store):
        dps, dm, db, mirror = store
        assert not dps.mark_entered("9999", 100.0, 1, db_path=db, json_path=mirror)

    def test_mark_entered_existing_returns_true(self, store):
        dps, dm, db, mirror = store
        dps.save_positions([_pos(status="watching")], db_path=db, json_path=mirror)
        assert dps.mark_entered("2330", 100.0, 1, db_path=db, json_path=mirror)

    def test_mark_closed_missing_returns_false(self, store):
        dps, dm, db, mirror = store
        assert not dps.mark_closed("9999", db_path=db, json_path=mirror)


# ── F4: update_entry_range 原子操作（9:05 不再 bulk 回存）────────────────────

class TestUpdateEntryRange:
    def test_updates_only_range(self, store):
        dps, dm, db, mirror = store
        dps.save_positions([_pos(status="watching", sell_attempts=2)],
                           db_path=db, json_path=mirror)
        assert dps.update_entry_range("2330", 98.5, 102.5,
                                      db_path=db, json_path=mirror)
        rows = dps.load_positions(db_path=db, json_path=mirror)
        assert rows[0].entry_low == 98.5 and rows[0].entry_high == 102.5
        assert rows[0].sell_attempts == 2      # 其他欄位不被覆蓋
        assert rows[0].status == "watching"


# ── F1: 輪詢出場走 claim（他人已 claim → 不下第二筆賣單）─────────────────────

class TestSellClaimFlow:
    def test_already_claimed_skips_sell(self, store, monkeypatch):
        dps, dm, db, mirror = store
        import main
        dps.save_positions([_pos()], db_path=db, json_path=mirror)
        # 模擬 tick 路徑先搶到
        assert dps.claim_for_close("2330", db_path=db, json_path=mirror) is True

        from daytrading_monitor import DaytradingAlert
        alert = DaytradingAlert(
            code="2330", name="台積電", alert_type="stop_loss",
            price=96.0, message="", time=datetime.now(), sell_required=True,
        )
        # pos_map 需要 active 才進迴圈——直接構造 pos 傳入（load 已是 closed
        # → pos_map 為空 → 不賣；再驗證 claim 層的第二道防線）
        with patch("main.force_stop_loss") as mock_sell:
            main._run_dt_sell_alerts(MagicMock(), [alert], MagicMock(),
                                     dt_path=mirror)
        mock_sell.assert_not_called()

    def test_claim_race_second_path_skips(self, store, monkeypatch):
        """position 在 load 之後、賣單之前被另一路徑 claim → 本路徑跳過。"""
        dps, dm, db, mirror = store
        import main
        dps.save_positions([_pos()], db_path=db, json_path=mirror)

        from daytrading_monitor import DaytradingAlert
        alert = DaytradingAlert(
            code="2330", name="台積電", alert_type="stop_loss",
            price=96.0, message="", time=datetime.now(), sell_required=True,
        )
        real_claim = dm.claim_for_close
        def racing_claim(code, path=None):
            # 模擬對手路徑在本路徑 load 之後、claim 之前搶先 claim 成功
            dps.claim_for_close(code, db_path=db, json_path=mirror)
            return real_claim(code, path=path)
        monkeypatch.setattr(dm, "claim_for_close", racing_claim)
        with patch("main.force_stop_loss") as mock_sell, \
             patch("telegram_bot.send_text"):
            main._run_dt_sell_alerts(MagicMock(), [alert], MagicMock(),
                                     dt_path=mirror)
        mock_sell.assert_not_called()

    def test_sell_failure_reverts_for_retry(self, store):
        dps, dm, db, mirror = store
        import main
        dps.save_positions([_pos()], db_path=db, json_path=mirror)
        from daytrading_monitor import DaytradingAlert
        alert = DaytradingAlert(
            code="2330", name="台積電", alert_type="stop_loss",
            price=96.0, message="", time=datetime.now(), sell_required=True,
        )
        with patch("main.force_stop_loss", return_value=False), \
             patch("telegram_bot.send_text"):
            main._run_dt_sell_alerts(MagicMock(), [alert], MagicMock(),
                                     dt_path=mirror)
        # 失敗 → 回滾 active 以便下一輪重試
        assert dps.get_status("2330", db_path=db) == "active"
        rows = dps.load_positions(db_path=db, json_path=mirror)
        assert rows[0].sell_attempts == 1


# ── F1: AlertWorker tick 路徑 claim ──────────────────────────────────────────

class TestAlertWorkerClaim:
    def _run_worker(self, alert, db_path, watchlist):
        import queue
        from monitor_agent import AlertWorker
        from research_db import init_db
        init_db(db_path)
        q = queue.Queue()
        worker = AlertWorker(q, db_path=db_path, telegram_chat_id=None,
                             auto_execute=True, api=MagicMock(),
                             watchlist=watchlist)
        q.put(alert)
        q.put(None)
        with patch("monitor_agent.notify_price_alert"), \
             patch("monitor_agent.force_stop_loss", return_value=True) as m:
            worker.run()
        return m

    def _alert(self):
        return {
            "code": "2330", "name": "台積電", "alert_type": "stop_loss",
            "message": "", "severity": "high", "created_at": datetime.now(),
            "current_price": 96.0,
        }

    def _watch(self):
        return [{"code": "2330", "name": "台積電", "entry_price": 100.0,
                 "quantity": 1, "lot_type": "common", "stop_loss_price": 97.0}]

    def test_claims_before_sell(self, store, tmp_path):
        dps, dm, db, mirror = store
        dps.save_positions([_pos()], db_path=db, json_path=mirror)
        m = self._run_worker(self._alert(), str(tmp_path / "r.db"), self._watch())
        m.assert_called_once()
        assert dps.get_status("2330", db_path=db) == "closed"

    def test_already_closed_skips_sell(self, store, tmp_path):
        dps, dm, db, mirror = store
        dps.save_positions([_pos(status="closed")], db_path=db, json_path=mirror)
        m = self._run_worker(self._alert(), str(tmp_path / "r.db"), self._watch())
        m.assert_not_called()

    def test_missing_row_still_sells(self, store, tmp_path):
        """不在狀態機中的持倉：出場安全優先，照舊下賣單。"""
        dps, dm, db, mirror = store
        m = self._run_worker(self._alert(), str(tmp_path / "r.db"), self._watch())
        m.assert_called_once()


# ── F2: 13:25 ForceCloseJob 標記 DT 持倉 closed ──────────────────────────────

class TestForceCloseMarksDT:
    def test_force_close_claims_dt_position(self, store, tmp_path):
        dps, dm, db, mirror = store
        import main
        from research_db import init_db, save_daily_trade
        rdb = str(tmp_path / "research.db")
        init_db(rdb)
        save_daily_trade({
            "trade_date": date.today(), "code": "2330", "name": "台積電",
            "action": "buy", "quantity": 1, "price": 100.0, "amount": 100000.0,
            "lot_type": "common", "sector": "當沖", "note": "daytrade_buy",
        }, rdb)
        dps.save_positions([_pos()], db_path=db, json_path=mirror)

        with patch("main.force_stop_loss", return_value=True), \
             patch("main._get_snapshot_price", return_value=99.0):
            main.ForceCloseJob(api=MagicMock(), db_path=rdb).run()

        assert dps.get_status("2330", db_path=db) == "closed"

    def test_poll_window_ends_before_force_close(self):
        import main
        from datetime import time as dtime
        assert main._DT_POLL_END < dtime(13, 25)


# ── F6: 升級告警節流 ─────────────────────────────────────────────────────────

class TestEscalationThrottle:
    def _fail_once(self, dps, dm, db, mirror, preset_attempts):
        import main
        dps.save_positions([_pos(sell_attempts=preset_attempts)],
                           db_path=db, json_path=mirror)
        from daytrading_monitor import DaytradingAlert
        alert = DaytradingAlert(
            code="2330", name="台積電", alert_type="stop_loss",
            price=96.0, message="", time=datetime.now(), sell_required=True,
        )
        import main as m
        with patch("main.force_stop_loss", return_value=False), \
             patch("telegram_bot.send_text") as mock_send, \
             patch.object(m, "TELEGRAM_CHAT_ID", "12345"):
            main._run_dt_sell_alerts(MagicMock(), [alert], MagicMock(),
                                     dt_path=mirror)
        return mock_send

    def test_third_failure_escalates(self, store):
        dps, dm, db, mirror = store
        mock_send = self._fail_once(dps, dm, db, mirror, preset_attempts=2)
        assert any("人工介入" in c.args[1] for c in mock_send.call_args_list)

    def test_fourth_failure_no_telegram(self, store):
        dps, dm, db, mirror = store
        mock_send = self._fail_once(dps, dm, db, mirror, preset_attempts=3)
        mock_send.assert_not_called()


# ── F5: legacy JSON 分支 merge-by-code ───────────────────────────────────────

class TestLegacyMergeSave:
    def test_saving_subset_preserves_others(self, tmp_path):
        from daytrading_monitor import (
            save_daytrading_positions, load_daytrading_positions,
        )
        path = str(tmp_path / "custom.json")
        save_daytrading_positions([_pos("2330"), _pos("2454", name="聯發科")],
                                  path=path)
        # 只回存 2330（peak 變更）→ 2454 不得消失
        p1 = _pos("2330", peak_price=103.0)
        save_daytrading_positions([p1], path=path)
        loaded = {p.code: p for p in load_daytrading_positions(path=path)}
        assert set(loaded) == {"2330", "2454"}
        assert loaded["2330"].peak_price == 103.0

    def test_run_monitor_subset_does_not_drop_positions(self, tmp_path):
        from daytrading_monitor import (
            run_daytrading_monitor, save_daytrading_positions,
            load_daytrading_positions,
        )
        from daytrading_config import DaytradingConfig
        path = str(tmp_path / "custom.json")
        save_daytrading_positions(
            [_pos("2330"), _pos("2454", name="聯發科", status="watching")],
            path=path)
        cfg = DaytradingConfig(force_close_time="23:59")
        # 只有 2330 抓得到價（peak 更新）；2454 抓不到
        def fake_price(code, api=None):
            return 102.0 if code == "2330" else None
        with patch("daytrading_monitor.fetch_current_price", side_effect=fake_price):
            run_daytrading_monitor(api=None, path=path, config=cfg)
        codes = {p.code for p in load_daytrading_positions(path=path)}
        assert codes == {"2330", "2454"}


# ── F7: 跨日鏡像不遷移 ───────────────────────────────────────────────────────

class TestMigrationMtime:
    def test_stale_json_not_migrated(self, store):
        dps, dm, db, mirror = store
        Path(mirror).write_text(
            json.dumps([{"code": "2330", "name": "台積電", "status": "active",
                         "quantity": 1}]),
            encoding="utf-8")
        yesterday = _time.time() - 86400
        os.utime(mirror, (yesterday, yesterday))
        assert dps.load_positions(db_path=db, json_path=mirror) == []

    def test_today_json_still_migrates(self, store):
        dps, dm, db, mirror = store
        Path(mirror).write_text(
            json.dumps([{"code": "2330", "name": "台積電", "status": "active",
                         "quantity": 1}]),
            encoding="utf-8")
        loaded = dps.load_positions(db_path=db, json_path=mirror)
        assert [p.code for p in loaded] == ["2330"]


# ── F8: calc_risk_quantity 正規化 ────────────────────────────────────────────

class TestRiskQtyNormalize:
    def test_common_lot_normalized(self):
        from executor import calc_risk_quantity
        # 風險額 5000 / 每股風險 2 = 2500 股 → 125,000 元可買 2 張 → 2000 股
        shares, reason = calc_risk_quantity(
            total_budget=1_000_000, risk_pct=0.5,
            entry_price=50.0, stop_loss_price=48.0,
            budget_cap=200_000, hard_limit=200_000)
        assert shares == 2000

    def test_odd_lot_kept(self):
        from executor import calc_risk_quantity
        # 風險額 1600 / 每股風險 2 = 800 股 → 40,000 元買不起 1 張 → 零股 800
        shares, reason = calc_risk_quantity(
            total_budget=320_000, risk_pct=0.5,
            entry_price=50.0, stop_loss_price=48.0,
            budget_cap=200_000, hard_limit=200_000)
        assert shares == 800
