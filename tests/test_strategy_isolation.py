"""
tests/test_strategy_isolation.py — 當沖與波段必須分開

`daily_trades` 同時裝當沖與波段的成交。DT 買進雖然寫了 sector="當沖" /
note="daytrade_buy"，但那是**自由文字**，沒有任何讀取端用它篩選。於是：

  ForceCloseJob ← load_current_positions
      → 9:00 買的波段股，13:00 會被當沖強平賣掉，波段策略等於不存在
  PostMarketJob 加總 daily_trades.pnl
      → 當日損益把兩種策略混成一個數字
  risk_guard.validate_plan
      → 部位上限檢查算錯

（dt_risk.get_today_dt_realized_pnl 已經有篩選，靠的是 note=="auto_exit" /
note=="force_close_simulation" and sector=="當沖" 這類**自由文字**判斷。
它目前是對的，但只要有人改了 note 字串就會靜默失效——改用正式欄位。）

修法：daily_trades 新增 strategy_type（'daytrade' / 'swing'）正式欄位，
讀取端明確篩選。既有列以 note/sector 的既有標記回填，不臆測。
"""
from datetime import date

import pytest

from research_db import init_db, load_daily_trades, save_daily_trade

TODAY = date(2026, 9, 3)


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "research.db")
    init_db(p)
    return p


def _trade(code, action, strategy_type=None, pnl=None, **kw):
    t = {
        "trade_date": TODAY, "code": code, "name": code, "action": action,
        "quantity": 1, "price": 100.0, "amount": 100_000.0, "pnl": pnl,
        "lot_type": "common", "sector": "半導體", "note": "",
    }
    if strategy_type is not None:
        t["strategy_type"] = strategy_type
    t.update(kw)
    return t


class TestSchema:
    def test_strategy_type_column_exists(self, db):
        save_daily_trade(_trade("2330", "buy", "daytrade"), db)
        assert "strategy_type" in load_daily_trades(TODAY, db)[0]

    def test_unspecified_stays_unknown(self, db):
        """★ 未指定就是 NULL（未知），不臆測。

        安全方向不對稱：漏平一檔當沖 → 隔天交割義務、可能違約；誤平一檔
        波段 → 少賺。所以強平只放過**明確標記為 swing** 的部位，未知照平。
        若這裡預設成 'swing'，任何漏標的當沖就再也不會被平倉。
        """
        save_daily_trade(_trade("2330", "buy"), db)
        assert load_daily_trades(TODAY, db)[0]["strategy_type"] is None

    def test_migration_backfills_from_existing_markers(self, tmp_path):
        """★ 既有資料庫的舊列要用既有標記回填，不能全部當成 swing——
        那會讓歷史上的當沖紀錄被誤認為波段。"""
        import sqlite3
        p = str(tmp_path / "old.db")
        init_db(p)
        # 模擬「加欄位之前」的資料庫：把欄位拿掉不可行，改為直接塞 NULL
        con = sqlite3.connect(p)
        con.execute(
            "INSERT INTO daily_trades (trade_date, code, action, sector, note,"
            " strategy_type) VALUES (?,?,?,?,?,NULL)",
            (TODAY.isoformat(), "2330", "buy", "當沖", "daytrade_buy"),
        )
        con.execute(
            "INSERT INTO daily_trades (trade_date, code, action, sector, note,"
            " strategy_type) VALUES (?,?,?,?,?,NULL)",
            (TODAY.isoformat(), "2317", "buy", "電子", "id=X1"),
        )
        con.commit()
        con.close()

        init_db(p)   # 再跑一次 migration
        rows = {r["code"]: r["strategy_type"] for r in load_daily_trades(TODAY, p)}
        assert rows["2330"] == "daytrade", "note=daytrade_buy 應回填為 daytrade"
        assert rows["2317"] is None, "沒有標記的歷史列維持未知，不臆測成 swing"


class TestPositionFiltering:
    def _seed(self, db):
        save_daily_trade(_trade("2330", "buy", "daytrade"), db)
        save_daily_trade(_trade("2317", "buy", "swing"), db)

    def test_load_all_by_default(self, db):
        import main
        self._seed(db)
        codes = {p["code"] for p in main.load_current_positions(TODAY, db)}
        assert codes == {"2330", "2317"}

    def test_filter_daytrade_only(self, db):
        import main
        self._seed(db)
        codes = {p["code"] for p in
                 main.load_current_positions(TODAY, db, strategy_type="daytrade")}
        assert codes == {"2330"}

    def test_filter_swing_only(self, db):
        import main
        self._seed(db)
        codes = {p["code"] for p in
                 main.load_current_positions(TODAY, db, strategy_type="swing")}
        assert codes == {"2317"}

    def test_sells_net_out_within_same_strategy(self, db):
        """同策略的賣出才抵銷買進——否則跨策略淨額會算錯。"""
        import main
        self._seed(db)
        save_daily_trade(_trade("2330", "sell", "daytrade"), db)
        codes = {p["code"] for p in
                 main.load_current_positions(TODAY, db, strategy_type="daytrade")}
        assert codes == set()


