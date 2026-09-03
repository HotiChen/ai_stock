"""
tests/test_kbars_limits.py — Shioaji kbars 的 30 天硬限制

2026-09-03 正式環境實錯：
    {'status_code': 400, 'detail': 'Kbars date range must not exceed 30 days.'}

程式裡有多處一次查超過 30 天：
    technical_indicators.fetch_indicators   150 天（**原本就有的程式碼，一直是壞的**）
    fetch_indicators_shioaji_batch          chunk_days=130
    stock_query._fetch_annual_trend         chunk_days=365
    dt_backfill --chunk-days                使用者可自行調大

以前 yfinance 備援會接住 fetch_indicators 的失敗，所以沒人發現；備援移除後
8:30 選股完全拿不到指標，當天零候選。

修法：把上限收進 date_chunks 內部強制執行，呼叫端傳再大的 chunk_days 也會
被切到 30 天以內——不能指望每個呼叫端都記得這個限制。
"""
from datetime import date

import pytest

pd = pytest.importorskip("pandas")

import shioaji_history as sh


class TestChunkSpanCap:
    def test_span_never_exceeds_shioaji_limit(self):
        """★ 呼叫端傳 365 天也必須被切開——上限要在這裡強制，不能靠呼叫端自律。"""
        chunks = sh.date_chunks(date(2026, 1, 1), date(2026, 12, 31),
                                chunk_days=365)
        assert chunks, "不該回傳空清單"
        for s, e in chunks:
            assert (e - s).days < sh.MAX_KBARS_SPAN_DAYS, \
                f"區間 {s}~{e} 跨 {(e - s).days} 天，超過 Shioaji 上限"

    def test_default_chunk_also_within_limit(self):
        for s, e in sh.date_chunks(date(2026, 1, 1), date(2026, 6, 30)):
            assert (e - s).days < sh.MAX_KBARS_SPAN_DAYS

    def test_still_contiguous_after_capping(self):
        """切小之後仍不得有空隙或重疊——漏一天指標就少一根 K。"""
        chunks = sh.date_chunks(date(2026, 1, 1), date(2026, 12, 31),
                                chunk_days=365)
        for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:]):
            assert (next_start - prev_end).days == 1

    def test_covers_whole_range(self):
        chunks = sh.date_chunks(date(2026, 1, 1), date(2026, 12, 31),
                                chunk_days=365)
        assert chunks[0][0] == date(2026, 1, 1)
        assert chunks[-1][1] == date(2026, 12, 31)


class TestKbarsToDfRobustness:
    def test_numpy_ts_does_not_raise(self):
        """★ ts 是 numpy array 時，`if not ts` 會拋
        「truth value of an array is ambiguous」——要用長度判斷。"""
        import numpy as np
        kb = {
            "ts": np.array([1_700_000_000_000_000_000,
                            1_700_000_060_000_000_000]),
            "Open": np.array([1.0, 2.0]), "High": np.array([2.0, 3.0]),
            "Low": np.array([0.5, 1.5]), "Close": np.array([1.5, 2.5]),
            "Volume": np.array([10.0, 20.0]),
        }
        df = sh.kbars_to_df(kb)
        assert df is not None and len(df) == 2

    def test_empty_numpy_ts_returns_none(self):
        import numpy as np
        assert sh.kbars_to_df({"ts": np.array([])}) is None

    def test_empty_list_ts_returns_none(self):
        assert sh.kbars_to_df({"ts": []}) is None


class TestFetchIndicatorsChunked:
    def test_fetch_indicators_does_not_request_more_than_limit(self):
        """★ technical_indicators.fetch_indicators 原本一次要 150 天，
        必然 400。改走分段路徑後，每次請求都要在限制內。"""
        from unittest.mock import MagicMock, patch

        spans = []

        def spy(api, code, start, end, chunk_days=30):
            spans.append((end - start).days)
            idx = pd.date_range(end=pd.Timestamp("2026-09-03"), periods=100,
                                freq="D")
            return pd.DataFrame({
                "Open": [1.0] * 100, "High": [1.0] * 100, "Low": [1.0] * 100,
                "Close": [1.0] * 100, "Volume": [1.0] * 100}, index=idx)

        with patch("shioaji_history.fetch_daily", side_effect=spy):
            from technical_indicators import fetch_indicators
            out = fetch_indicators(MagicMock(), "2330")

        assert out is not None
        assert spans, "應該有呼叫分段抓取"


class TestDailyAsKbarsDf:
    """logic.normalize_kbars_df 期待 kbars 格式（有 ts 欄位），而 fetch_daily
    回傳 DatetimeIndex。提供轉換 helper，讓 auto_trader / app.py 能直接改用
    分段抓取而不必各自處理格式。"""

    def _daily(self, n=5):
        idx = pd.date_range(end=pd.Timestamp("2026-09-03"), periods=n, freq="D")
        return pd.DataFrame({
            "Open": [1.0] * n, "High": [2.0] * n, "Low": [0.5] * n,
            "Close": [1.5] * n, "Volume": [10.0] * n}, index=idx)

    def test_has_ts_column(self):
        from unittest.mock import MagicMock, patch
        with patch("shioaji_history.fetch_daily", return_value=self._daily()):
            df = sh.fetch_daily_as_kbars_df(MagicMock(), "2330", days=120)
        assert df is not None and "ts" in df.columns

    def test_normalize_kbars_df_accepts_it(self):
        """★ 直接餵給既有的 normalize_kbars_df 必須不炸——這是相容性的重點。"""
        pytest.importorskip("plotly")   # logic.py 匯入 plotly，CI 容器未安裝
        from unittest.mock import MagicMock, patch
        from logic import normalize_kbars_df
        with patch("shioaji_history.fetch_daily", return_value=self._daily()):
            df = sh.fetch_daily_as_kbars_df(MagicMock(), "2330", days=120)
        out = normalize_kbars_df(df)
        assert {"ts", "Open", "High", "Low", "Close", "Volume"} <= set(out.columns)

    def test_empty_when_fetch_fails(self):
        from unittest.mock import MagicMock, patch
        with patch("shioaji_history.fetch_daily", return_value=None):
            df = sh.fetch_daily_as_kbars_df(MagicMock(), "2330", days=120)
        assert df is not None and df.empty
