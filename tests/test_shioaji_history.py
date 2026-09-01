"""
tests/test_shioaji_history.py — Shioaji kbars → 日線／分鐘 K 的純轉換

為什麼不用 yfinance
-------------------
1. yfinance 的 1 分鐘 K 只保留最近 7 天，回填 60 個交易日拿不到——而模擬層
   必須靠分鐘 K 才能判斷「當日先觸停利還是先觸停損」。只有日 OHLC 的話，
   兩者都碰到時只能保守假設停損先觸發，會系統性低估績效。
2. 台股資料品質：yfinance 常缺值、除權息調整不一致；Shioaji 是券商原始資料。

本檔只測「純轉換」——kbars dict → DataFrame、分鐘 K → 日線。網路 I/O 那層
維持極薄，不在單元測試範圍。
"""
from datetime import date, datetime

import pytest

pd = pytest.importorskip("pandas")

import shioaji_history as sh


def _kbars(rows):
    """rows: [(datetime, open, high, low, close, volume), ...] → Shioaji kbars dict。

    Shioaji 的 ts 是奈秒 epoch，欄位名為 Open/High/Low/Close/Volume。
    """
    return {
        "ts":     [int(pd.Timestamp(r[0]).value) for r in rows],
        "Open":   [r[1] for r in rows],
        "High":   [r[2] for r in rows],
        "Low":    [r[3] for r in rows],
        "Close":  [r[4] for r in rows],
        "Volume": [r[5] for r in rows],
    }


class TestKbarsToDataframe:
    def test_ts_becomes_datetime_index(self):
        kb = _kbars([
            (datetime(2026, 6, 29, 9, 0),  100.0, 101.0, 99.5, 100.5, 500),
            (datetime(2026, 6, 29, 9, 1),  100.5, 102.0, 100.0, 101.5, 700),
        ])
        df = sh.kbars_to_df(kb)
        assert list(df.index) == [pd.Timestamp("2026-06-29 09:00"),
                                  pd.Timestamp("2026-06-29 09:01")]

    def test_columns_match_ohlcv_convention(self):
        """欄位名必須與 technical_indicators._df_to_arrays 期待的一致
        （Open/High/Low/Close/Volume），否則指標計算會 KeyError。"""
        kb = _kbars([(datetime(2026, 6, 29, 9, 0), 1.0, 2.0, 0.5, 1.5, 10)])
        df = sh.kbars_to_df(kb)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            assert col in df.columns

    def test_empty_kbars_returns_none(self):
        assert sh.kbars_to_df(None) is None
        assert sh.kbars_to_df({}) is None
        assert sh.kbars_to_df({"ts": []}) is None

    def test_missing_column_returns_none(self):
        """kbars 缺欄位時回 None，不可讓半份資料流進指標計算。"""
        assert sh.kbars_to_df({"ts": [1], "Open": [1.0]}) is None

    def test_rows_sorted_by_time(self):
        """Shioaji 回傳理應已排序，但不能假設——亂序會讓 open/close 取錯。"""
        kb = _kbars([
            (datetime(2026, 6, 29, 9, 5), 105.0, 106.0, 104.0, 105.5, 100),
            (datetime(2026, 6, 29, 9, 0), 100.0, 101.0, 99.0, 100.5, 200),
        ])
        df = sh.kbars_to_df(kb)
        assert df.index[0] == pd.Timestamp("2026-06-29 09:00")


class TestResampleDaily:
    def _minute_df(self):
        rows = [
            # 6/29：開 100，最高 108，最低 97，收 105，量 1000
            (datetime(2026, 6, 29, 9, 0),  100.0, 103.0,  99.0, 102.0, 300),
            (datetime(2026, 6, 29, 10, 0), 102.0, 108.0,  97.0, 101.0, 400),
            (datetime(2026, 6, 29, 13, 0), 101.0, 106.0, 100.0, 105.0, 300),
            # 6/30：開 106，最高 110，最低 104，收 109，量 500
            (datetime(2026, 6, 30, 9, 0),  106.0, 110.0, 104.0, 108.0, 200),
            (datetime(2026, 6, 30, 13, 0), 108.0, 109.5, 107.0, 109.0, 300),
        ]
        return sh.kbars_to_df(_kbars(rows))

    def test_open_is_first_bar_of_day(self):
        daily = sh.resample_daily(self._minute_df())
        assert daily.loc["2026-06-29", "Open"] == 100.0
        assert daily.loc["2026-06-30", "Open"] == 106.0

    def test_close_is_last_bar_of_day(self):
        daily = sh.resample_daily(self._minute_df())
        assert daily.loc["2026-06-29", "Close"] == 105.0
        assert daily.loc["2026-06-30", "Close"] == 109.0

    def test_high_low_span_whole_day(self):
        daily = sh.resample_daily(self._minute_df())
        assert daily.loc["2026-06-29", "High"] == 108.0
        assert daily.loc["2026-06-29", "Low"] == 97.0

    def test_volume_is_summed(self):
        daily = sh.resample_daily(self._minute_df())
        assert daily.loc["2026-06-29", "Volume"] == 1000
        assert daily.loc["2026-06-30", "Volume"] == 500

    def test_one_row_per_trading_day(self):
        daily = sh.resample_daily(self._minute_df())
        assert len(daily) == 2

    def test_index_is_datetime_so_slice_before_works(self):
        """★ dt_backfill.slice_before 靠 index 的 .date() 判斷，型別錯就會
        整個前視偏誤防護失效。"""
        daily = sh.resample_daily(self._minute_df())
        assert all(hasattr(ts, "date") for ts in daily.index)

    def test_empty_input_returns_none(self):
        assert sh.resample_daily(None) is None


