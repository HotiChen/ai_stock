"""
tests/test_order_sync.py — 向券商同步委託狀態

狀態機只有在「有人告訴它成交了」的時候才會前進。這個「有人」必須是**券商**，
不是 place_order 的回傳值。本模組週期性查 api.list_trades()，把每筆委託的
真實狀態推進狀態機。

對應關係：
    Filled              → 成交確認（buy→active / sell→closed）
    PartFilled          → **也算成交**：手上確實有股票，數量以 deal_quantity 為準。
                          當成「還沒成交」會讓 13:00 強平漏掉這個部位。
    Cancelled / Failed  → 回滾（buy→watching / sell→active）
    Submitted / 其他     → 維持不動，下一輪再看
"""
from unittest.mock import MagicMock

import pytest

import dt_position_store as store
from daytrading_monitor import DaytradingPosition

TODAY = "2026-09-03"


def _pos(code="2330"):
    return DaytradingPosition(
        code=code, name="台積電", entry_low=100.0, entry_high=101.0,
        target_price=110.0, stop_loss=97.0, dt_score=8, ai_summary="",
        status="watching",
    )


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "dt.db")
    store.replace_today([_pos()], trade_date=TODAY, db_path=p, json_path=None)
    return p


def _trade(order_id, action, status, deal_qty=0, deal_price=0.0):
    t = MagicMock()
    t.order.id = order_id
    t.order.action = action
    t.status.status = status
    t.status.deal_quantity = deal_qty
    t.status.deal_price = deal_price
    return t


def _api(*trades):
    api = MagicMock()
    api.list_trades.return_value = list(trades)
    return api


class TestBuySync:
    def _submitted(self, db):
        store.mark_buy_submitted("2330", order_id="B1", trade_date=TODAY, db_path=db)

    def test_filled_becomes_active(self, db):
        self._submitted(db)
        store.sync_order_status(_api(_trade("B1", "Buy", "Filled", 1, 100.5)),
                                trade_date=TODAY, db_path=db, json_path=None)
        assert store.get_status("2330", TODAY, db) == "active"

    def test_filled_records_deal_price(self, db):
        self._submitted(db)
        store.sync_order_status(_api(_trade("B1", "Buy", "Filled", 2, 100.5)),
                                trade_date=TODAY, db_path=db, json_path=None)
        pos = {p.code: p for p in store.load_positions(TODAY, db)}["2330"]
        assert pos.entry_price == 100.5
        assert pos.quantity == 2

    def test_part_filled_also_becomes_active(self, db):
        """★ 部分成交也是持有。當成「還沒成交」會讓 13:00 強平漏掉它，
        隔天變成交割義務。"""
        self._submitted(db)
        store.sync_order_status(_api(_trade("B1", "Buy", "PartFilled", 1, 100.5)),
                                trade_date=TODAY, db_path=db, json_path=None)
        assert store.get_status("2330", TODAY, db) == "active"

    def test_cancelled_reverts_to_watching(self, db):
        self._submitted(db)
        store.sync_order_status(_api(_trade("B1", "Buy", "Cancelled")),
                                trade_date=TODAY, db_path=db, json_path=None)
        assert store.get_status("2330", TODAY, db) == "watching"

    def test_failed_reverts_to_watching(self, db):
        self._submitted(db)
        store.sync_order_status(_api(_trade("B1", "Buy", "Failed")),
                                trade_date=TODAY, db_path=db, json_path=None)
        assert store.get_status("2330", TODAY, db) == "watching"

    def test_still_submitted_stays_put(self, db):
        """掛著沒成交就維持原狀——不能猜它成交了。"""
        self._submitted(db)
        store.sync_order_status(_api(_trade("B1", "Buy", "Submitted")),
                                trade_date=TODAY, db_path=db, json_path=None)
        assert store.get_status("2330", TODAY, db) == "buy_submitted"


