"""
tests/test_dt_backfill.py — 歷史預測回填引擎

為什麼需要回填：正式環境從 2026-08-21 起連續停擺，dt_prediction_log 幾乎沒有
樣本。等每日累積要等好幾個月才有統計意義；回填可以一次重建過去 N 個交易日的
「規則版預測」（dt_score + dt_rules，不含 LLM），立刻取得上千筆樣本供參數校準
與模擬損益驗證。

最重要的一組測試是「前視偏誤」（look-ahead bias）：
    T 日盤前的預測，只能看到 T-1 收盤為止的資料。
只要不小心用到 T 日的 K 線，回測績效就會漂亮到不真實，而且**不會有任何錯誤訊息**
——這正是 LESSONS.md 記錄的那一類「靜默失敗」。所以這裡用「餵進未來資料，
結果必須完全不變」的方式驗證，而不是只檢查切片長度。
"""
from datetime import date

import pytest

pd = pytest.importorskip("pandas")

import dt_backfill
from daytrading_db import DaytradingDB, DTPrediction


# ── 測試資料 ────────────────────────────────────────────────────────────────

def _make_df(n: int = 120, start_price: float = 100.0, end: date = date(2026, 6, 30)):
    """產生 n 根日 K，index 為連續日曆日（測試不需要真實交易日曆）。"""
    idx = pd.date_range(end=pd.Timestamp(end), periods=n, freq="D")
    closes = [start_price + i * 0.5 for i in range(n)]
    return pd.DataFrame(
        {
            "Open":   [c - 0.3 for c in closes],
            "High":   [c + 1.0 for c in closes],
            "Low":    [c - 1.0 for c in closes],
            "Close":  closes,
            "Volume": [1_000_000 + i * 1000 for i in range(n)],
        },
        index=idx,
    )


def _make_long_df(n: int = 120, end: date = date(2026, 6, 30)):
    """產生一定會通過 dt_rules long 條件的資料。

    需同時滿足：dt_score >= 6（靠量比 >= 2.0 加 2 分到 7）、量比 >= 1.5、
    多頭排列（價 > MA5 > MA10 > MA20）。單調上漲即可造出多頭排列，
    最後一根爆量造出量比。
    """
    df = _make_df(n=n, end=end)
    vols = list(df["Volume"])
    # 爆量要放在 T-1（index -2），不是 T（index -1）——T 日那根會被
    # slice_before 正確地排除掉。第一版把它放在 -1，結果量比條件永遠不成立。
    vols[-2] = int(sum(vols[-7:-2]) / 5 * 3)
    df = df.copy()
    df["Volume"] = vols
    return df


def _future_rows(df, n: int = 5, jump: float = 50.0):
    """在 df 後面接上 n 根「未來」K 線，且刻意大漲——若被用到，指標必然改變。"""
    last = df.index[-1]
    idx = pd.date_range(start=last + pd.Timedelta(days=1), periods=n, freq="D")
    base = float(df["Close"].iloc[-1]) + jump
    closes = [base + i for i in range(n)]
    future = pd.DataFrame(
        {
            "Open":   [c - 0.3 for c in closes],
            "High":   [c + 1.0 for c in closes],
            "Low":    [c - 1.0 for c in closes],
            "Close":  closes,
            "Volume": [9_000_000] * n,
        },
        index=idx,
    )
    return pd.concat([df, future])


# ══════════════════════════════════════════════════════════════════════════════
# ★ 核心：前視偏誤
# ══════════════════════════════════════════════════════════════════════════════

