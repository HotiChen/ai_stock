import pytest
from themes import (
    ThemeStats,
    calc_theme_stats,
    get_theme_detail_rows,
    rank_themes,
    get_theme_momentum_label,
    THEME_GROUPS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_snapshot(change_rate: float, volume: int = 10000, close: float = 100.0) -> dict:
    return {
        "change_rate": change_rate,
        "change_price": close * change_rate / 100,
        "close": close,
        "total_volume": volume,
        "buy_price": close - 0.5,
        "sell_price": close + 0.5,
    }


def make_snapshots(*change_rates) -> dict[str, dict]:
    return {f"stock{i}": make_snapshot(r) for i, r in enumerate(change_rates)}


# ── THEME_GROUPS ──────────────────────────────────────────────────────────────

class TestThemeGroups:
    def test_at_least_5_themes(self):
        assert len(THEME_GROUPS) >= 5

    def test_each_theme_has_stocks(self):
        for name, stocks in THEME_GROUPS.items():
            assert len(stocks) >= 3, f"{name} 應該至少 3 支股票"

    def test_stock_codes_are_strings(self):
        for stocks in THEME_GROUPS.values():
            for code in stocks:
                assert isinstance(code, str)

    def test_no_duplicate_names(self):
        assert len(THEME_GROUPS) == len(set(THEME_GROUPS.keys()))


# ── calc_theme_stats ──────────────────────────────────────────────────────────

class TestCalcThemeStats:
    def test_basic_calculation(self):
        snapshots = make_snapshots(3.0, -1.0, 2.0)
        stocks = list(snapshots.keys())
        result = calc_theme_stats("測試題材", stocks, snapshots)
        assert result.name == "測試題材"
        assert result.avg_change_pct == pytest.approx(4/3, abs=0.01)

    def test_advance_decline_count(self):
        snapshots = make_snapshots(2.0, -1.0, 3.0, -0.5, 0.0)
        stocks = list(snapshots.keys())
        result = calc_theme_stats("題材", stocks, snapshots)
        assert result.advance_count == 2
        assert result.decline_count == 2

    def test_flat_counted_as_neither(self):
        snapshots = make_snapshots(0.0, 0.0)
        stocks = list(snapshots.keys())
        result = calc_theme_stats("題材", stocks, snapshots)
        assert result.advance_count == 0
        assert result.decline_count == 0

    def test_total_volume_summed(self):
        snapshots = {
            "a": make_snapshot(1.0, volume=5000),
            "b": make_snapshot(2.0, volume=3000),
        }
        result = calc_theme_stats("題材", ["a", "b"], snapshots)
        assert result.total_volume == 8000

    def test_strongest_stock_identified(self):
        snapshots = {
            "weak": make_snapshot(-2.0),
            "mid":  make_snapshot(1.0),
            "best": make_snapshot(5.0),
        }
        result = calc_theme_stats("題材", list(snapshots.keys()), snapshots)
        assert result.strongest_stock == "best"

    def test_weakest_stock_identified(self):
        snapshots = {
            "weak": make_snapshot(-3.0),
            "mid":  make_snapshot(1.0),
            "best": make_snapshot(5.0),
        }
        result = calc_theme_stats("題材", list(snapshots.keys()), snapshots)
        assert result.weakest_stock == "weak"

    def test_missing_snapshots_skipped(self):
        snapshots = {"stock0": make_snapshot(2.0)}
        result = calc_theme_stats("題材", ["stock0", "missing"], snapshots)
        assert result.avg_change_pct == pytest.approx(2.0)
        assert result.stock_count == 1

    def test_all_missing_returns_neutral(self):
        result = calc_theme_stats("題材", ["x", "y"], {})
        assert result.avg_change_pct == 0.0
        assert result.stock_count == 0

    def test_result_has_all_fields(self):
        snapshots = make_snapshots(1.0)
        result = calc_theme_stats("題材", list(snapshots.keys()), snapshots)
        assert hasattr(result, "name")
        assert hasattr(result, "avg_change_pct")
        assert hasattr(result, "advance_count")
        assert hasattr(result, "decline_count")
        assert hasattr(result, "total_volume")
        assert hasattr(result, "strongest_stock")
        assert hasattr(result, "weakest_stock")
        assert hasattr(result, "stock_count")
        assert hasattr(result, "stocks")


# ── rank_themes ───────────────────────────────────────────────────────────────

class TestRankThemes:
    def _make_stats(self, name: str, avg: float) -> ThemeStats:
        return ThemeStats(
            name=name, stocks=[], avg_change_pct=avg,
            advance_count=0, decline_count=0, total_volume=0,
            strongest_stock="", weakest_stock="", stock_count=1,
        )

    def test_sorted_by_avg_change_descending(self):
        stats = [
            self._make_stats("A", 1.0),
            self._make_stats("B", 3.0),
            self._make_stats("C", -2.0),
        ]
        result = rank_themes(stats)
        assert result[0].name == "B"
        assert result[1].name == "A"
        assert result[2].name == "C"

    def test_empty_list_returns_empty(self):
        assert rank_themes([]) == []

    def test_single_item_returns_single(self):
        stats = [self._make_stats("A", 2.0)]
        assert len(rank_themes(stats)) == 1

    def test_equal_values_stable(self):
        stats = [self._make_stats("A", 1.0), self._make_stats("B", 1.0)]
        result = rank_themes(stats)
        assert len(result) == 2


# ── get_theme_detail_rows ─────────────────────────────────────────────────────

DETAIL_SNAPS = {
    "2330": {"name": "台積電", "close": 850.0, "change_rate": 3.5, "change_price": 28.5, "total_volume": 50000},
    "2454": {"name": "聯發科", "close": 1200.0, "change_rate": 5.2, "change_price": 59.5, "total_volume": 20000},
    "2317": {"name": "鴻海",   "close": 180.0,  "change_rate": -2.1, "change_price": -3.9, "total_volume": 80000},
}
DETAIL_CODES = ["2330", "2454", "2317", "9999"]  # 9999 has no snapshot


class TestGetThemeDetailRows:
    def test_sorted_by_change_rate_desc(self):
        rows = get_theme_detail_rows("AI", DETAIL_CODES, DETAIL_SNAPS)
        rates = [r["change_rate"] for r in rows if r["has_data"]]
        assert rates == sorted(rates, reverse=True)

    def test_includes_all_codes(self):
        rows = get_theme_detail_rows("AI", DETAIL_CODES, DETAIL_SNAPS)
        codes = [r["code"] for r in rows]
        for c in DETAIL_CODES:
            assert c in codes

    def test_name_from_snapshot(self):
        rows = get_theme_detail_rows("AI", ["2330"], DETAIL_SNAPS)
        assert rows[0]["name"] == "台積電"

    def test_name_fallback_to_code(self):
        rows = get_theme_detail_rows("AI", ["9999"], DETAIL_SNAPS)
        assert rows[0]["name"] == "9999"

    def test_has_data_true_when_snap_exists(self):
        rows = get_theme_detail_rows("AI", ["2330"], DETAIL_SNAPS)
        assert rows[0]["has_data"] is True

    def test_has_data_false_when_no_snap(self):
        rows = get_theme_detail_rows("AI", ["9999"], DETAIL_SNAPS)
        assert rows[0]["has_data"] is False

    def test_close_from_snapshot(self):
        rows = get_theme_detail_rows("AI", ["2330"], DETAIL_SNAPS)
        assert rows[0]["close"] == pytest.approx(850.0)

    def test_missing_code_defaults_to_zero(self):
        rows = get_theme_detail_rows("AI", ["9999"], DETAIL_SNAPS)
        assert rows[0]["close"] == 0.0
        assert rows[0]["change_rate"] == 0.0
        assert rows[0]["total_volume"] == 0

    def test_empty_codes_returns_empty(self):
        rows = get_theme_detail_rows("AI", [], DETAIL_SNAPS)
        assert rows == []

    def test_empty_snapshots_all_no_data(self):
        rows = get_theme_detail_rows("AI", ["2330", "2454"], {})
        assert all(not r["has_data"] for r in rows)

    def test_no_data_rows_sorted_after_data_rows(self):
        rows = get_theme_detail_rows("AI", DETAIL_CODES, DETAIL_SNAPS)
        has_data = [r for r in rows if r["has_data"]]
        no_data = [r for r in rows if not r["has_data"]]
        # has_data rows come first, then no_data rows at the end
        data_indices = [i for i, r in enumerate(rows) if r["has_data"]]
        no_data_indices = [i for i, r in enumerate(rows) if not r["has_data"]]
        if data_indices and no_data_indices:
            assert max(data_indices) < min(no_data_indices)


# ── get_theme_momentum_label ──────────────────────────────────────────────────

class TestGetThemeMomentumLabel:
    def test_strong_up(self):
        label = get_theme_momentum_label(3.5)
        assert "強漲" in label or "🔥" in label

    def test_mild_up(self):
        label = get_theme_momentum_label(1.0)
        assert "漲" in label or "↑" in label

    def test_flat(self):
        label = get_theme_momentum_label(0.1)
        assert "平" in label or "→" in label or "持平" in label

    def test_mild_down(self):
        label = get_theme_momentum_label(-1.0)
        assert "跌" in label or "↓" in label

    def test_strong_down(self):
        label = get_theme_momentum_label(-3.5)
        assert "重跌" in label or "❄" in label or "跌" in label
