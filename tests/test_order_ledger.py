"""
tests/test_order_ledger.py — 跨行程的下單去重

現況：executor.is_duplicate_order(code, action, prior_orders) 由呼叫端自己
傳入 prior_orders。實際上只有 main.py 的波段路徑有傳；當沖自動買、
Telegram 快速下單、FastAPI backend 都沒傳——**守衛等於沒開**。

而且它是記憶體內的判斷。main.py、telegram_bot.py、FastAPI backend 是三個
獨立行程，彼此看不到對方送出的委託：使用者在 Telegram 按「快速下單」的
同時，9:10 排程也在買同一檔，兩張單都會送出去。

修法：SQLite 的原子宣告（INSERT OR IGNORE + UNIQUE），下單前先搶。
搶不到代表別人已經在處理，直接跳過。

刻意只擋買進
------------
  重複買進 → 曝險加倍、花掉沒打算花的錢
  重複賣出 → 券商會擋（現股不能賣超過庫存）
  **被擋住的賣出 → 部位沒平掉 → 隔日交割義務 → 可能違約**
最後一項最嚴重，所以賣出一律放行，與 HALT 閘門同一個道理。
"""
from datetime import date

import pytest

import order_ledger

TODAY = date(2026, 9, 3)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "orders.db")


class TestAtomicClaim:
    def test_first_claim_succeeds(self, db):
        assert order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db) is True

    def test_second_claim_fails(self, db):
        order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db)
        assert order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db) is False

    def test_different_code_independent(self, db):
        order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db)
        assert order_ledger.claim("2454", "buy", trade_date=TODAY, db_path=db) is True

    def test_different_action_independent(self, db):
        """買和賣是不同的宣告——買過不該擋住賣出。"""
        order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db)
        assert order_ledger.claim("2330", "sell", trade_date=TODAY, db_path=db) is True

    def test_different_day_independent(self, db):
        order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db)
        assert order_ledger.claim("2330", "buy", trade_date=date(2026, 9, 4),
                                  db_path=db) is True

    def test_action_normalized(self, db):
        """Shioaji 回傳的是 'Action.Buy'，內部用 'buy'——兩者必須視為同一件事，
        否則同一檔會被下兩次單。"""
        order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db)
        assert order_ledger.claim("2330", "Action.Buy", trade_date=TODAY,
                                  db_path=db) is False

    def test_concurrent_claims_only_one_wins(self, db):
        """★ 這是整個機制的重點：多執行緒同時搶，只能有一個成功。"""
        import threading
        results = []
        lock = threading.Lock()

        def worker():
            got = order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db)
            with lock:
                results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(results) == 1, f"應只有一個成功，實際 {sum(results)}"


class TestRelease:
    def test_release_allows_retry(self, db):
        """★ 下單失敗要釋放，否則今天再也不能重試這一檔。"""
        order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db)
        order_ledger.release("2330", "buy", trade_date=TODAY, db_path=db)
        assert order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db) is True

    def test_release_unclaimed_is_noop(self, db):
        order_ledger.release("9999", "buy", trade_date=TODAY, db_path=db)  # 不得拋出

    def test_confirmed_claim_not_released_by_accident(self, db):
        """已確認送出的委託不該被 release 掉——那會讓重複防護失效。"""
        order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db)
        order_ledger.confirm("2330", "buy", order_id="OID1",
                             trade_date=TODAY, db_path=db)
        order_ledger.release("2330", "buy", trade_date=TODAY, db_path=db)
        assert order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db) is False


class TestInspection:
    def test_confirm_records_order_id(self, db):
        order_ledger.claim("2330", "buy", trade_date=TODAY, db_path=db)
        order_ledger.confirm("2330", "buy", order_id="OID1",
                             trade_date=TODAY, db_path=db)
        rows = order_ledger.list_claims(TODAY, db_path=db)
        assert rows[0]["order_id"] == "OID1"
        assert rows[0]["status"] == "submitted"

    def test_list_empty_day(self, db):
        assert order_ledger.list_claims(TODAY, db_path=db) == []


