from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from market_scanner import (
    ScanCriteria,
    ScanResult,
    screen_candidates,
    score_snapshot,
    get_all_stock_codes,
    run_deep_analysis,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_snap(code="2330", name="台積電", close=850.0, change_rate=3.5, volume=50000):
    return {
        "code": code, "name": name,
        "close": close, "change_rate": change_rate,
        "change_price": close * change_rate / 100,
        "total_volume": volume,
    }


SNAPSHOTS = {
    "2330": make_snap("2330", "台積電",   close=850,  change_rate=3.5,  volume=50000),
    "2454": make_snap("2454", "聯發科",   close=1200, change_rate=5.2,  volume=20000),
    "2317": make_snap("2317", "鴻海",     close=180,  change_rate=-2.1, volume=80000),
    "0050": make_snap("0050", "元大50",   close=145,  change_rate=1.1,  volume=90000),
    "9999": make_snap("9999", "低量股",   close=10,   change_rate=0.1,  volume=100),
    "8888": make_snap("8888", "超貴股",   close=9999, change_rate=1.0,  volume=5000),
    "7777": make_snap("7777", "低價股",   close=3,    change_rate=1.0,  volume=5000),
}


# ── ScanCriteria ──────────────────────────────────────────────────────────────

class TestScanCriteria:
    def test_has_default_values(self):
        c = ScanCriteria()
        assert c.min_volume > 0
        assert c.min_price > 0
        assert c.max_price > c.min_price
        assert c.top_n > 0

    def test_can_be_customized(self):
        c = ScanCriteria(min_volume=5000, min_price=50.0, max_price=2000.0, top_n=10)
        assert c.min_volume == 5000
        assert c.top_n == 10

    def test_default_top_n_reasonable(self):
        c = ScanCriteria()
        assert 5 <= c.top_n <= 50


# ── ScanResult ────────────────────────────────────────────────────────────────

class TestScanResult:
    def test_has_required_fields(self):
        r = ScanResult(
            code="2330", name="台積電", close=850.0,
            change_rate=3.5, total_volume=50000, analysis=None,
        )
        assert r.code == "2330"
        assert r.analysis is None

    def test_analysis_can_be_dict(self):
        r = ScanResult(
            code="2330", name="台積電", close=850.0,
            change_rate=3.5, total_volume=50000,
            analysis={"action": "buy", "confidence": 7},
        )
        assert r.analysis["action"] == "buy"


# ── score_snapshot ────────────────────────────────────────────────────────────

class TestScoreSnapshot:
    def test_higher_volume_higher_score(self):
        low_vol  = make_snap(volume=1000,  change_rate=3.0)
        high_vol = make_snap(volume=50000, change_rate=3.0)
        assert score_snapshot(high_vol) > score_snapshot(low_vol)

    def test_higher_abs_change_higher_score(self):
        low_chg  = make_snap(volume=10000, change_rate=0.5)
        high_chg = make_snap(volume=10000, change_rate=5.0)
        assert score_snapshot(high_chg) > score_snapshot(low_chg)

    def test_negative_change_also_scores(self):
        pos = make_snap(volume=10000, change_rate=3.0)
        neg = make_snap(volume=10000, change_rate=-3.0)
        assert score_snapshot(neg) == pytest.approx(score_snapshot(pos), rel=0.01)

    def test_zero_change_scores_lower(self):
        no_move = make_snap(volume=10000, change_rate=0.0)
        moving  = make_snap(volume=10000, change_rate=2.0)
        assert score_snapshot(moving) > score_snapshot(no_move)

    def test_returns_non_negative(self):
        assert score_snapshot(make_snap(volume=0, change_rate=0.0)) >= 0


# ── screen_candidates ─────────────────────────────────────────────────────────

class TestScreenCandidates:
    def test_returns_list(self):
        result = screen_candidates(SNAPSHOTS, ScanCriteria())
        assert isinstance(result, list)

    def test_filters_low_volume(self):
        criteria = ScanCriteria(min_volume=10000, min_price=5.0, max_price=9000.0, top_n=20)
        result = screen_candidates(SNAPSHOTS, criteria)
        codes = [r["code"] for r in result]
        assert "9999" not in codes  # volume=100, 被過濾

    def test_filters_low_price(self):
        criteria = ScanCriteria(min_volume=50, min_price=10.0, max_price=9000.0, top_n=20)
        result = screen_candidates(SNAPSHOTS, criteria)
        codes = [r["code"] for r in result]
        assert "7777" not in codes  # price=3, 被過濾

    def test_filters_high_price(self):
        criteria = ScanCriteria(min_volume=1000, min_price=5.0, max_price=2000.0, top_n=20)
        result = screen_candidates(SNAPSHOTS, criteria)
        codes = [r["code"] for r in result]
        assert "8888" not in codes  # price=9999, 被過濾

    def test_respects_top_n(self):
        criteria = ScanCriteria(min_volume=0, min_price=0, max_price=99999, top_n=3)
        result = screen_candidates(SNAPSHOTS, criteria)
        assert len(result) <= 3

    def test_sorted_by_score_desc(self):
        criteria = ScanCriteria(min_volume=0, min_price=0, max_price=99999, top_n=20)
        result = screen_candidates(SNAPSHOTS, criteria)
        scores = [score_snapshot(r) for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_empty_snapshots_returns_empty(self):
        assert screen_candidates({}, ScanCriteria()) == []

    def test_all_filtered_returns_empty(self):
        criteria = ScanCriteria(min_volume=999999, min_price=0, max_price=99999, top_n=10)
        result = screen_candidates(SNAPSHOTS, criteria)
        assert result == []


# ── get_all_stock_codes ───────────────────────────────────────────────────────

class TestGetAllStockCodes:
    def test_returns_list_of_strings(self):
        mock_api = MagicMock()
        tse_contracts = [MagicMock(code="2330"), MagicMock(code="2317")]
        otc_contracts = [MagicMock(code="6547")]
        mock_api.Contracts.Stocks.TSE = tse_contracts
        mock_api.Contracts.Stocks.OTC = otc_contracts
        mock_api.Contracts.Stocks.OES = []

        result = get_all_stock_codes(mock_api)
        assert isinstance(result, list)
        assert all(isinstance(c, str) for c in result)

    def test_includes_tse_and_otc(self):
        mock_api = MagicMock()
        mock_api.Contracts.Stocks.TSE = [MagicMock(code="2330")]
        mock_api.Contracts.Stocks.OTC = [MagicMock(code="6547")]
        mock_api.Contracts.Stocks.OES = []

        result = get_all_stock_codes(mock_api)
        assert "2330" in result
        assert "6547" in result

    def test_deduplicates_codes(self):
        mock_api = MagicMock()
        mock_api.Contracts.Stocks.TSE = [MagicMock(code="2330"), MagicMock(code="2330")]
        mock_api.Contracts.Stocks.OTC = []
        mock_api.Contracts.Stocks.OES = []

        result = get_all_stock_codes(mock_api)
        assert result.count("2330") == 1

    def test_handles_exchange_error_gracefully(self):
        mock_api = MagicMock()
        mock_api.Contracts.Stocks.TSE = [MagicMock(code="2330")]
        type(mock_api.Contracts.Stocks).OTC = property(lambda self: (_ for _ in ()).throw(Exception("no OTC")))
        mock_api.Contracts.Stocks.OES = []

        result = get_all_stock_codes(mock_api)
        assert isinstance(result, list)

    def test_returns_empty_on_full_failure(self):
        mock_api = MagicMock()
        mock_api.Contracts.Stocks.TSE = MagicMock(side_effect=Exception("fail"))
        mock_api.Contracts.Stocks.OTC = MagicMock(side_effect=Exception("fail"))
        mock_api.Contracts.Stocks.OES = MagicMock(side_effect=Exception("fail"))

        result = get_all_stock_codes(mock_api)
        assert isinstance(result, list)


# ── run_deep_analysis ─────────────────────────────────────────────────────────

class TestRunDeepAnalysis:
    @patch("market_scanner.analyze_stock_ai")
    @patch("market_scanner.fetch_stock_fundamentals")
    @patch("market_scanner.fetch_stock_news")
    def test_returns_list_of_scan_results(self, mock_news, mock_fund, mock_ai):
        mock_news.return_value = ["台積電漲停"]
        mock_fund.return_value = MagicMock()
        mock_ai.return_value = {"action": "buy", "confidence": 7, "reason": "好", "target_price": 900, "stop_loss": 800}

        candidates = [make_snap("2330", "台積電"), make_snap("2454", "聯發科")]
        results = run_deep_analysis(candidates, indicators_map={})
        assert isinstance(results, list)
        assert all(isinstance(r, ScanResult) for r in results)

    @patch("market_scanner.analyze_stock_ai")
    @patch("market_scanner.fetch_stock_fundamentals")
    @patch("market_scanner.fetch_stock_news")
    def test_result_contains_analysis(self, mock_news, mock_fund, mock_ai):
        mock_news.return_value = []
        mock_fund.return_value = MagicMock()
        mock_ai.return_value = {"action": "buy", "confidence": 8, "reason": "RSI低", "target_price": None, "stop_loss": None}

        candidates = [make_snap("2330", "台積電")]
        results = run_deep_analysis(candidates, indicators_map={})
        assert results[0].analysis["action"] == "buy"

    @patch("market_scanner.analyze_stock_ai", side_effect=Exception("AI 失敗"))
    @patch("market_scanner.fetch_stock_fundamentals")
    @patch("market_scanner.fetch_stock_news")
    def test_ai_failure_sets_none_analysis(self, mock_news, mock_fund, mock_ai):
        mock_news.return_value = []
        mock_fund.return_value = MagicMock()

        candidates = [make_snap("2330", "台積電")]
        results = run_deep_analysis(candidates, indicators_map={})
        assert results[0].analysis is None

    @patch("market_scanner.analyze_stock_ai")
    @patch("market_scanner.fetch_stock_fundamentals")
    @patch("market_scanner.fetch_stock_news")
    def test_passes_indicators_when_available(self, mock_news, mock_fund, mock_ai):
        mock_news.return_value = []
        mock_fund.return_value = MagicMock()
        mock_ai.return_value = {"action": "hold", "confidence": 5, "reason": "觀望", "target_price": None, "stop_loss": None}

        indicators_map = {"2330": {"RSI": 45.0, "MA5": 850.0}}
        candidates = [make_snap("2330", "台積電")]
        run_deep_analysis(candidates, indicators_map=indicators_map)

        _, kwargs = mock_ai.call_args
        passed_indicators = mock_ai.call_args[0][4] if mock_ai.call_args[0] else kwargs.get("indicators", {})
        assert isinstance(passed_indicators, dict)
