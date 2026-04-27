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
        # total sector exposure 28+5 = 33% > 30%, pick should be reduced or rejected
        approved = {p["code"]: p for p in result["approved"]}
        if "2330" in approved:
            sector_total = 28_000.0 + approved["2330"]["budget"]
            assert sector_total <= capital * 0.30 + 0.01

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