class TestBarsForDay:
    """模擬層要用的：取某一天的分鐘 K（判斷先觸停利還是停損）。"""

    def _df(self):
        rows = [
            (datetime(2026, 6, 29, 9, 0),  100.0, 101.0, 99.0, 100.0, 100),
            (datetime(2026, 6, 30, 9, 0),  106.0, 110.0, 104.0, 108.0, 200),
            (datetime(2026, 6, 30, 9, 1),  108.0, 112.0, 107.0, 111.0, 300),
        ]
        return sh.kbars_to_df(_kbars(rows))

    def test_returns_only_that_day(self):
        bars = sh.bars_for_day(self._df(), date(2026, 6, 30))
        assert len(bars) == 2

    def test_preserves_chronological_order(self):
        """順序就是一切——模擬層靠它判斷誰先觸發。"""
        bars = sh.bars_for_day(self._df(), date(2026, 6, 30))
        assert bars[0]["high"] == 110.0 and bars[1]["high"] == 112.0

    def test_returns_dicts_with_lowercase_keys(self):
        """與 daytrading_review._determine_outcome 既有的 bar 格式一致
        （open/high/low/close），才能直接沿用那套判斷。"""
        bars = sh.bars_for_day(self._df(), date(2026, 6, 30))
        assert set(bars[0]) >= {"open", "high", "low", "close"}

    def test_missing_day_returns_empty(self):
        assert sh.bars_for_day(self._df(), date(2026, 1, 1)) == []


class TestChunking:
    """kbars 一次抓太長會爆量：81 檔 × 400 天 × 270 根 ≈ 870 萬根。
    分段抓、抓完立刻聚合成日線再丟掉分鐘資料，記憶體才守得住。"""

    def test_splits_range_into_chunks(self):
        chunks = sh.date_chunks(date(2026, 1, 1), date(2026, 3, 31), chunk_days=30)
        assert chunks[0][0] == date(2026, 1, 1)
        assert chunks[-1][1] == date(2026, 3, 31)
        assert all((e - s).days < 30 for s, e in chunks)

    def test_chunks_are_contiguous_without_gaps_or_overlap(self):
        """漏一天 → 指標少一根 K；重複一天 → 聚合出重複日線。兩者都會靜默污染資料。"""
        chunks = sh.date_chunks(date(2026, 1, 1), date(2026, 3, 31), chunk_days=30)
        for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:]):
            assert (next_start - prev_end).days == 1

    def test_single_chunk_when_range_is_short(self):
        chunks = sh.date_chunks(date(2026, 1, 1), date(2026, 1, 10), chunk_days=30)
        assert chunks == [(date(2026, 1, 1), date(2026, 1, 10))]


class TestIndexContract:
    """加權指數在 Shioaji 是 Indexs.TSE 的 "001"，與個股不同路徑。
    取不到時必須回 None 讓呼叫端走「大盤缺值」路徑，不可拋出。"""

    class _FakeIndex:
        def __init__(self, code="001"):
            self.code = code

    def _api(self, indexs=None, raises=False):
        class C:
            pass
        class Api:
            pass
        api = Api()
        api.Contracts = C()
        if raises:
            class Boom:
                def __getattr__(self, _):
                    raise RuntimeError("contracts not ready")
            api.Contracts = Boom()
        else:
            api.Contracts.Indexs = C()
            api.Contracts.Indexs.TSE = indexs
        return api

    def test_finds_tse_001_by_subscript(self):
        idx = {"001": self._FakeIndex()}
        assert sh.resolve_index_contract(self._api(idx)) is idx["001"]

    def test_returns_none_when_index_missing(self):
        assert sh.resolve_index_contract(self._api({})) is None

    def test_returns_none_when_contracts_unavailable(self):
        """券商未登入 / 合約未下載完成時不得拋出——8:30 這是常態。"""
        assert sh.resolve_index_contract(self._api(raises=True)) is None

    def test_returns_none_for_none_api(self):
        assert sh.resolve_index_contract(None) is None


class TestBarsForDayCarriesTime:
    """模擬層要靠時間戳判斷強制平倉（13:15），沒有它只能用收盤價結算。"""

    def _df(self):
        rows = [
            (datetime(2026, 6, 30, 9, 0),  106.0, 110.0, 104.0, 108.0, 200),
            (datetime(2026, 6, 30, 13, 20), 108.0, 112.0, 107.0, 111.0, 300),
        ]
        return sh.kbars_to_df(_kbars(rows))

    def test_time_present_and_formatted(self):
        bars = sh.bars_for_day(self._df(), date(2026, 6, 30))
        assert [b["time"] for b in bars] == ["09:00", "13:20"]

    def test_ohlc_still_present(self):
        bars = sh.bars_for_day(self._df(), date(2026, 6, 30))
        assert set(bars[0]) >= {"open", "high", "low", "close"}