class TestNoLookAhead:
    def test_slice_before_excludes_the_trade_date_itself(self):
        """T 日盤前只能看到 T-1 收盤，T 日自己那根 K 不得入列。"""
        df = _make_df(n=120, end=date(2026, 6, 30))
        sliced = dt_backfill.slice_before(df, date(2026, 6, 30))

        assert len(sliced) == 119
        assert sliced.index[-1].date() == date(2026, 6, 29)
        assert all(ts.date() < date(2026, 6, 30) for ts in sliced.index)

    def test_slice_before_is_empty_when_date_precedes_history(self):
        df = _make_df(n=120, end=date(2026, 6, 30))
        assert len(dt_backfill.slice_before(df, date(2020, 1, 1))) == 0

    def test_indicators_identical_with_and_without_future_bars(self):
        """★ 最重要的一條 ★

        同一個交易日，一份 df 只到 T-1、另一份多接了 5 根大漲的未來 K 線。
        兩者算出的指標必須「完全相同」——只要有任何一個欄位不同，就代表
        未來資料洩漏進了預測。
        """
        trade_date = date(2026, 6, 30)
        clean  = _make_df(n=120, end=trade_date)
        leaked = _future_rows(clean, n=5, jump=50.0)

        ind_clean  = dt_backfill.indicators_as_of(clean,  trade_date)
        ind_leaked = dt_backfill.indicators_as_of(leaked, trade_date)

        assert ind_clean is not None
        assert ind_clean == ind_leaked, "未來資料洩漏進指標計算"

    def test_prediction_identical_with_and_without_future_bars(self):
        """整條預測流程（不只指標）都不得受未來資料影響。"""
        trade_date = date(2026, 6, 30)
        clean  = _make_df(n=120, end=trade_date)
        leaked = _future_rows(clean, n=5, jump=50.0)
        market = {"index_change_pct": 0.4, "futures_premium_pct": 0.1}

        p_clean  = dt_backfill.build_prediction("2330", "台積電", clean,  trade_date, market=market)
        p_leaked = dt_backfill.build_prediction("2330", "台積電", leaked, trade_date, market=market)

        assert p_clean is not None
        assert p_clean == p_leaked


# ══════════════════════════════════════════════════════════════════════════════
# 資料不足 / 邊界
# ══════════════════════════════════════════════════════════════════════════════

class TestInsufficientData:
    def test_returns_none_when_fewer_than_80_bars(self):
        """calculate_indicators 在 <80 筆時會 raise；回填必須吞掉並跳過該日，
        不能讓整批回填中斷（一支股票的資料不足不該炸掉其他 1,199 筆）。"""
        df = _make_df(n=60, end=date(2026, 6, 30))
        assert dt_backfill.indicators_as_of(df, date(2026, 6, 30)) is None

    def test_build_prediction_returns_none_when_indicators_unavailable(self):
        df = _make_df(n=60, end=date(2026, 6, 30))
        assert dt_backfill.build_prediction("2330", "台積電", df, date(2026, 6, 30)) is None

    def test_exactly_80_bars_is_enough(self):
        """邊界：切片後剛好 80 筆要算得出來（calculate_indicators 的門檻是 <80 才拒絕）。"""
        df = _make_df(n=81, end=date(2026, 6, 30))
        assert dt_backfill.indicators_as_of(df, date(2026, 6, 30)) is not None


# ══════════════════════════════════════════════════════════════════════════════
# 預測內容
# ══════════════════════════════════════════════════════════════════════════════

class TestPredictionContent:
    def _build(self, market, **kw):
        trade_date = kw.pop("trade_date", date(2026, 6, 30))
        df = _make_df(n=120, end=trade_date)
        return dt_backfill.build_prediction("2330", "台積電", df, trade_date, market=market, **kw)

    def test_marked_as_backfill_source(self):
        """回填資料必須可與每日真實累積的資料區分——統計時要能分開看。"""
        p = self._build({"index_change_pct": 0.4})
        assert p.source == "backfill"

    def test_date_is_the_trade_date(self):
        p = self._build({"index_change_pct": 0.4})
        assert p.date == "2026-06-30"

    def _build_long(self, trade_date=date(2026, 6, 30)):
        """量比爆量 + 多頭排列 → 規則必判 long。"""
        df = _make_long_df(n=121, end=trade_date)
        return dt_backfill.build_prediction(
            "2330", "台積電", df, trade_date,
            market={"index_change_pct": 0.4},
        )

    def test_rules_produce_long_for_bullish_high_volume(self):
        """這組資料若判不出 long，下面幾條價格測試就形同虛設——先釘住前提。"""
        assert self._build_long().action == "long"

    def test_long_gets_entry_target_stop(self):
        """規則判 long 時，進場區間 / 目標 / 停損都要有值，否則模擬層無從結算。"""
        p = self._build_long()
        assert p.entry_low is not None and p.entry_high is not None
        assert p.target_price is not None and p.stop_loss is not None

    def test_long_stop_loss_below_entry_range(self):
        """停損不得落在進場區間內，否則一進場就觸發。"""
        p = self._build_long()
        assert p.stop_loss < p.entry_low

    def test_long_target_above_entry_range(self):
        p = self._build_long()
        assert p.target_price > p.entry_high

    def test_long_prices_also_free_of_look_ahead(self):
        """★ long 路徑同樣不得受未來資料影響（價格計算用到 ATR/VWAP/壓力價，
        這些都來自指標，是最容易洩漏的地方）。"""
        trade_date = date(2026, 6, 30)
        clean = _make_long_df(n=121, end=trade_date)
        leaked = _future_rows(clean, n=5, jump=50.0)
        a = dt_backfill.build_prediction("2330", "台積電", clean, trade_date,
                                         market={"index_change_pct": 0.4})
        b = dt_backfill.build_prediction("2330", "台積電", leaked, trade_date,
                                         market={"index_change_pct": 0.4})
        assert a.action == "long"
        assert a == b

    def test_skip_has_no_price_levels(self):
        """大盤重挫 → 規則判 skip；skip 不得帶進場/目標/停損。"""
        p = self._build({"index_change_pct": -5.0})
        assert p.action == "skip"
        assert p.entry_low is None and p.entry_high is None
        assert p.target_price is None and p.stop_loss is None

    def test_reason_recorded_in_summary(self):
        """規則為什麼判 skip 要留下來，否則事後無法追溯。"""
        p = self._build({"index_change_pct": -5.0})
        assert p.ai_summary, "規則決策理由不得為空"