class TestSellSync:
    def _sell_submitted(self, db):
        store.mark_buy_submitted("2330", order_id="B1", trade_date=TODAY, db_path=db)
        store.confirm_buy_filled("2330", 100.0, 1, trade_date=TODAY, db_path=db)
        store.mark_sell_submitted("2330", order_id="S1", trade_date=TODAY, db_path=db)

    def test_filled_becomes_closed(self, db):
        self._sell_submitted(db)
        store.sync_order_status(_api(_trade("S1", "Sell", "Filled", 1, 108.0)),
                                trade_date=TODAY, db_path=db, json_path=None)
        assert store.get_status("2330", TODAY, db) == "closed"

    def test_cancelled_reverts_to_active(self, db):
        """★ 賣單被退要回 active，否則系統以為平了、實際還抱著，
        13:00 強平也不會處理它。"""
        self._sell_submitted(db)
        store.sync_order_status(_api(_trade("S1", "Sell", "Cancelled")),
                                trade_date=TODAY, db_path=db, json_path=None)
        assert store.get_status("2330", TODAY, db) == "active"

    def test_still_pending_stays_put(self, db):
        self._sell_submitted(db)
        store.sync_order_status(_api(_trade("S1", "Sell", "Submitted")),
                                trade_date=TODAY, db_path=db, json_path=None)
        assert store.get_status("2330", TODAY, db) == "sell_submitted"


class TestRobustness:
    def test_no_api_is_noop(self, db):
        store.mark_buy_submitted("2330", order_id="B1", trade_date=TODAY, db_path=db)
        report = store.sync_order_status(None, trade_date=TODAY, db_path=db,
                                         json_path=None)
        assert report["synced"] == 0
        assert store.get_status("2330", TODAY, db) == "buy_submitted"

    def test_api_failure_does_not_raise(self, db):
        """★ 券商查詢失敗不得中斷主流程——這是週期性背景工作。"""
        api = MagicMock()
        api.list_trades.side_effect = Exception("connection lost")
        report = store.sync_order_status(api, trade_date=TODAY, db_path=db,
                                         json_path=None)
        assert report["error"]

    def test_unknown_order_id_ignored(self, db):
        """券商回報本系統不認識的委託（手動下單、其他程式）→ 不動作。"""
        store.mark_buy_submitted("2330", order_id="B1", trade_date=TODAY, db_path=db)
        store.sync_order_status(_api(_trade("OTHER", "Buy", "Filled", 1, 99.0)),
                                trade_date=TODAY, db_path=db, json_path=None)
        assert store.get_status("2330", TODAY, db) == "buy_submitted"

    def test_report_counts(self, db):
        store.mark_buy_submitted("2330", order_id="B1", trade_date=TODAY, db_path=db)
        report = store.sync_order_status(_api(_trade("B1", "Buy", "Filled", 1, 100.0)),
                                         trade_date=TODAY, db_path=db, json_path=None)
        assert report["filled"] == 1
        assert report["synced"] == 1


class TestUncertainAlert:
    def test_lists_uncertain_positions(self, db):
        """★ 買單送出很久還沒回報的部位，必須被點名。

        它可能已經成交但系統不知道——13:00 強平不會處理它（狀態不是
        active），隔天就是交割義務。這種情況必須讓人看見。
        """
        store.mark_buy_submitted("2330", order_id="B1", trade_date=TODAY, db_path=db)
        codes = store.uncertain_positions(TODAY, db)
        assert [p.code for p in codes] == ["2330"]

    def test_empty_when_all_settled(self, db):
        store.mark_buy_submitted("2330", order_id="B1", trade_date=TODAY, db_path=db)
        store.confirm_buy_filled("2330", 100.0, 1, trade_date=TODAY, db_path=db)
        assert store.uncertain_positions(TODAY, db) == []


class TestReconcileCoversSellSubmitted:
    def test_sell_submitted_counted_as_held(self, db, monkeypatch):
        """★ 賣單送出但未成交，股票還在手上——對帳必須看得到它。

        漏掉的話，對帳會回報「券商有、DB 沒有」，人工判斷時容易誤以為那是
        系統外的部位而不處理。
        """
        from unittest.mock import MagicMock

        monkeypatch.setattr(store, "_DB_PATH", db)
        store.mark_buy_submitted("2330", order_id="B1", trade_date=TODAY, db_path=db)
        store.confirm_buy_filled("2330", 100.0, 1, trade_date=TODAY, db_path=db)
        store.mark_sell_submitted("2330", order_id="S1", trade_date=TODAY, db_path=db)

        api = MagicMock()
        broker_pos = MagicMock()
        broker_pos.code = "2330"
        broker_pos.quantity = 1000
        api.list_positions.return_value = [broker_pos]

        report = store.reconcile_with_broker(api, trade_date=TODAY, db_path=db)
        assert "2330" not in report["broker_only"], \
            "sell_submitted 應被視為持有，不該回報成券商獨有"