class TestForceCloseScope:
    """波段是否留倉是**資金面決定**（留倉需要 T+2 全額交割金），不是技術設定。
    原本系統每天把所有部位平掉，所以預設維持該行為；要保護波段必須明確開啟
    PROTECT_SWING_FROM_FORCE_CLOSE。"""

    def _run(self, db, protect: bool):
        from unittest.mock import MagicMock, patch

        import main
        save_daily_trade(_trade("2330", "buy", "daytrade"), db)
        save_daily_trade(_trade("2317", "buy", "swing"), db)
        save_daily_trade(_trade("2454", "buy"), db)          # 未標記
        with patch.object(main, "SIMULATION", True), \
             patch.object(main, "_get_snapshot_price", return_value=100.0), \
             patch("config.PROTECT_SWING_FROM_FORCE_CLOSE", protect):
            return {r["code"] for r in main.ForceCloseJob(api=MagicMock(),
                                                          db_path=db).run()}

    def test_default_closes_everything(self, db):
        """預設行為不變：全部平掉，不留倉。"""
        assert self._run(db, protect=False) == {"2330", "2317", "2454"}

    def test_protects_swing_when_enabled(self, db):
        """★ 開啟後，明確標記為 swing 的部位不再被強平。"""
        assert "2317" not in self._run(db, protect=True)

    def test_daytrade_always_closed(self, db):
        assert "2330" in self._run(db, protect=True)

    def test_unknown_strategy_always_closed(self, db):
        """★ 未標記一律照平——漏平一檔當沖會變成隔日交割義務、可能違約，
        代價遠高於誤平一檔波段。"""
        assert "2454" in self._run(db, protect=True)


class TestCircuitBreakerScope:
    """dt_risk 目前靠 note/sector 的自由文字判斷「這是不是當沖」。
    行為是對的，但脆弱：任何人改了 note 字串就會靜默失效。改用正式欄位，
    同時保留舊標記作為回溯相容。"""

    def _today_trade(self, code, pnl, strategy_type=None, note="", sector="半導體"):
        from datetime import date as _d
        t = _trade(code, "sell", strategy_type, pnl=pnl, note=note, sector=sector)
        t["trade_date"] = _d.today()
        return t

    def test_swing_loss_excluded(self, db):
        """★ 波段虧損不得計入當沖熔斷——那會讓當沖在沒賠錢的日子被停掉。"""
        import dt_risk
        save_daily_trade(self._today_trade("2317", -50_000.0, "swing"), db)
        assert dt_risk.get_today_dt_realized_pnl(db) == 0.0

    def test_daytrade_loss_counted_via_strategy_type(self, db):
        """★ 只靠 strategy_type 就要算得出來，不需要 note 是特定字串。"""
        import dt_risk
        save_daily_trade(self._today_trade("2330", -3_000.0, "daytrade"), db)
        assert dt_risk.get_today_dt_realized_pnl(db) == -3_000.0

    def test_legacy_null_strategy_type_falls_back_to_note(self, db):
        """回溯相容：欄位加入前寫的列是 NULL，要靠 note 認出來。

        （正常流程不會產生 NULL——save_daily_trade 預設填 'swing'，migration
        也會回填。這條守的是直接 SQL 寫入或 migration 之前殘留的列。）
        """
        import sqlite3
        from datetime import date as _d

        import dt_risk
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO daily_trades (trade_date, code, action, pnl, note,"
            " sector, strategy_type) VALUES (?,?,?,?,?,?,NULL)",
            (_d.today().isoformat(), "2454", "sell", -1_500.0, "auto_exit", "當沖"),
        )
        con.commit()
        con.close()
        assert dt_risk.get_today_dt_realized_pnl(db) == -1_500.0

    def test_unconfirmed_sell_still_excluded(self, db):
        """pnl 為 None（未確認成交）仍不得計入——既有行為不能退步。"""
        import dt_risk
        save_daily_trade(self._today_trade("2330", None, "daytrade"), db)
        assert dt_risk.get_today_dt_realized_pnl(db) == 0.0