# ══════════════════════════════════════════════════════════════════════════════
# 大盤歷史漲跌幅
# ══════════════════════════════════════════════════════════════════════════════

class TestHistoricalMarket:
    def test_index_change_from_previous_two_closes(self):
        """T 日盤前看到的大盤，是 T-1 相對 T-2 的漲跌幅。"""
        idx = pd.date_range(end=pd.Timestamp(date(2026, 6, 29)), periods=3, freq="D")
        df = pd.DataFrame({"Close": [100.0, 110.0, 121.0]}, index=idx)
        # T = 6/30 → 看 6/29(121) vs 6/28(110) = +10%
        pct = dt_backfill.market_change_as_of(df, date(2026, 6, 30))
        assert pct == pytest.approx(10.0, abs=0.01)

    def test_returns_zero_when_history_too_short(self):
        idx = pd.date_range(end=pd.Timestamp(date(2026, 6, 29)), periods=1, freq="D")
        df = pd.DataFrame({"Close": [100.0]}, index=idx)
        assert dt_backfill.market_change_as_of(df, date(2026, 6, 30)) == 0.0

    def test_does_not_use_the_trade_date_close(self):
        """★ 前視偏誤：T 日的大盤收盤價絕不能用在 T 日盤前的判斷。"""
        idx = pd.date_range(end=pd.Timestamp(date(2026, 6, 30)), periods=3, freq="D")
        df = pd.DataFrame({"Close": [100.0, 110.0, 500.0]}, index=idx)   # 6/30 暴衝
        # 只能看到 6/29(110) vs 6/28(100) = +10%
        assert dt_backfill.market_change_as_of(df, date(2026, 6, 30)) == pytest.approx(10.0, abs=0.01)


# ══════════════════════════════════════════════════════════════════════════════
# 寫入 DB
# ══════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    @pytest.fixture
    def db(self, tmp_path):
        return DaytradingDB(str(tmp_path / "review.db"))

    def test_source_column_defaults_to_live(self, db):
        """既有的每日流程沒有傳 source，必須落成 'live'——回填才分得出來。"""
        db.save_predictions([DTPrediction(
            date="2026-06-30", code="2330", name="台積電", dt_score=7,
            action="long", entry_low=100.0, entry_high=101.0,
            target_price=105.0, stop_loss=97.0, ai_summary="",
        )])
        assert db.get_predictions("2026-06-30")[0]["source"] == "live"

    def test_backfill_does_not_overwrite_live_prediction(self, db):
        """同一天同一支若已有真實預測，回填不得覆蓋——真實紀錄優先。"""
        db.save_predictions([DTPrediction(
            date="2026-06-30", code="2330", name="台積電", dt_score=9,
            action="long", entry_low=100.0, entry_high=101.0,
            target_price=105.0, stop_loss=97.0, ai_summary="真實",
            source="live",
        )])
        db.save_predictions([DTPrediction(
            date="2026-06-30", code="2330", name="台積電", dt_score=3,
            action="skip", entry_low=None, entry_high=None,
            target_price=None, stop_loss=None, ai_summary="回填",
            source="backfill",
        )])
        rows = db.get_predictions("2026-06-30")
        assert len(rows) == 1
        assert rows[0]["source"] == "live"
        assert rows[0]["ai_summary"] == "真實"

    def test_get_predictions_can_filter_by_source(self, db):
        db.save_predictions([
            DTPrediction(date="2026-06-30", code="2330", name="台積電", dt_score=7,
                         action="long", entry_low=None, entry_high=None,
                         target_price=None, stop_loss=None, source="live"),
            DTPrediction(date="2026-06-30", code="2454", name="聯發科", dt_score=6,
                         action="skip", entry_low=None, entry_high=None,
                         target_price=None, stop_loss=None, source="backfill"),
        ])
        assert [r["code"] for r in db.get_predictions("2026-06-30", source="live")] == ["2330"]
        assert [r["code"] for r in db.get_predictions("2026-06-30", source="backfill")] == ["2454"]
        assert len(db.get_predictions("2026-06-30")) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 批次回填
