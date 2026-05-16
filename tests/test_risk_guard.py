from __future__ import annotations

"""
TDD tests for risk_guard.py

Covers:
- check_ex_dividend(stock_id) → bool (True = has upcoming ex-dividend)
- check_limit_up_down(stock_id, current_price, prev_close) → "limit_up"/"limit_down"/"normal"
- is_blacklisted(stock_id) → bool
- validate_plan(plan, capital, current_positions) → dict with approved/rejected picks
- Single position ≤5% capital enforcement (auto-reduce)
- Sector exposure ≤30% capital enforcement
"""

import pytest
from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_pick(code: str, name: str, budget: float, sector: str = "科技") -> dict:
    return {
        "code": code,
        "name": name,
        "budget": budget,
        "sector": sector,
        "signal": "buy",
        "confidence": 7,
    }


def _make_position(code: str, sector: str, value: float) -> dict:
    return {"code": code, "sector": sector, "value": value}


# ── check_ex_dividend ─────────────────────────────────────────────────────────

class TestCheckExDividend:
    @patch("risk_guard.requests.get")
    def test_stock_with_upcoming_ex_dividend_returns_true(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {"Code": "2330", "Date": "2026-05-10", "BeforeTradingDay": "2026-05-09"}
        ]
        from risk_guard import check_ex_dividend
        assert check_ex_dividend("2330") is True

    @patch("risk_guard.requests.get")
    def test_stock_without_ex_dividend_returns_false(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = []
        from risk_guard import check_ex_dividend
        assert check_ex_dividend("9999") is False

    @patch("risk_guard.requests.get")
    def test_api_failure_returns_false(self, mock_get):
        mock_get.side_effect = Exception("network error")
        from risk_guard import check_ex_dividend
        assert check_ex_dividend("2330") is False

    @patch("risk_guard.requests.get")
    def test_non_200_response_returns_false(self, mock_get):
        mock_get.return_value.status_code = 500
        mock_get.return_value.json.return_value = []
        from risk_guard import check_ex_dividend
        assert check_ex_dividend("2330") is False

    @patch("risk_guard.requests.get")
    def test_other_stock_code_in_response_returns_false(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {"Code": "2454", "Date": "2026-05-10", "BeforeTradingDay": "2026-05-09"}
        ]
        from risk_guard import check_ex_dividend
        assert check_ex_dividend("2330") is False


# ── check_limit_up_down ───────────────────────────────────────────────────────

class TestCheckLimitUpDown:
    def test_limit_up_returns_limit_up(self):
        from risk_guard import check_limit_up_down
        result = check_limit_up_down("2330", current_price=935.0, prev_close=850.0)
        assert result == "limit_up"

    def test_limit_down_returns_limit_down(self):
        from risk_guard import check_limit_up_down
        result = check_limit_up_down("2330", current_price=765.0, prev_close=850.0)
        assert result == "limit_down"

    def test_normal_price_returns_normal(self):
        from risk_guard import check_limit_up_down
        result = check_limit_up_down("2330", current_price=860.0, prev_close=850.0)
        assert result == "normal"

    def test_exact_10pct_up_is_limit_up(self):
        from risk_guard import check_limit_up_down
        result = check_limit_up_down("2330", current_price=935.0, prev_close=850.0)
        assert result == "limit_up"

    def test_exact_10pct_down_is_limit_down(self):
        from risk_guard import check_limit_up_down
        result = check_limit_up_down("2330", current_price=765.0, prev_close=850.0)
        assert result == "limit_down"

    def test_just_below_limit_up_is_normal(self):
        from risk_guard import check_limit_up_down
        result = check_limit_up_down("2330", current_price=934.0, prev_close=850.0)
        assert result == "normal"


# ── is_blacklisted ────────────────────────────────────────────────────────────

class TestIsBlacklisted:
    def test_blacklisted_stock_returns_true(self):
        from risk_guard import is_blacklisted
        assert is_blacklisted("BLACKTEST") is True

    def test_normal_stock_returns_false(self):
        from risk_guard import is_blacklisted
        assert is_blacklisted("2330") is False

    def test_blacklist_is_case_sensitive(self):
        from risk_guard import is_blacklisted
        assert is_blacklisted("blacktest") is False

    def test_empty_string_returns_false(self):
        from risk_guard import is_blacklisted
        assert is_blacklisted("") is False


# ── validate_plan: single position ≤5% ───────────────────────────────────────

class TestValidatePlanPositionLimit:
    def test_pick_within_5pct_is_approved(self):
        from risk_guard import validate_plan
        capital = 100_000.0
        picks = [_make_pick("2330", "台積電", 4_000.0)]
        result = validate_plan(picks, capital, current_positions=[])
        codes = [p["code"] for p in result["approved"]]
        assert "2330" in codes

    def test_pick_exceeding_5pct_is_reduced(self):
        from risk_guard import validate_plan
        capital = 100_000.0
        picks = [_make_pick("2330", "台積電", 8_000.0)]  # 8% > 5%
        result = validate_plan(picks, capital, current_positions=[])
        approved = {p["code"]: p for p in result["approved"]}
        assert "2330" in approved
        assert approved["2330"]["budget"] <= capital * 0.05

    def test_pick_reduced_reason_logged(self):
        from risk_guard import validate_plan
        capital = 100_000.0
        picks = [_make_pick("2330", "台積電", 8_000.0)]
        result = validate_plan(picks, capital, current_positions=[])
        approved = {p["code"]: p for p in result["approved"]}
        assert "reason" in approved["2330"]

    def test_exact_5pct_is_approved_unchanged(self):
        from risk_guard import validate_plan
        capital = 100_000.0
        picks = [_make_pick("2330", "台積電", 5_000.0)]
        result = validate_plan(picks, capital, current_positions=[])
        approved = {p["code"]: p for p in result["approved"]}
        assert approved["2330"]["budget"] == pytest.approx(5_000.0)


# ── validate_plan: sector exposure ≤30% ──────────────────────────────────────

class TestValidatePlanSectorLimit:
    def test_sector_within_30pct_is_approved(self):
        from risk_guard import validate_plan
        capital = 100_000.0
        picks = [
            _make_pick("2330", "台積電", 3_000.0, sector="半導體"),
            _make_pick("2454", "聯發科", 3_000.0, sector="半導體"),
        ]
        result = validate_plan(picks, capital, current_positions=[])
        codes = [p["code"] for p in result["approved"]]
        assert "2330" in codes
        assert "2454" in codes

    def test_sector_exceeding_30pct_triggers_rejection(self):
        from risk_guard import validate_plan
        capital = 100_000.0
        # 半導體 picks total 35% of capital
        picks = [
            _make_pick("2330", "台積電",  5_000.0, sector="半導體"),
            _make_pick("2454", "聯發科",  5_000.0, sector="半導體"),
            _make_pick("3711", "日月光",  5_000.0, sector="半導體"),
            _make_pick("2379", "瑞昱",    5_000.0, sector="半導體"),
            _make_pick("6770", "力積電",  5_000.0, sector="半導體"),
            _make_pick("2344", "華邦電",  5_000.0, sector="半導體"),
            _make_pick("5347", "世界",    5_000.0, sector="半導體"),
        ]
        result = validate_plan(picks, capital, current_positions=[])
        total_sector = sum(p["budget"] for p in result["approved"] if p["sector"] == "半導體")
        assert total_sector <= capital * 0.30 + 0.01  # small float tolerance

    def test_existing_positions_count_toward_sector_limit(self):
        from risk_guard import validate_plan
        capital = 100_000.0
        existing = [_make_position("2303", sector="半導體", value=28_000.0)]  # 28%
        picks = [_make_pick("2330", "台積電", 5_000.0, sector="半導體")]  # would push to 33%
        result = validate_plan(picks, capital, current_positions=existing)
        # total sector exposure 28+5 = 33% > 30%: pick must be reduced (to 2k) or rejected
        approved = {p["code"]: p for p in result["approved"]}
        rejected = {p["code"]: p for p in result["rejected"]}
        if "2330" in approved:
            sector_total = 28_000.0 + approved["2330"]["budget"]
            assert sector_total <= capital * 0.30 + 0.01
        else:
            assert "2330" in rejected  # fully rejected is also acceptable

    def test_different_sectors_not_affected_by_each_other(self):
        from risk_guard import validate_plan
        capital = 100_000.0
        picks = [
            _make_pick("2330", "台積電", 5_000.0, sector="半導體"),
            _make_pick("2882", "國泰金", 5_000.0, sector="金融"),
            _make_pick("1301", "台塑",   5_000.0, sector="石化"),
        ]
        result = validate_plan(picks, capital, current_positions=[])
        codes = [p["code"] for p in result["approved"]]
        assert len(codes) == 3


# ── validate_plan: existing positions → single-stock cap ─────────────────────
#
# 核心場景：昨日已有持倉，今日再買同股或同板塊
# 驗收：Guard 4（單股 5%）必須計算「已持倉 + 新增」的合計，不能只看新增。

class TestValidatePlanSingleStockWithExistingPositions:
    """Guard 4：單股上限需計入已持倉曝險。"""

    def test_existing_holding_reduces_available_room(self):
        """
        輸入：2330 已持有 4,000（4%），新 pick budget=3,000（3%）
        合計 7% > 5% → 應縮減至剩餘 1,000（1%）
        """
        from risk_guard import validate_plan
        capital  = 100_000.0
        existing = [_make_position("2330", sector="半導體", value=4_000.0)]
        picks    = [_make_pick("2330", "台積電", 3_000.0, sector="半導體")]

        result   = validate_plan(picks, capital, current_positions=existing)

        approved = {p["code"]: p for p in result["approved"]}
        assert "2330" in approved, "2330 should be approved with reduced budget"
        assert approved["2330"]["budget"] == pytest.approx(1_000.0), (
            f"Expected 1000 (room = 5000-4000), got {approved['2330']['budget']}"
        )

    def test_existing_holding_at_limit_rejects_new_buy(self):
        """
        輸入：2330 已持有 5,000（= 5% 上限）
        新 pick → 直接拒絕，不得再加碼同一股票。
        """
        from risk_guard import validate_plan
        capital  = 100_000.0
        existing = [_make_position("2330", sector="半導體", value=5_000.0)]
        picks    = [_make_pick("2330", "台積電", 2_000.0, sector="半導體")]

        result   = validate_plan(picks, capital, current_positions=existing)

        rejected_codes = [p["code"] for p in result["rejected"]]
        assert "2330" in rejected_codes, "Already at 5% limit → must be rejected"
        approved_codes = [p["code"] for p in result["approved"]]
        assert "2330" not in approved_codes

    def test_existing_holding_above_limit_rejects_new_buy(self):
        """
        輸入：舊邏輯下可能存入超限持倉（例如 6,000 = 6%）
        新 pick → 仍應拒絕，不能繼續加碼。
        """
        from risk_guard import validate_plan
        capital  = 100_000.0
        existing = [_make_position("2330", sector="半導體", value=6_000.0)]
        picks    = [_make_pick("2330", "台積電", 1_000.0, sector="半導體")]

        result   = validate_plan(picks, capital, current_positions=existing)

        rejected_codes = [p["code"] for p in result["rejected"]]
        assert "2330" in rejected_codes

    def test_different_stock_not_affected_by_existing_position(self):
        """
        2330 已持有 4%；新 pick 是 2454（不同股票）→ 2454 不受影響。
        """
        from risk_guard import validate_plan
        capital  = 100_000.0
        existing = [_make_position("2330", sector="半導體", value=4_000.0)]
        picks    = [_make_pick("2454", "聯發科", 4_000.0, sector="半導體")]

        result   = validate_plan(picks, capital, current_positions=existing)
        # 2454 stock room = 5000 (no existing), 半導體 sector room = 30000-4000=26000
        # → 4000 should pass both caps
        approved_codes = [p["code"] for p in result["approved"]]
        assert "2454" in approved_codes

    def test_two_picks_same_stock_cumulative_cap(self):
        """
        同批次 picks 中有兩筆 2330：第一筆 3,000、第二筆 3,000
        無既有持倉，上限 5,000。
        第一筆核准（3,000），第二筆僅剩 2,000 空間 → 縮至 2,000。
        """
        from risk_guard import validate_plan
        capital = 100_000.0
        picks   = [
            _make_pick("2330", "台積電", 3_000.0, sector="半導體"),
            _make_pick("2330", "台積電", 3_000.0, sector="半導體"),
        ]

        result  = validate_plan(picks, capital, current_positions=[])

        approved = result["approved"]
        total_2330 = sum(p["budget"] for p in approved if p["code"] == "2330")
        assert total_2330 <= 100_000.0 * 0.05 + 0.01, (
            f"Total 2330 budget {total_2330} exceeds 5% cap"
        )


# ── validate_plan: 昨日持倉 + 今日新買 + 同板塊加碼 ──────────────────────────
#
# 最高風險場景：跨日持倉 + 板塊集中度雙重疊加

class TestValidatePlanMultiDayExposure:
    """昨日持倉 + 今日新買：sector_exposure 必須合計所有曝險。"""

    def test_yesterday_plus_today_same_sector_capped(self):
        """
        昨日半導體持倉 28,000（28%）
        今日新買半導體 pick budget=5,000 → 合計 33% > 30%
        → 應縮減至 2,000（剩餘空間）
        """
        from risk_guard import validate_plan
        capital  = 100_000.0
        existing = [_make_position("2303", sector="半導體", value=28_000.0)]
        picks    = [_make_pick("2330", "台積電", 5_000.0, sector="半導體")]

        result   = validate_plan(picks, capital, current_positions=existing)

        approved = {p["code"]: p for p in result["approved"]}
        assert "2330" in approved, "Should be approved with reduced budget"
        sector_total = 28_000.0 + approved["2330"]["budget"]
        assert sector_total <= capital * 0.30 + 0.01, (
            f"Sector total {sector_total} > 30% cap"
        )
        assert approved["2330"]["budget"] == pytest.approx(2_000.0)

    def test_yesterday_sector_at_limit_rejects_today_same_sector(self):
        """
        昨日半導體已達 30,000（30%）→ 今日同板塊新 pick 直接拒絕。
        """
        from risk_guard import validate_plan
        capital  = 100_000.0
        existing = [_make_position("2303", sector="半導體", value=30_000.0)]
        picks    = [_make_pick("2330", "台積電", 3_000.0, sector="半導體")]

        result   = validate_plan(picks, capital, current_positions=existing)

        rejected_codes = [p["code"] for p in result["rejected"]]
        assert "2330" in rejected_codes, "Sector at 30% limit → must reject"

    def test_three_layer_accumulation(self):
        """
        三層疊加：昨日持倉 + 今日批次第一筆 + 今日批次第二筆，同板塊。

        昨日半導體：20,000（20%）
        今日第一筆：6,000 → 通過（合計 26%，未超 30%）
        今日第二筆：6,000 → 只剩 4,000 空間，縮至 4,000

        合計三層：20,000 + 6,000 + 4,000 = 30,000 = 上限，不超限。
        """
        from risk_guard import validate_plan
        capital  = 100_000.0
        existing = [_make_position("2303", sector="半導體", value=20_000.0)]
        picks    = [
            _make_pick("2330", "台積電",  6_000.0, sector="半導體"),
            _make_pick("2454", "聯發科",  6_000.0, sector="半導體"),
        ]

        result   = validate_plan(picks, capital, current_positions=existing)

        approved = {p["code"]: p for p in result["approved"]}
        total_sector = (
            20_000.0
            + approved.get("2330", {}).get("budget", 0.0)
            + approved.get("2454", {}).get("budget", 0.0)
        )
        assert total_sector <= capital * 0.30 + 0.01, (
            f"Three-layer sector total {total_sector} exceeds 30% cap"
        )

    def test_single_stock_and_sector_both_constrain(self):
        """
        2330 昨日持有 4,000（4%），板塊 28,000（28%）
        新 pick 2330 budget=5,000
        → 單股剩 1,000；板塊剩 2,000
        → 取較小值：batch reduced to 1,000
        """
        from risk_guard import validate_plan
        capital  = 100_000.0
        existing = [
            _make_position("2303", sector="半導體", value=24_000.0),  # other 半導體
            _make_position("2330", sector="半導體", value=4_000.0),   # same stock
        ]
        # total 半導體 = 28,000 (28%); 2330 = 4,000 (4%)
        picks = [_make_pick("2330", "台積電", 5_000.0, sector="半導體")]

        result  = validate_plan(picks, capital, current_positions=existing)

        approved = {p["code"]: p for p in result["approved"]}
        assert "2330" in approved
        # stock_room  = 5000 - 4000 = 1000
        # sector_room = 30000 - 28000 = 2000
        # final budget = min(1000, 2000) = 1000
        assert approved["2330"]["budget"] == pytest.approx(1_000.0)

    def test_no_existing_positions_behavior_unchanged(self):
        """
        空 current_positions → 行為與修正前完全相同（回歸保護）。
        單股 5,000 = 5%，剛好在上限，應核准不縮減。
        """
        from risk_guard import validate_plan
        capital = 100_000.0
        picks   = [_make_pick("2330", "台積電", 5_000.0, sector="半導體")]
        result  = validate_plan(picks, capital, current_positions=[])
        approved = {p["code"]: p for p in result["approved"]}
        assert "2330" in approved
        assert approved["2330"]["budget"] == pytest.approx(5_000.0)


# ── validate_plan: blacklist & ex-dividend ────────────────────────────────────

class TestValidatePlanFilters:
    def test_blacklisted_stock_is_rejected(self):
        from risk_guard import validate_plan
        picks = [_make_pick("BLACKTEST", "黑名單股", 3_000.0)]
        result = validate_plan(picks, 100_000.0, current_positions=[])
        codes = [p["code"] for p in result["rejected"]]
        assert "BLACKTEST" in codes

    def test_rejected_pick_has_reason(self):
        from risk_guard import validate_plan
        picks = [_make_pick("BLACKTEST", "黑名單股", 3_000.0)]
        result = validate_plan(picks, 100_000.0, current_positions=[])
        rejected = {p["code"]: p for p in result["rejected"]}
        assert "reason" in rejected["BLACKTEST"]

    @patch("risk_guard.check_ex_dividend", return_value=True)
    def test_ex_dividend_stock_is_rejected(self, mock_exdiv):
        from risk_guard import validate_plan
        picks = [_make_pick("2330", "台積電", 3_000.0)]
        result = validate_plan(picks, 100_000.0, current_positions=[])
        codes = [p["code"] for p in result["rejected"]]
        assert "2330" in codes

    @patch("risk_guard.check_ex_dividend", return_value=False)
    def test_non_ex_dividend_stock_passes_filter(self, mock_exdiv):
        from risk_guard import validate_plan
        picks = [_make_pick("2330", "台積電", 3_000.0)]
        result = validate_plan(picks, 100_000.0, current_positions=[])
        codes = [p["code"] for p in result["approved"]]
        assert "2330" in codes

    @patch("risk_guard.check_ex_dividend", return_value=False)
    def test_limit_up_stock_is_rejected(self, mock_exdiv):
        from risk_guard import validate_plan
        # price == prev_close * 1.10 → limit up
        picks = [_make_pick("2330", "台積電", 3_000.0)]
        picks[0]["current_price"] = 935.0
        picks[0]["prev_close"]    = 850.0
        result = validate_plan(picks, 100_000.0, current_positions=[])
        codes = [p["code"] for p in result["rejected"]]
        assert "2330" in codes


# ── validate_plan: output structure ──────────────────────────────────────────

class TestValidatePlanOutput:
    @patch("risk_guard.check_ex_dividend", return_value=False)
    def test_returns_approved_and_rejected_keys(self, mock_exdiv):
        from risk_guard import validate_plan
        result = validate_plan([], 100_000.0, current_positions=[])
        assert "approved" in result
        assert "rejected" in result

    @patch("risk_guard.check_ex_dividend", return_value=False)
    def test_empty_picks_returns_empty_lists(self, mock_exdiv):
        from risk_guard import validate_plan
        result = validate_plan([], 100_000.0, current_positions=[])
        assert result["approved"] == []
        assert result["rejected"] == []

    @patch("risk_guard.check_ex_dividend", return_value=False)
    def test_approved_picks_preserve_original_fields(self, mock_exdiv):
        from risk_guard import validate_plan
        picks = [_make_pick("2330", "台積電", 3_000.0)]
        result = validate_plan(picks, 100_000.0, current_positions=[])
        approved = result["approved"]
        assert len(approved) == 1
        assert approved[0]["code"] == "2330"
        assert approved[0]["name"] == "台積電"
        assert approved[0]["signal"] == "buy"
