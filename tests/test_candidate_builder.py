"""Tests for build_market_candidates() in market_scanner.py"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from market_scanner import ScanCriteria, build_market_candidates


# ── Fixtures ──────────────────────────────────────────────────────────────────

NAME_MAP = {"2330": "台積電", "2317": "鴻海"}

FAKE_SNAPSHOTS = {
    "2330": {
        "close": 850.0,
        "change_rate": 1.23,
        "total_volume": 50000,
    },
    "2317": {
        "close": 110.0,
        "change_rate": -0.5,
        "total_volume": 30000,
    },
}

SCREEN_RESULT = [
    {"code": "2330", "close": 850.0, "change_rate": 1.23, "total_volume": 50000},
    {"code": "2317", "close": 110.0, "change_rate": -0.5, "total_volume": 30000},
]


# ── Test 1: api=None, no manual codes ─────────────────────────────────────────

def test_api_none_no_manual():
    result = build_market_candidates(api=None, name_map=NAME_MAP)
    assert result == []


# ── Test 2: api=None, with manual codes ───────────────────────────────────────

def test_api_none_with_manual():
    result = build_market_candidates(
        api=None,
        name_map=NAME_MAP,
        manual_codes=["2330"],
    )
    assert len(result) == 1
    row = result[0]
    assert row["code"] == "2330"
    assert row["source"] == "manual"
    assert row["close"] == 0.0
    assert row["change_rate"] == 0.0
    assert row["total_volume"] == 0
    assert row["name"] == "台積電"  # from name_map


def test_api_none_with_manual_unknown_code():
    """Code not in name_map → use code as name."""
    result = build_market_candidates(
        api=None,
        name_map={},
        manual_codes=["9999"],
    )
    assert len(result) == 1
    assert result[0]["name"] == "9999"


# ── Test 3: api mock, scan path ───────────────────────────────────────────────

def test_api_mock_scan():
    api = MagicMock()
    criteria = ScanCriteria(top_n=5)

    with patch("market_scanner.get_all_stock_codes", return_value=["2330", "2317"]) as mock_codes, \
         patch("market_scanner.batch_fetch_snapshots", return_value=FAKE_SNAPSHOTS) as mock_snaps, \
         patch("market_scanner.screen_candidates", return_value=SCREEN_RESULT) as mock_screen:

        result = build_market_candidates(api=api, name_map=NAME_MAP, criteria=criteria)

    mock_codes.assert_called_once_with(api)
    mock_snaps.assert_called_once_with(api, ["2330", "2317"])
    mock_screen.assert_called_once()

    assert len(result) == 2
    sources = {r["source"] for r in result}
    assert sources == {"scan"}

    # names filled from name_map
    codes_in_result = {r["code"] for r in result}
    assert "2330" in codes_in_result
    assert "2317" in codes_in_result


# ── Test 4: dedup — scan wins over manual ─────────────────────────────────────

def test_dedup_scan_wins():
    api = MagicMock()

    with patch("market_scanner.get_all_stock_codes", return_value=["2330", "2317"]), \
         patch("market_scanner.batch_fetch_snapshots", return_value=FAKE_SNAPSHOTS), \
         patch("market_scanner.screen_candidates", return_value=SCREEN_RESULT):

        result = build_market_candidates(
            api=api,
            name_map=NAME_MAP,
            manual_codes=["2330"],  # also in scan result
        )

    # 2330 should appear only once
    codes = [r["code"] for r in result]
    assert codes.count("2330") == 1

    # and its source should be "scan", not "manual"
    row_2330 = next(r for r in result if r["code"] == "2330")
    assert row_2330["source"] == "scan"


# ── Test 5: manual code, api snapshot fallback to 0.0 ────────────────────────

def test_manual_no_api_snapshot_fallback():
    """api.Contracts.Stocks.get returns None → close=0.0."""
    api = MagicMock()

    with patch("market_scanner.get_all_stock_codes", return_value=[]), \
         patch("market_scanner.batch_fetch_snapshots", return_value={}), \
         patch("market_scanner.screen_candidates", return_value=[]):

        # api.Contracts.Stocks.get returns None for the manual code
        api.Contracts.Stocks.get.return_value = None

        result = build_market_candidates(
            api=api,
            name_map=NAME_MAP,
            manual_codes=["2330"],
        )

    assert len(result) == 1
    assert result[0]["close"] == 0.0
    assert result[0]["source"] == "manual"


# ── Test 6: all required fields present ───────────────────────────────────────

REQUIRED_FIELDS = {"code", "name", "close", "change_rate", "total_volume", "source", "analysis"}


def test_all_fields_present_scan():
    api = MagicMock()

    with patch("market_scanner.get_all_stock_codes", return_value=["2330"]), \
         patch("market_scanner.batch_fetch_snapshots", return_value=FAKE_SNAPSHOTS), \
         patch("market_scanner.screen_candidates", return_value=[SCREEN_RESULT[0]]):

        result = build_market_candidates(api=api, name_map=NAME_MAP)

    assert len(result) >= 1
    for row in result:
        assert REQUIRED_FIELDS.issubset(row.keys()), f"Missing fields in: {row}"


def test_all_fields_present_manual():
    result = build_market_candidates(
        api=None,
        name_map=NAME_MAP,
        manual_codes=["2330", "2317"],
    )
    for row in result:
        assert REQUIRED_FIELDS.issubset(row.keys()), f"Missing fields in: {row}"
