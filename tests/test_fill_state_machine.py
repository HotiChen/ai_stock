"""
tests/test_fill_state_machine.py — 委託 ≠ 成交

現況：executor.place_stock_order 在 api.place_order() 一回來就回報
success=True，接著 mark_entered() 把持倉標成 active。但那是「**委託已送出**」，
不是「已成交」。委託可能被券商退單、只成交一部分、或掛著整天沒成交——
系統一律當成「已持有」，於是：

  * 監控會對一個不存在的部位算損益、發停損警報
  * 13:00 強平會對沒成交的部位下賣單 → 變成放空
  * 當日績效統計出現從未存在的交易

新增兩個中間狀態，只有**券商回報成交**才能進入 active / closed：

    watching → buy_submitted → active → sell_submitted → closed
                    ↓ 退單                      ↓ 退單
                 watching                     active

模擬模式（PAPER_TRADING=true）沒有券商，維持原本的立即轉換——這也讓既有
行為在切到真錢之前完全不變。
"""
import pytest

import dt_position_store as store
from daytrading_monitor import DaytradingPosition

TODAY = "2026-09-03"


def _pos(code="2330", status="watching"):
    return DaytradingPosition(
        code=code, name="台積電", entry_low=100.0, entry_high=101.0,
        target_price=110.0, stop_loss=97.0, dt_score=8,
        ai_summary="", status=status,
    )


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "dt.db")
    store.replace_today([_pos()], trade_date=TODAY, db_path=p, json_path=None)
    return p


class TestBuyLifecycle:
    def test_submit_moves_to_buy_submitted(self, db):
        assert store.mark_buy_submitted("2330", order_id="OID1",
                                        trade_date=TODAY, db_path=db) is True
        assert store.get_status("2330", TODAY, db) == "buy_submitted"

    def test_submitted_is_not_active(self, db):
        """★ 送出委託不等於持有。監控與強平都以 active 為準，
        提前標 active 會讓系統對不存在的部位下賣單。"""
        store.mark_buy_submitted("2330", order_id="OID1", trade_date=TODAY, db_path=db)
        assert store.get_status("2330", TODAY, db) != "active"

    def test_fill_confirmation_moves_to_active(self, db):
        store.mark_buy_submitted("2330", order_id="OID1", trade_date=TODAY, db_path=db)
        assert store.confirm_buy_filled("2330", entry_price=100.5, quantity=1,
                                        trade_date=TODAY, db_path=db) is True
        assert store.get_status("2330", TODAY, db) == "active"

    def test_fill_records_entry_price(self, db):
        store.mark_buy_submitted("2330", order_id="OID1", trade_date=TODAY, db_path=db)
        store.confirm_buy_filled("2330", entry_price=100.5, quantity=2,
                                 trade_date=TODAY, db_path=db)
        pos = {p.code: p for p in store.load_positions(TODAY, db)}["2330"]
        assert pos.entry_price == 100.5
        assert pos.quantity == 2
        assert pos.peak_price == 100.5

    def test_rejection_reverts_to_watching(self, db):
        """★ 退單要回到 watching，讓之後可以重試——留在 buy_submitted 的話
        這一檔今天就卡死了。"""
        store.mark_buy_submitted("2330", order_id="OID1", trade_date=TODAY, db_path=db)
        assert store.revert_buy_submitted("2330", trade_date=TODAY, db_path=db) is True
        assert store.get_status("2330", TODAY, db) == "watching"

    def test_cannot_confirm_without_submitting(self, db):
        """沒送過委託就回報成交，是狀態機被繞過的訊號，必須拒絕。"""
        assert store.confirm_buy_filled("2330", entry_price=100.0, quantity=1,
                                        trade_date=TODAY, db_path=db) is False
        assert store.get_status("2330", TODAY, db) == "watching"

    def test_double_submit_blocked(self, db):
        store.mark_buy_submitted("2330", order_id="OID1", trade_date=TODAY, db_path=db)
        assert store.mark_buy_submitted("2330", order_id="OID2",
                                        trade_date=TODAY, db_path=db) is False


class TestSellLifecycle:
    def _active(self, db):
        store.mark_buy_submitted("2330", order_id="B1", trade_date=TODAY, db_path=db)
        store.confirm_buy_filled("2330", entry_price=100.0, quantity=1,
                                 trade_date=TODAY, db_path=db)

    def test_submit_moves_to_sell_submitted(self, db):
        self._active(db)
        assert store.mark_sell_submitted("2330", order_id="S1",
                                         trade_date=TODAY, db_path=db) is True
        assert store.get_status("2330", TODAY, db) == "sell_submitted"

    def test_only_one_sell_submit_wins(self, db):
        """★ CAS：兩條路徑（tick 監控與 5 分鐘輪詢）同時想賣，只能有一個送單。"""
        self._active(db)
        first = store.mark_sell_submitted("2330", order_id="S1",
                                          trade_date=TODAY, db_path=db)
        second = store.mark_sell_submitted("2330", order_id="S2",
                                           trade_date=TODAY, db_path=db)
        assert (first, second) == (True, False)

    def test_fill_confirmation_moves_to_closed(self, db):
        self._active(db)
        store.mark_sell_submitted("2330", order_id="S1", trade_date=TODAY, db_path=db)
        assert store.confirm_sell_filled("2330", trade_date=TODAY, db_path=db) is True
        assert store.get_status("2330", TODAY, db) == "closed"

    def test_rejection_reverts_to_active(self, db):
        """★ 賣單被退要回到 active，否則系統以為已平倉，實際上還抱著——
        13:00 強平也不會處理它，隔天就是交割義務。"""
        self._active(db)
        store.mark_sell_submitted("2330", order_id="S1", trade_date=TODAY, db_path=db)
        assert store.revert_sell_submitted("2330", trade_date=TODAY, db_path=db) is True
        assert store.get_status("2330", TODAY, db) == "active"

    def test_cannot_sell_what_is_not_active(self, db):
        assert store.mark_sell_submitted("2330", order_id="S1",
                                         trade_date=TODAY, db_path=db) is False


class TestHeldStatuses:
    """哪些狀態代表「可能持有」——強平與對帳必須涵蓋它們。"""

    def test_active_is_held(self):
        assert "active" in store.HELD_STATUSES

    def test_sell_submitted_is_held(self):
        """★ 賣單送出但未成交，部位仍在手上。漏掉它 → 以為平了，其實沒平。"""
        assert "sell_submitted" in store.HELD_STATUSES

    def test_buy_submitted_is_uncertain_not_held(self):
        """買單送出但未成交：不確定持有與否。不能當成持有（會放空），
        但必須另行對帳確認——所以獨立一組。"""
        assert "buy_submitted" not in store.HELD_STATUSES
        assert "buy_submitted" in store.UNCERTAIN_STATUSES

    def test_watching_and_closed_not_held(self):
        assert "watching" not in store.HELD_STATUSES
        assert "closed" not in store.HELD_STATUSES
