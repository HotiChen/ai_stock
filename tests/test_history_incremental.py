"""
tests/test_history_incremental.py — 日線增量快取

問題：8:30 選股對每支候選呼叫 fetch_indicators，它抓 150 天的分鐘 K 來算
日線指標。50 支候選 = 7,500 個股票日的分鐘資料，每天重抓一次——而其中
149 天的資料昨天就抓過了，只有最後一根是新的。

Shioaji 歷史資料每日上限 500 MB。2026-09-02 就是這樣被燒光的，之後
8:30 選股完全拿不到指標。

修法：日線落地快取，之後只抓「快取最後一天」到「今天」的缺口。
穩態下每天每支只抓 1 天，用量降到原本的 1/150。
"""
from datetime import date

import pytest

pd = pytest.importorskip("pandas")

import shioaji_history as sh


def _df(start: str, n: int, base: float = 100.0):
    idx = pd.date_range(start=start, periods=n, freq="D")
    return pd.DataFrame({
        "Open": [base + i for i in range(n)],
        "High": [base + i + 1 for i in range(n)],
        "Low": [base + i - 1 for i in range(n)],
        "Close": [base + i for i in range(n)],
        "Volume": [1000.0] * n,
    }, index=idx)


class TestMergeDaily:
    def test_concatenates_and_sorts(self):
        a = _df("2026-01-01", 3)
        b = _df("2026-01-04", 2, base=200.0)
        out = sh.merge_daily(a, b)
        assert len(out) == 5
        assert list(out.index) == sorted(out.index)

    def test_overlap_keeps_newer(self):
        """★ 重疊日期要保留新抓的那份——舊快取可能是盤中抓的（收盤價還沒定）。"""
        old = _df("2026-01-01", 3)                      # Close 100,101,102
        new = _df("2026-01-03", 2, base=999.0)          # 1/3 的 Close = 999
        out = sh.merge_daily(old, new)
        assert out.loc["2026-01-03", "Close"] == 999.0
        assert len(out) == 4

    def test_none_inputs(self):
        a = _df("2026-01-01", 2)
        assert sh.merge_daily(None, a) is a
        assert sh.merge_daily(a, None) is a
        assert sh.merge_daily(None, None) is None


class TestMissingRange:
    def test_full_range_when_no_cache(self):
        assert sh.missing_range(None, date(2026, 1, 1), date(2026, 3, 1)) == \
            (date(2026, 1, 1), date(2026, 3, 1))

    def test_only_tail_when_cache_covers_start(self):
        """★ 這條就是省流量的核心：快取到 2/27，只抓 2/27 起的尾巴。

        起點刻意含快取的最後一天（而非它的隔天）：那一天可能是盤中抓的，
        收盤價還沒定案。多抓一天的成本微不足道，抓到半根 K 卻會讓指標算錯。
        """
        cached = _df("2026-01-01", 58)   # 1/1 ~ 2/27
        gap = sh.missing_range(cached, date(2026, 1, 1), date(2026, 3, 1))
        assert gap == (date(2026, 2, 27), date(2026, 3, 1))
        assert (gap[1] - gap[0]).days <= 3, "只該抓尾巴，不是整段"

    def test_none_when_cache_already_current(self):
        """快取已涵蓋到今天 → 完全不用抓。"""
        cached = _df("2026-01-01", 60)   # 到 3/1
        assert sh.missing_range(cached, date(2026, 1, 1), date(2026, 3, 1)) is None

    def test_refetches_all_when_cache_starts_too_late(self):
        """快取起點晚於需求起點 → 前面缺一段，只能整段重抓
        （kbars 無法只抓中間，且缺頭會讓指標算錯）。"""
        cached = _df("2026-02-01", 29)
        gap = sh.missing_range(cached, date(2026, 1, 1), date(2026, 3, 1))
        assert gap == (date(2026, 1, 1), date(2026, 3, 1))


class TestFetchDailyCached:
    def _patch_fetch(self, monkeypatch, calls):
        def fake(api, code, start, end, chunk_days=30):
            calls.append((start, end))
            days = (end - start).days + 1
            return _df(start.isoformat(), days, base=500.0)
        monkeypatch.setattr(sh, "fetch_daily", fake)

    def test_first_call_fetches_full_range(self, tmp_path, monkeypatch):
        calls = []
        self._patch_fetch(monkeypatch, calls)
        out = sh.fetch_daily_cached(object(), "2330", date(2026, 1, 1),
                                    date(2026, 3, 1), cache_dir=str(tmp_path))
        assert out is not None
        assert calls == [(date(2026, 1, 1), date(2026, 3, 1))]

    def test_second_call_fetches_only_the_gap(self, tmp_path, monkeypatch):
        """★ 第二次只抓缺口——這就是「不要一直重複下載」。"""
        calls = []
        self._patch_fetch(monkeypatch, calls)
        sh.fetch_daily_cached(object(), "2330", date(2026, 1, 1),
                              date(2026, 3, 1), cache_dir=str(tmp_path))
        calls.clear()
        sh.fetch_daily_cached(object(), "2330", date(2026, 1, 1),
                              date(2026, 3, 5), cache_dir=str(tmp_path))
        assert len(calls) == 1
        gap_start, gap_end = calls[0]
        assert gap_end == date(2026, 3, 5)
        assert (gap_end - gap_start).days <= 5, "只該抓幾天，不是整段重抓"

    def test_no_fetch_when_cache_current(self, tmp_path, monkeypatch):
        calls = []
        self._patch_fetch(monkeypatch, calls)
        sh.fetch_daily_cached(object(), "2330", date(2026, 1, 1),
                              date(2026, 3, 1), cache_dir=str(tmp_path))
        calls.clear()
        out = sh.fetch_daily_cached(object(), "2330", date(2026, 1, 1),
                                    date(2026, 3, 1), cache_dir=str(tmp_path))
        assert calls == [], "快取已涵蓋範圍時不得再打 API"
        assert out is not None

    def test_returns_cache_when_fetch_fails(self, tmp_path, monkeypatch):
        """★ 額度用完時，仍要回傳快取裡的舊資料——有點舊總比完全沒有好。
        2026-09-02 的 8:30 選股就是因為額度歸零而一支都算不出指標。"""
        calls = []
        self._patch_fetch(monkeypatch, calls)
        sh.fetch_daily_cached(object(), "2330", date(2026, 1, 1),
                              date(2026, 3, 1), cache_dir=str(tmp_path))
        monkeypatch.setattr(sh, "fetch_daily", lambda *a, **k: None)
        out = sh.fetch_daily_cached(object(), "2330", date(2026, 1, 1),
                                    date(2026, 3, 20), cache_dir=str(tmp_path))
        assert out is not None and len(out) > 0

    def test_returns_none_when_no_cache_and_fetch_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sh, "fetch_daily", lambda *a, **k: None)
        assert sh.fetch_daily_cached(object(), "9999", date(2026, 1, 1),
                                     date(2026, 3, 1),
                                     cache_dir=str(tmp_path)) is None

    def test_cache_persisted_for_next_process(self, tmp_path, monkeypatch):
        calls = []
        self._patch_fetch(monkeypatch, calls)
        sh.fetch_daily_cached(object(), "2330", date(2026, 1, 1),
                              date(2026, 3, 1), cache_dir=str(tmp_path))
        assert sh.cache_load(str(tmp_path), "2330") is not None
