from __future__ import annotations

"""
tests/test_force_close_idempotency.py — ForceCloseJob 冪等性測試

覆蓋範圍（與 test_main.py 不重複）：
  (a) 待平倉記錄攔截機制（pending_close_codes guard）— 驗證第二次執行時
      已有 force_close_requested 記錄的代號被跳過，且回傳結果仍含 success=True
  (b) 模擬模式：force_stop_loss 成功後寫入 action='sell'，pnl 以
      (close_price - buy_price) * quantity * multiplier 計算
  (c) 真實模式：force_stop_loss 成功後寫入 action='force_close_requested'，
      pnl 欄位為 None（未確認成交）

注意：test_main.py 已涵蓋「第一次→第二次整體跑通」的整合場景；
本文件針對各分支的細部行為與回傳值做獨立驗收。
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest


# ── 輔助：建立最小買入交易記錄 ────────────────────────────────────────────────

def _make_buy_trade(
    code: str = "2330",
    name: str = "台積電",
    quantity: int = 1000,
    price: float = 850.0,
    lot_type: str = "common",
    sector: str = "半導體",
) -> dict:
    return {
        "trade_date": date.today(),
        "code": code,
        "name": name,
        "action": "buy",
        "quantity": quantity,
        "price": price,
        "amount": price * quantity,
        "pnl": None,
        "lot_type": lot_type,
        "sector": sector,
        "note": "",
    }


# ── (a) pending_close_codes guard ────────────────────────────────────────────


class TestPendingCloseCodesGuard:
    """
    真實模式下，DB 已有 force_close_requested 記錄的代號
    不應再次送出 force_stop_loss 委託。
    """

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_skips_code_with_pending_close_record(self, mock_force, tmp_path):
        """DB 已有 force_close_requested → force_stop_loss 不被呼叫。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade

        db_path = str(tmp_path / "t.db")
        init_db(db_path)

        # 買入部位
        save_daily_trade(_make_buy_trade("2330"), db_path)
        # 模擬第一次已送出平倉委託
        save_daily_trade({
            "trade_date": date.today(),
            "code": "2330",
            "name": "台積電",
            "action": "force_close_requested",
            "quantity": 1000,
            "price": 870.0,
            "amount": 870_000.0,
            "pnl": None,
            "lot_type": "common",
            "sector": "半導體",
            "note": "force_close_requested",
        }, db_path)

        mock_force.return_value = True
        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        mock_force.assert_not_called()

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_skipped_position_still_appears_in_results(self, mock_force, tmp_path):
        """跳過的代號在回傳清單中仍有 {'code': ..., 'success': True}。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade

        db_path = str(tmp_path / "t.db")
        init_db(db_path)

        save_daily_trade(_make_buy_trade("2330"), db_path)
        save_daily_trade({
            "trade_date": date.today(),
            "code": "2330",
            "name": "台積電",
            "action": "force_close_requested",
            "quantity": 1000,
            "price": 870.0,
            "amount": 870_000.0,
            "pnl": None,
            "lot_type": "common",
            "sector": "半導體",
            "note": "force_close_requested",
        }, db_path)

        mock_force.return_value = True
        results = ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        assert len(results) == 1
        assert results[0]["code"] == "2330"
        assert results[0]["success"] is True

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_only_matching_code_is_skipped_other_codes_proceed(
        self, mock_force, tmp_path
    ):
        """pending_close_codes 只攔截對應代號；其他仍有持倉的代號照常送單。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade

        db_path = str(tmp_path / "t.db")
        init_db(db_path)

        # 2330 已有 pending close，2454 尚未送過
        save_daily_trade(_make_buy_trade("2330"), db_path)
        save_daily_trade(_make_buy_trade("2454", name="聯發科"), db_path)
        save_daily_trade({
            "trade_date": date.today(),
            "code": "2330",
            "name": "台積電",
            "action": "force_close_requested",
            "quantity": 1000,
            "price": 870.0,
            "amount": 870_000.0,
            "pnl": None,
            "lot_type": "common",
            "sector": "半導體",
            "note": "force_close_requested",
        }, db_path)

        mock_force.return_value = True
        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        # 只有 2454 進入 force_stop_loss
        assert mock_force.call_count == 1
        assert mock_force.call_args[1]["code"] == "2454"

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_pending_close_codes_built_from_load_daily_trades(
        self, mock_force, tmp_path
    ):
        """pending_close_codes 應完全來自 load_daily_trades 的 force_close_requested 行。

        驗證方式：patch load_daily_trades 讓它回傳一筆 force_close_requested，
        但持倉中的代號和該記錄的代號相同 → force_stop_loss 不被呼叫。
        """
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(_make_buy_trade("2330"), db_path)

        # 用 patch 模擬 load_daily_trades 回傳包含 force_close_requested 的清單
        fake_trades = [{
            "trade_date": date.today().isoformat(),
            "code": "2330",
            "action": "force_close_requested",
        }]
        with patch("main.load_daily_trades", return_value=fake_trades):
            mock_force.return_value = True
            ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        mock_force.assert_not_called()


