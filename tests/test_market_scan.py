import pytest

from market_scan import (
    get_top_gainers,
    get_top_losers,
    get_top_volume,
    search_stocks,
    normalize_snapshot_row,
)


def make_snap(code: str, name: str, change_rate: float, volume: int, close: float = 100.0) -> dict:
    return {
        "code": code, "name": name,
        "change_rate": change_rate, "total_volume": volume,
        "close": close, "change_price": close * change_rate / 100,
    }


SNAPSHOTS = {
    "2330": make_snap("2330", "台積電", 3.5,  50000, 850),
    "2317": make_snap("2317", "鴻海",   -2.1, 80000, 180),
    "2454": make_snap("2454", "聯發科", 5.2,  20000, 1200),
    "2303": make_snap("2303", "聯電",   -0.5, 30000, 55),
    "0050": make_snap("0050", "元大50", 1.1,  90000, 145),
}


class TestGetTopGainers:
    def test_sorted_by_change_rate_desc(self):
        result = get_top_gainers(SNAPSHOTS, n=3)
        rates = [r["change_rate"] for r in result]
        assert rates == sorted(rates, reverse=True)

    def test_respects_n(self):
        result = get_top_gainers(SNAPSHOTS, n=2)
        assert len(result) == 2

    def test_first_is_highest(self):
        result = get_top_gainers(SNAPSHOTS, n=1)
        assert result[0]["code"] == "2454"

    def test_empty_snapshots(self):
        assert get_top_gainers({}, n=5) == []


class TestGetTopLosers:
    def test_sorted_by_change_rate_asc(self):
        result = get_top_losers(SNAPSHOTS, n=3)
        rates = [r["change_rate"] for r in result]
        assert rates == sorted(rates)

    def test_first_is_lowest(self):
        result = get_top_losers(SNAPSHOTS, n=1)
        assert result[0]["code"] == "2317"

    def test_respects_n(self):
        assert len(get_top_losers(SNAPSHOTS, n=2)) == 2


class TestGetTopVolume:
    def test_sorted_by_volume_desc(self):
        result = get_top_volume(SNAPSHOTS, n=3)
        vols = [r["total_volume"] for r in result]
        assert vols == sorted(vols, reverse=True)

    def test_first_is_highest_volume(self):
        result = get_top_volume(SNAPSHOTS, n=1)
        assert result[0]["code"] == "0050"

    def test_respects_n(self):
        assert len(get_top_volume(SNAPSHOTS, n=2)) == 2


class TestSearchStocks:
    def test_search_by_name(self):
        result = search_stocks(SNAPSHOTS, "台積電")
        assert any(r["code"] == "2330" for r in result)

    def test_search_by_code(self):
        result = search_stocks(SNAPSHOTS, "2454")
        assert any(r["code"] == "2454" for r in result)

    def test_partial_name_match(self):
        result = search_stocks(SNAPSHOTS, "聯")
        codes = [r["code"] for r in result]
        assert "2454" in codes
        assert "2303" in codes

    def test_no_match_returns_empty(self):
        result = search_stocks(SNAPSHOTS, "不存在的公司XYZ")
        assert result == []

    def test_empty_query_returns_all(self):
        result = search_stocks(SNAPSHOTS, "")
        assert len(result) == len(SNAPSHOTS)

    def test_case_insensitive_code(self):
        result = search_stocks(SNAPSHOTS, "2330")
        assert len(result) >= 1


class TestNormalizeSnapshotRow:
    def test_returns_required_keys(self):
        snap = make_snap("2330", "台積電", 2.0, 50000, 850)
        row = normalize_snapshot_row("2330", snap)
        for key in ("code", "name", "close", "change_rate", "change_price", "total_volume"):
            assert key in row

    def test_code_preserved(self):
        snap = make_snap("2330", "台積電", 2.0, 50000)
        row = normalize_snapshot_row("2330", snap)
        assert row["code"] == "2330"

    def test_name_from_snapshot(self):
        snap = make_snap("2330", "台積電", 2.0, 50000)
        row = normalize_snapshot_row("2330", snap)
        assert row["name"] == "台積電"