# ══════════════════════════════════════════════════════════════════════════════

class TestRunBackfill:
    def test_skips_weekends(self, tmp_path):
        """回填不該替週末產生預測。"""
        df = _make_df(n=200, end=date(2026, 6, 30))
        db_path = str(tmp_path / "review.db")

        stats = dt_backfill.run_backfill(
            codes=[("2330", "台積電")],
            trade_dates=[date(2026, 6, 27), date(2026, 6, 28), date(2026, 6, 29)],  # 六、日、一
            history={"2330": df},
            index_history=None,
            db_path=db_path,
        )
        saved = DaytradingDB(db_path).get_predictions("2026-06-27")
        assert saved == []
        assert stats["skipped_non_trading"] == 2
        assert DaytradingDB(db_path).get_predictions("2026-06-29") != []

    def test_reports_counts(self, tmp_path):
        df = _make_df(n=200, end=date(2026, 6, 30))
        stats = dt_backfill.run_backfill(
            codes=[("2330", "台積電"), ("2454", "聯發科")],
            trade_dates=[date(2026, 6, 29), date(2026, 6, 30)],
            history={"2330": df, "2454": df},
            index_history=None,
            db_path=str(tmp_path / "review.db"),
        )
        assert stats["saved"] == 4
        assert stats["trade_dates"] == 2

    def test_missing_history_is_counted_not_fatal(self, tmp_path):
        """某支股票抓不到歷史，其他支必須照跑。"""
        df = _make_df(n=200, end=date(2026, 6, 30))
        stats = dt_backfill.run_backfill(
            codes=[("2330", "台積電"), ("9999", "查無此股")],
            trade_dates=[date(2026, 6, 30)],
            history={"2330": df},
            index_history=None,
            db_path=str(tmp_path / "review.db"),
        )
        assert stats["saved"] == 1
        assert stats["no_history"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# 交易日推算與股票池（純函式，不碰網路）
# ══════════════════════════════════════════════════════════════════════════════

class TestRecentTradeDates:
    def test_returns_requested_count(self):
        dates = dt_backfill.recent_trade_dates(10, end=date(2026, 6, 30))
        assert len(dates) == 10

    def test_excludes_weekends(self):
        dates = dt_backfill.recent_trade_dates(20, end=date(2026, 6, 30))
        assert all(d.weekday() < 5 for d in dates)

    def test_excludes_twse_holidays(self):
        from tw_trading_calendar import is_twse_holiday
        dates = dt_backfill.recent_trade_dates(40, end=date(2026, 6, 30))
        assert not any(is_twse_holiday(d) for d in dates)

    def test_sorted_oldest_first(self):
        dates = dt_backfill.recent_trade_dates(10, end=date(2026, 6, 30))
        assert dates == sorted(dates)

    def test_never_returns_dates_after_end(self):
        """★ 回填不得產生「未來日期」的預測——那會被之後的每日流程誤認為真實資料。"""
        end = date(2026, 6, 30)
        assert all(d <= end for d in dt_backfill.recent_trade_dates(10, end=end))

    def test_end_itself_included_when_trading_day(self):
        end = date(2026, 6, 30)   # 週二
        assert dt_backfill.recent_trade_dates(5, end=end)[-1] == end


class TestDefaultUniverse:
    def test_returns_code_name_pairs(self):
        universe = dt_backfill.default_universe()
        assert universe, "預設股票池不得為空"
        assert all(isinstance(c, str) and isinstance(n, str) for c, n in universe)

    def test_codes_are_unique(self):
        codes = [c for c, _ in dt_backfill.default_universe()]
        assert len(codes) == len(set(codes))