# ── (b) 模擬模式：sell + pnl ─────────────────────────────────────────────────


class TestSimulationModeSellWithPnl:
    """
    SIMULATION=True 時，force_stop_loss 成功後應寫入 action='sell' 記錄，
    pnl = (close_price - buy_price) * quantity * multiplier。
    """

    @patch("main.SIMULATION", True)
    @patch("main.force_stop_loss")
    def test_simulation_sell_record_pnl_formula_common(self, mock_force, tmp_path):
        """整股 common：pnl = (close - buy) * qty * 1000。

        close_price 由 _get_snapshot_price fallback 到 buy_price 時 pnl=0；
        本測試 mock snapshot 讓 close != buy，確認乘數 1000 正確。
        """
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades

        db_path = str(tmp_path / "t.db")
        init_db(db_path)

        buy_price = 850.0
        close_price = 900.0  # +50 per share
        quantity = 1  # 1 張 = 1000 股
        expected_pnl = (close_price - buy_price) * quantity * 1000  # 50000

        save_daily_trade({
            **_make_buy_trade("2330", quantity=quantity, price=buy_price),
            "amount": buy_price * quantity,
        }, db_path)

        mock_force.return_value = True

        api = MagicMock()
        snapshot = MagicMock()
        snapshot.close = close_price
        api.snapshots.return_value = [snapshot]

        ForceCloseJob(api=api, db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        sells = [t for t in trades if t["action"] == "sell"]
        assert len(sells) == 1, f"模擬模式應寫入 1 筆 sell，實際 {len(sells)} 筆"
        assert sells[0]["pnl"] == pytest.approx(expected_pnl), (
            f"PnL 應為 {expected_pnl}，實際 {sells[0]['pnl']}"
        )

    @patch("main.SIMULATION", True)
    @patch("main.force_stop_loss")
    def test_simulation_sell_record_pnl_formula_intraday_odd(
        self, mock_force, tmp_path
    ):
        """零股 intraday_odd：multiplier=1，pnl = (close - buy) * qty。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades

        db_path = str(tmp_path / "t.db")
        init_db(db_path)

        buy_price = 850.0
        close_price = 870.0  # +20 per share
        quantity = 50  # 50 股（零股）
        expected_pnl = (close_price - buy_price) * quantity * 1  # 1000

        save_daily_trade({
            **_make_buy_trade("2330", quantity=quantity, price=buy_price,
                              lot_type="intraday_odd"),
            "amount": buy_price * quantity,
        }, db_path)

        mock_force.return_value = True

        api = MagicMock()
        snapshot = MagicMock()
        snapshot.close = close_price
        api.snapshots.return_value = [snapshot]

        ForceCloseJob(api=api, db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        sells = [t for t in trades if t["action"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["pnl"] == pytest.approx(expected_pnl), (
            f"零股 PnL 應為 {expected_pnl}，實際 {sells[0]['pnl']}"
        )

    @patch("main.SIMULATION", True)
    @patch("main.force_stop_loss")
    def test_simulation_sell_record_note_is_force_close_simulation(
        self, mock_force, tmp_path
    ):
        """模擬模式的 sell 記錄，note 欄位應為 'force_close_simulation'。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(_make_buy_trade("2330"), db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        sells = [t for t in trades if t["action"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["note"] == "force_close_simulation"

    @patch("main.SIMULATION", True)
    @patch("main.force_stop_loss")
    def test_simulation_no_force_close_requested_record_written(
        self, mock_force, tmp_path
    ):
        """模擬模式：DB 中不應出現 force_close_requested 記錄。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(_make_buy_trade("2330"), db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        pending = [t for t in trades if t["action"] == "force_close_requested"]
        assert len(pending) == 0, (
            f"模擬模式不應寫入 force_close_requested，實際 {len(pending)} 筆"
        )


# ── (c) 真實模式：force_close_requested + pnl=None ───────────────────────────


class TestRealModeForceCloseRequested:
    """
    SIMULATION=False 時，force_stop_loss 成功後應寫入
    action='force_close_requested'，且 pnl=None（未確認成交）。
    """

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_real_mode_writes_force_close_requested(self, mock_force, tmp_path):
        """真實模式：成功送出後寫入 force_close_requested，不寫 sell。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(_make_buy_trade("2330"), db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        pending = [t for t in trades if t["action"] == "force_close_requested"]
        assert len(pending) == 1, (
            f"真實模式應有 1 筆 force_close_requested，實際 {len(pending)} 筆"
        )

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_real_mode_pending_record_pnl_is_none(self, mock_force, tmp_path):
        """真實模式：pending 記錄的 pnl 必須為 None（成交未確認）。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(_make_buy_trade("2330"), db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        pending = [t for t in trades if t["action"] == "force_close_requested"]
        assert len(pending) == 1
        assert pending[0]["pnl"] is None, (
            f"真實模式未確認成交不應有 pnl，實際得到 {pending[0]['pnl']!r}"
        )

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_real_mode_no_confirmed_sell_written(self, mock_force, tmp_path):
        """真實模式：不應有 action='sell' 的已確認成交記錄。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(_make_buy_trade("2330"), db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        sells = [t for t in trades if t["action"] == "sell"]
        assert len(sells) == 0, (
            f"真實模式不應立即寫入 confirmed sell，實際 {len(sells)} 筆"
        )

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_real_mode_pending_record_note_is_force_close_requested(
        self, mock_force, tmp_path
    ):
        """真實模式：pending 記錄的 note 欄位應為 'force_close_requested'。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(_make_buy_trade("2330"), db_path)
        mock_force.return_value = True

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        pending = [t for t in trades if t["action"] == "force_close_requested"]
        assert len(pending) == 1
        assert pending[0]["note"] == "force_close_requested"

    @patch("main.SIMULATION", False)
    @patch("main.force_stop_loss")
    def test_real_mode_failed_submission_writes_nothing(self, mock_force, tmp_path):
        """真實模式：force_stop_loss 失敗（回傳 False）→ DB 無新增記錄。"""
        from main import ForceCloseJob
        from research_db import init_db, save_daily_trade, load_daily_trades

        db_path = str(tmp_path / "t.db")
        init_db(db_path)
        save_daily_trade(_make_buy_trade("2330"), db_path)
        mock_force.return_value = False  # 委託送出失敗

        ForceCloseJob(api=MagicMock(), db_path=db_path).run()

        trades = load_daily_trades(date.today(), db_path)
        # 只有原本的 buy；沒有 sell 或 force_close_requested
        non_buy = [t for t in trades if t["action"] != "buy"]
        assert len(non_buy) == 0, (
            f"force_stop_loss 失敗不應寫入任何新記錄，實際 {non_buy}"
        )