class TestExecutorIntegration:
    def _api(self):
        from unittest.mock import MagicMock
        api = MagicMock()
        trade = MagicMock()
        trade.order.id = "OID1"
        api.place_order.return_value = trade
        return api

    def _order(self, api, action, db):
        from executor import place_stock_order
        return place_stock_order(
            api=api, code="2330", name="台積電", action=action,
            budget=30_000, price=100.0, paper_trading=False,
            ledger_db_path=db,
        )

    def test_second_buy_blocked(self, db):
        api = self._api()
        first = self._order(api, "buy", db)
        second = self._order(api, "buy", db)
        assert first.success is True
        assert second.success is False
        assert api.place_order.call_count == 1, "同一檔同一天不得送出兩張買單"

    def test_block_reason_is_explicit(self, db):
        api = self._api()
        self._order(api, "buy", db)
        assert "重複" in self._order(api, "buy", db).reason

    def test_sell_never_blocked(self, db):
        """★ 賣出不受去重影響——擋住賣出比重複賣出危險得多。"""
        api = self._api()
        assert self._order(api, "sell", db).success is True
        assert self._order(api, "sell", db).success is True
        assert api.place_order.call_count == 2

    def test_failed_order_releases_claim(self, db):
        """★ 下單失敗（券商拒絕、網路斷）要釋放宣告，否則今天無法重試。"""
        from unittest.mock import MagicMock
        api = MagicMock()
        api.place_order.side_effect = Exception("broker rejected")
        assert self._order(api, "buy", db).success is False

        api2 = self._api()
        assert self._order(api2, "buy", db).success is True, "失敗後應可重試"

    def test_ledger_failure_does_not_block_trading(self, db, monkeypatch):
        """★ 帳本本身壞掉（磁碟滿、權限）不得癱瘓下單——去重是輔助機制，
        擋住所有交易的代價高於漏擋一次重複。"""
        monkeypatch.setattr(order_ledger, "claim",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        assert self._order(self._api(), "buy", db).success is True


class TestPaperMode:
    def test_paper_order_also_deduped(self, db):
        """紙上模式一樣要去重——否則模擬出來的績效會有重複部位。"""
        from unittest.mock import MagicMock

        from executor import place_stock_order
        kw = dict(api=MagicMock(), code="2330", name="台積電", action="buy",
                  budget=30_000, price=100.0, paper_trading=True,
                  ledger_db_path=db)
        assert place_stock_order(**kw).success is True
        assert place_stock_order(**kw).success is False

    def test_paper_claim_is_confirmed_not_left_dangling(self, db):
        """帳本狀態要反映事實，否則事後追查會以為「宣告了卻沒下單」。"""
        from unittest.mock import MagicMock

        from executor import place_stock_order
        place_stock_order(api=MagicMock(), code="2330", name="台積電",
                          action="buy", budget=30_000, price=100.0,
                          paper_trading=True, ledger_db_path=db)
        row = order_ledger.list_claims(db_path=db)[0]
        assert row["status"] == "submitted"
        assert row["order_id"] == "PAPER-ORDER"


class TestSharedGuard:
    """app.py 的 Streamlit 手動下單直接呼叫 api.place_order，繞過所有守衛：
    HALT、金額上限、重複防護，以及**最危險的 PAPER_TRADING**——紙上模式下
    按那個按鈕會送出真實委託。

    守衛抽成共用函式，兩邊都用同一份，不是各自複製一遍
    （複製的那份遲早會跟本體長歪，正是這次事故的模式）。
    """

    def test_blocks_buy_while_halted(self, db, tmp_path, monkeypatch):
        import halt as halt_mod

        from executor import guard_new_order
        monkeypatch.setattr(halt_mod, "_HALT_FILE", tmp_path / "HALT")
        halt_mod.halt(reason="test")
        reason = guard_new_order("2330", "buy", paper_trading=False,
                                 ledger_db_path=db)
        assert reason and "暫停" in reason

    def test_allows_sell_while_halted(self, db, tmp_path, monkeypatch):
        import halt as halt_mod

        from executor import guard_new_order
        monkeypatch.setattr(halt_mod, "_HALT_FILE", tmp_path / "HALT")
        halt_mod.halt(reason="test")
        assert guard_new_order("2330", "sell", paper_trading=False,
                               ledger_db_path=db) is None

    def test_blocks_duplicate_buy(self, db, tmp_path, monkeypatch):
        import halt as halt_mod

        from executor import guard_new_order
        monkeypatch.setattr(halt_mod, "_HALT_FILE", tmp_path / "HALT")
        assert guard_new_order("2330", "buy", paper_trading=False,
                               ledger_db_path=db) is None
        second = guard_new_order("2330", "buy", paper_trading=False,
                                 ledger_db_path=db)
        assert second and "重複" in second

    def test_blocks_real_order_in_paper_mode(self, db, tmp_path, monkeypatch):
        """★ 紙上模式下不得送出真實委託——這是 app.py 目前最危險的漏洞。"""
        import halt as halt_mod

        from executor import guard_new_order
        monkeypatch.setattr(halt_mod, "_HALT_FILE", tmp_path / "HALT")
        reason = guard_new_order("2330", "buy", paper_trading=True,
                                 ledger_db_path=db)
        assert reason and "紙上" in reason
