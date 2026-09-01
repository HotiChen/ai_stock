"""tests/test_futures_premium.py — TDD tests for futures_premium.py (PR-4)"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


# ── FuturesPremium dataclass ──────────────────────────────────────────────────

class TestFuturesPremiumDataclass:
    def test_fields_accessible(self):
        from futures_premium import FuturesPremium
        fp = FuturesPremium(
            spot_index=20000.0, futures_price=20050.0,
            premium=50.0, premium_pct=0.25, label="溢價 50 點（+0.25%）",
        )
        assert fp.spot_index == 20000.0
        assert fp.premium == 50.0
        assert fp.label == "溢價 50 點（+0.25%）"


# ── calc_premium ──────────────────────────────────────────────────────────────

class TestCalcPremium:
    def test_positive_premium(self):
        from futures_premium import calc_premium
        result = calc_premium(spot=20000.0, futures=20100.0)
        assert result["premium"] == pytest.approx(100.0)
        assert result["premium_pct"] > 0
        assert "溢價" in result["label"]

    def test_negative_premium_discount(self):
        from futures_premium import calc_premium
        result = calc_premium(spot=20000.0, futures=19900.0)
        assert result["premium"] == pytest.approx(-100.0)
        assert result["premium_pct"] < 0
        assert "貼水" in result["label"]

    def test_near_zero_is_flat(self):
        from futures_premium import calc_premium
        result = calc_premium(spot=20000.0, futures=20002.0)
        assert result["label"] == "平水"

    def test_zero_spot_returns_zero_pct(self):
        from futures_premium import calc_premium
        result = calc_premium(spot=0.0, futures=100.0)
        assert result["premium_pct"] == 0.0

    def test_returns_dict_with_required_keys(self):
        from futures_premium import calc_premium
        result = calc_premium(20000.0, 20050.0)
        assert "premium" in result
        assert "premium_pct" in result
        assert "label" in result


# ── fetch_spot_index ──────────────────────────────────────────────────────────

class TestFetchSpotIndex:
    """yfinance ^TWII 備援已移除，只走 Shioaji 指數合約。"""

    def test_returns_none_when_no_connection(self):
        from futures_premium import fetch_spot_index
        with patch("shioaji_session.get_api", return_value=None):
            assert fetch_spot_index() is None

    def test_returns_none_when_snapshot_raises(self):
        from futures_premium import fetch_spot_index
        api = MagicMock()
        api.Contracts.Indexs.TSE = {"001": object()}
        api.snapshots.side_effect = Exception("quote session down")
        assert fetch_spot_index(api=api) is None

    def _mock_yf(self, close=20000.0):
        import pandas as pd, numpy as np
        mock_yf = MagicMock()
        df = pd.DataFrame({"Close": [close]}, index=pd.date_range("2026-05-17", periods=1))
        mock_yf.Ticker.return_value.history.return_value = df
        return mock_yf


    def test_yfinance_empty_returns_none(self):
        from futures_premium import fetch_spot_index
        import sys, pandas as pd
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = pd.DataFrame()
        with patch.dict(sys.modules, {"yfinance": mock_yf}):
            result = fetch_spot_index()
        assert result is None

    def test_yfinance_exception_returns_none(self):
        from futures_premium import fetch_spot_index
        import sys
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.side_effect = Exception("timeout")
        with patch.dict(sys.modules, {"yfinance": mock_yf}):
            result = fetch_spot_index()
        assert result is None

    def test_shioaji_api_takes_priority(self):
        from futures_premium import fetch_spot_index
        api = MagicMock()
        snap = MagicMock()
        snap.close = 21000.0
        api.snapshots.return_value = [snap]
        result = fetch_spot_index(api=api)
        assert result == pytest.approx(21000.0)


class TestFetchFuturesPrice:
    def test_shioaji_success_returns_float(self):
        from futures_premium import fetch_futures_price
        api = MagicMock()
        snap = MagicMock()
        snap.close = 20080.0
        api.snapshots.return_value = [snap]
        result = fetch_futures_price(api=api)
        assert isinstance(result, float)
        assert result == pytest.approx(20080.0)

    def test_shioaji_failure_tries_twse_api(self):
        from futures_premium import fetch_futures_price
        import sys
        api = MagicMock()
        api.snapshots.side_effect = Exception("api error")
        mock_req = MagicMock()
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"msgArray": [{"z": "20060"}]}
        mock_req.get.return_value = mock_resp
        with patch.dict(sys.modules, {"requests": mock_req}):
            result = fetch_futures_price(api=api)
        assert result == pytest.approx(20060.0)

    def test_api_none_tries_twse_directly(self):
        from futures_premium import fetch_futures_price
        import sys
        mock_req = MagicMock()
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"msgArray": [{"z": "19980"}]}
        mock_req.get.return_value = mock_resp
        with patch.dict(sys.modules, {"requests": mock_req}):
            result = fetch_futures_price()
        assert result == pytest.approx(19980.0)

    def test_all_sources_fail_returns_none(self):
        from futures_premium import fetch_futures_price
        import sys
        mock_req = MagicMock()
        mock_req.get.side_effect = Exception("network error")
        with patch.dict(sys.modules, {"requests": mock_req}):
            result = fetch_futures_price()
        assert result is None

    def test_twse_empty_msgarray_returns_none(self):
        from futures_premium import fetch_futures_price
        import sys
        mock_req = MagicMock()
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"msgArray": []}
        mock_req.get.return_value = mock_resp
        with patch.dict(sys.modules, {"requests": mock_req}):
            result = fetch_futures_price()
        assert result is None


# ── fetch_futures_premium ─────────────────────────────────────────────────────

class TestFetchFuturesPremium:
    def test_success_returns_dataclass(self):
        from futures_premium import fetch_futures_premium, FuturesPremium
        with patch("futures_premium.fetch_spot_index", return_value=20000.0), \
             patch("futures_premium.fetch_futures_price", return_value=20060.0):
            result = fetch_futures_premium()
        assert isinstance(result, FuturesPremium)
        assert result.premium == pytest.approx(60.0)

    def test_spot_none_returns_none(self):
        from futures_premium import fetch_futures_premium
        with patch("futures_premium.fetch_spot_index", return_value=None), \
             patch("futures_premium.fetch_futures_price", return_value=20000.0):
            assert fetch_futures_premium() is None

    def test_futures_none_returns_none(self):
        from futures_premium import fetch_futures_premium
        with patch("futures_premium.fetch_spot_index", return_value=20000.0), \
             patch("futures_premium.fetch_futures_price", return_value=None):
            assert fetch_futures_premium() is None

    def test_premium_pct_correct(self):
        from futures_premium import fetch_futures_premium
        with patch("futures_premium.fetch_spot_index", return_value=20000.0), \
             patch("futures_premium.fetch_futures_price", return_value=20100.0):
            result = fetch_futures_premium()
        # premium = 100, premium_pct = 100/20000*100 = 0.5%
        assert result.premium_pct == pytest.approx(0.5, abs=0.01)

    def test_discount_label(self):
        from futures_premium import fetch_futures_premium
        with patch("futures_premium.fetch_spot_index", return_value=20000.0), \
             patch("futures_premium.fetch_futures_price", return_value=19900.0):
            result = fetch_futures_premium()
        assert "貼水" in result.label

    def test_premium_label(self):
        from futures_premium import fetch_futures_premium
        with patch("futures_premium.fetch_spot_index", return_value=20000.0), \
             patch("futures_premium.fetch_futures_price", return_value=20100.0):
            result = fetch_futures_premium()
        assert "溢價" in result.label


# ── _assess_day_trading with futures_premium_pct ─────────────────────────────

class TestAssessDayTradingFuturesPremium:
    def _base_ind(self):
        return {
            "current_price": 100.0, "volume_ratio": 1.5, "RSI": 55.0,
            "ATR": 3.0, "bullish_alignment": False, "bearish_alignment": False,
            "KD_K": 55.0, "KD_D": 50.0,
        }

    def _assess(self, futures_pct=0.0, index_pct=0.0):
        from stock_query import _assess_day_trading
        market = {"index_change_pct": index_pct, "futures_premium_pct": futures_pct}
        return _assess_day_trading(self._base_ind(), {"error": None, "monthly_closes": []}, market=market)

    def test_premium_above_threshold_adds_score(self):
        base = self._assess(futures_pct=0.0)["score"]
        result = self._assess(futures_pct=0.5)["score"]
        assert result >= base + 1

    def test_premium_adds_good_reason(self):
        result = self._assess(futures_pct=0.5)
        assert any("期" in r or "溢價" in r for r in result["reasons_good"])

    def test_discount_below_threshold_reduces_score(self):
        base = self._assess(futures_pct=0.0)["score"]
        result = self._assess(futures_pct=-0.5)["score"]
        assert result <= base - 1

    def test_discount_adds_bad_reason(self):
        result = self._assess(futures_pct=-0.5)
        assert any("期" in r or "貼水" in r for r in result["reasons_bad"])

    def test_small_premium_within_flat_zone_no_change(self):
        base = self._assess(futures_pct=0.0)["score"]
        small = self._assess(futures_pct=0.1)["score"]
        assert small == base

    def test_no_futures_key_no_change(self):
        from stock_query import _assess_day_trading
        market_no_futures = {"index_change_pct": 0.0}
        market_with_zero  = {"index_change_pct": 0.0, "futures_premium_pct": 0.0}
        base = _assess_day_trading(self._base_ind(), {"error": None, "monthly_closes": []},
                                   market=market_no_futures)["score"]
        same = _assess_day_trading(self._base_ind(), {"error": None, "monthly_closes": []},
                                   market=market_with_zero)["score"]
        assert base == same


# ── _fetch_market in daytrading_report includes futures premium ───────────────

class TestFetchMarketIncludesFutures:
    def test_futures_premium_pct_key_present(self):
        from daytrading_report import _fetch_market
        with patch("market_index.fetch_market_index_change", return_value=0.3), \
             patch("futures_premium.fetch_futures_premium",
                   return_value=MagicMock(premium_pct=0.25)):
            result = _fetch_market()
        assert "futures_premium_pct" in result

    def test_futures_premium_value_used(self):
        from daytrading_report import _fetch_market
        with patch("market_index.fetch_market_index_change", return_value=0.3), \
             patch("futures_premium.fetch_futures_premium",
                   return_value=MagicMock(premium_pct=0.25)):
            result = _fetch_market()
        assert result["futures_premium_pct"] == pytest.approx(0.25)

    def test_futures_failure_defaults_to_zero(self):
        from daytrading_report import _fetch_market
        with patch("market_index.fetch_market_index_change", return_value=0.3), \
             patch("futures_premium.fetch_futures_premium", return_value=None):
            result = _fetch_market()
        assert result["futures_premium_pct"] == 0.0
