"""
tests/test_buy_result_recording.py — 下單結果如何進入狀態機

place_stock_order 回傳 success=True 只代表「委託已送出」。兩個模式的處理
必須不同：

  紙上模式（PAPER_TRADING=true）：沒有券商，委託即成交 → 直接 active。
      維持既有行為，切到真錢之前完全不變。

  真實模式：委託 ≠ 成交 → buy_submitted，等券商回報才 active。
      提前標 active 會讓監控對不存在的部位算損益、讓 13:00 強平對沒成交的
      部位下賣單（變成放空）。
"""
import pytest

import dt_position_store as store
from daytrading_monitor import DaytradingPosition, record_buy_result
from executor import ExecutionResult

TODAY = "2026-09-03"


def _pos(code="2330"):
    return DaytradingPosition(
        code=code, name="台積電", entry_low=100.0, entry_high=101.0,
        target_price=110.0, stop_loss=97.0, dt_score=8, ai_summary="",
        status="watching",
    )


def _result(order_id="OID1"):
    return ExecutionResult(
        code="2330", name="台積電", action="buy", quantity=1, price=100.5,
        amount=100_500.0, lot_type="common", success=True,
        order_id=order_id, reason="",
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = str(tmp_path / "dt.db")
    monkeypatch.setattr(store, "_DB_PATH", p)
    store.replace_today([_pos()], trade_date=TODAY, db_path=p, json_path=None)
    return p


class TestPaperMode:
    def test_goes_straight_to_active(self, db):
        """紙上模式沒有券商，維持既有行為。"""
        assert record_buy_result("2330", _result(), paper_trading=True,
                                 trade_date=TODAY, db_path=db) is True
        assert store.get_status("2330", TODAY, db) == "active"

    def test_records_price_and_quantity(self, db):
        record_buy_result("2330", _result(), paper_trading=True,
                          trade_date=TODAY, db_path=db)
        pos = {p.code: p for p in store.load_positions(TODAY, db)}["2330"]
        assert pos.entry_price == 100.5
        assert pos.quantity == 1


class TestRealMode:
    def test_goes_to_buy_submitted_not_active(self, db):
        """★ 真實模式：委託送出 ≠ 成交。"""
        assert record_buy_result("2330", _result(), paper_trading=False,
                                 trade_date=TODAY, db_path=db) is True
        assert store.get_status("2330", TODAY, db) == "buy_submitted"

    def test_entry_price_not_set_before_fill(self, db):
        """★ 成交價要等券商回報。用委託價當成交價，損益從一開始就是錯的。"""
        record_buy_result("2330", _result(), paper_trading=False,
                          trade_date=TODAY, db_path=db)
        pos = {p.code: p for p in store.load_positions(TODAY, db)}["2330"]
        assert pos.entry_price is None

    def test_order_id_recorded_for_later_sync(self, db):
        """沒有 order_id 就無法向券商查這筆的狀態，狀態機會永遠卡住。"""
        record_buy_result("2330", _result(order_id="OID9"), paper_trading=False,
                          trade_date=TODAY, db_path=db)
        import sqlite3
        con = sqlite3.connect(db)
        oid = con.execute(
            "SELECT buy_order_id FROM dt_positions WHERE trade_date=? AND code=?",
            (TODAY, "2330")).fetchone()[0]
        con.close()
        assert oid == "OID9"

    def test_returns_false_when_position_missing(self, db):
        """券商已受理但持倉庫沒這筆 → 回 False，呼叫端要告警。"""
        assert record_buy_result("9999", _result(), paper_trading=False,
                                 trade_date=TODAY, db_path=db) is False
