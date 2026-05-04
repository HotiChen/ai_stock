"""Tests for chip_data module - TWSE institutional investors data."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chip_data import (
    fetch_institutional_investors,
    fetch_margin_trading,
    fetch_monthly_revenue,
    filter_institutional_strong,
    filter_margin_risk,
    filter_revenue_growth,
    get_continuous_buy_days,
)

# ---------------------------------------------------------------------------
# Sample TWSE API response fixture
# ---------------------------------------------------------------------------

SAMPLE_TWSE_RESPONSE = {
    "stat": "OK",
    "data": [
        # [0]=code, [1]=name, [4]=外資買進, [5]=外資賣出, [6]=外資差,
        # [10]=投信買進, [11]=投信賣出, [12]=投信差,
        # [13]=自營商買賣超, [17]=三大法人合計
        [
            "2330", "台積電",
            "col2", "col3",
            "10,000", "5,000", "5,000",   # 外資 idx 4,5,6
            "col7", "col8", "col9",
            "500", "300", "200",           # 投信 idx 10,11,12
            "-100",                        # 自營商 idx 13
            "col14", "col15", "col16",
            "5,100",                       # 三大合計 idx 17
        ],
        [
            "2454", "聯發科",
            "col2", "col3",
            "2,000", "3,000", "-1,000",   # 外資差 -1000
            "col7", "col8", "col9",
            "100", "200", "-100",          # 投信差 -100
            "50",                          # 自營商
            "col14", "col15", "col16",
            "-1,050",                      # 三大合計
        ],
    ],
}


# ---------------------------------------------------------------------------
# fetch_institutional_investors
# ---------------------------------------------------------------------------


class TestFetchInstitutionalInvestors:
    def test_normal_parse(self):
        """正常資料解析，數字正確去逗號轉 float"""
        with patch("chip_data.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = SAMPLE_TWSE_RESPONSE
            mock_get.return_value = mock_resp

            result = fetch_institutional_investors("20260505")

        assert "2330" in result
        assert result["2330"]["foreign_net"] == 5000.0
        assert result["2330"]["investment_trust_net"] == 200.0
        assert result["2330"]["dealer_net"] == -100.0
        assert result["2330"]["total_net"] == 5100.0

        assert "2454" in result
        assert result["2454"]["foreign_net"] == -1000.0
        assert result["2454"]["investment_trust_net"] == -100.0
        assert result["2454"]["dealer_net"] == 50.0
        assert result["2454"]["total_net"] == -1050.0

    def test_date_format_with_dashes(self):
        """接受 YYYY-MM-DD 格式，自動轉成 YYYYMMDD 送 API"""
        with patch("chip_data.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = SAMPLE_TWSE_RESPONSE
            mock_get.return_value = mock_resp

            fetch_institutional_investors("2026-05-05")

            called_url = mock_get.call_args[0][0]
            assert "20260505" in called_url
            assert "-" not in called_url.split("date=")[1].split("&")[0]

    def test_stat_not_ok_returns_empty(self):
        """API stat != OK 時回傳空 dict"""
        with patch("chip_data.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"stat": "FAILED", "data": []}
            mock_get.return_value = mock_resp

            result = fetch_institutional_investors("20260505")

        assert result == {}

    def test_network_error_returns_empty(self):
        """網路錯誤時回傳空 dict，不 raise"""
        with patch("chip_data.requests.get") as mock_get:
            mock_get.side_effect = Exception("Network error")

            result = fetch_institutional_investors("20260505")

        assert result == {}


# ---------------------------------------------------------------------------
# get_continuous_buy_days
# ---------------------------------------------------------------------------


class TestGetContinuousBuyDays:
    def _make_fetcher(self, date_to_data: dict):
        """建立一個根據日期回傳資料的 mock fetcher"""
        def fetcher(date_str: str) -> dict:
            return date_to_data.get(date_str, {})
        return fetcher

    def test_foreign_continuous_3_days(self):
        """外資連續 3 天買超"""
        data = {
            "20260505": {"2330": {"foreign_net": 500.0, "investment_trust_net": -10.0}},
            "20260504": {"2330": {"foreign_net": 300.0, "investment_trust_net": -5.0}},
            "20260503": {"2330": {"foreign_net": 100.0, "investment_trust_net": 0.0}},
            "20260502": {"2330": {"foreign_net": -50.0, "investment_trust_net": 0.0}},
            "20260501": {"2330": {"foreign_net": 200.0, "investment_trust_net": 0.0}},
        }
        fetcher = self._make_fetcher(data)
        result = get_continuous_buy_days("2330", "20260505", data_fetcher=fetcher, days=5)

        assert result["foreign_continuous_buy"] == 3
        assert result["investment_trust_continuous_buy"] == 0

    def test_trust_buy_broken_by_zero(self):
        """投信中間有 <= 0 就重置；外資連續 2 天"""
        data = {
            "20260505": {"2330": {"foreign_net": 100.0, "investment_trust_net": 50.0}},
            "20260504": {"2330": {"foreign_net": 200.0, "investment_trust_net": 0.0}},
            "20260503": {"2330": {"foreign_net": -50.0, "investment_trust_net": 80.0}},
        }
        fetcher = self._make_fetcher(data)
        result = get_continuous_buy_days("2330", "20260505", data_fetcher=fetcher, days=3)

        assert result["foreign_continuous_buy"] == 2  # 05/05, 05/04 are > 0, 05/03 is not
        # 等等：05/04 investment_trust = 0 (<=0), 所以連續只有 1 天
        assert result["investment_trust_continuous_buy"] == 1

    def test_stock_not_found_returns_zero(self):
        """找不到股票代號時天數回傳 0"""
        data = {
            "20260505": {"2330": {"foreign_net": 100.0, "investment_trust_net": 50.0}},
        }
        fetcher = self._make_fetcher(data)
        result = get_continuous_buy_days("9999", "20260505", data_fetcher=fetcher, days=1)

        assert result["foreign_continuous_buy"] == 0
        assert result["investment_trust_continuous_buy"] == 0

    def test_all_days_buy(self):
        """所有天都買超，天數 = days"""
        data = {
            f"2026050{i}": {"2330": {"foreign_net": float(i * 100), "investment_trust_net": float(i * 10)}}
            for i in range(1, 6)
        }
        fetcher = self._make_fetcher(data)
        result = get_continuous_buy_days("2330", "20260505", data_fetcher=fetcher, days=5)

        assert result["foreign_continuous_buy"] == 5
        assert result["investment_trust_continuous_buy"] == 5


# ---------------------------------------------------------------------------
# filter_institutional_strong
# ---------------------------------------------------------------------------


class TestFilterInstitutionalStrong:
    def _sample_data(self):
        return {
            "2330": {"foreign_net": 5000.0, "investment_trust_net": 200.0, "dealer_net": -100.0, "total_net": 5100.0},
            "2454": {"foreign_net": -500.0, "investment_trust_net": 300.0, "dealer_net": 50.0, "total_net": -150.0},
            "1234": {"foreign_net": -200.0, "investment_trust_net": -50.0, "dealer_net": 0.0, "total_net": -250.0},
            "5678": {"foreign_net": 1000.0, "investment_trust_net": -10.0, "dealer_net": 0.0, "total_net": 990.0},
        }

    def test_filter_by_foreign_only(self):
        """外資超門檻或投信 >= 0 皆入選（OR 邏輯，min_trust_net=0）"""
        data = self._sample_data()
        # min_foreign_net=500, min_trust_net=0
        # 2330: foreign=5000 >= 500 → YES
        # 2454: foreign=-500 < 500, trust=300 >= 0 → YES (OR 成立)
        # 1234: foreign=-200 < 500, trust=-50 < 0 → NO
        # 5678: foreign=1000 >= 500 → YES
        result = filter_institutional_strong(data, min_foreign_net=500.0, min_trust_net=0.0)
        assert "2330" in result
        assert "5678" in result
        assert "2454" in result  # trust=300 >= min_trust_net=0，OR 成立
        assert "1234" not in result  # 兩者皆不達標

    def test_filter_or_logic(self):
        """OR 邏輯：外資 OR 投信超門檻皆入選"""
        data = self._sample_data()
        result = filter_institutional_strong(data, min_foreign_net=500.0, min_trust_net=250.0)
        # 2330: foreign=5000 >= 500 → YES
        # 2454: foreign=-500 < 500, trust=300 >= 250 → YES
        # 1234: foreign=-200 < 500, trust=-50 < 250 → NO
        # 5678: foreign=1000 >= 500 → YES
        assert set(result) == {"2330", "2454", "5678"}

    def test_filter_none_qualify(self):
        """門檻設很高，都不符合"""
        data = self._sample_data()
        result = filter_institutional_strong(data, min_foreign_net=99999.0, min_trust_net=99999.0)
        assert result == []

    def test_filter_empty_data(self):
        """空資料回傳 []"""
        result = filter_institutional_strong({})
        assert result == []


# ---------------------------------------------------------------------------
# fetch_margin_trading
# ---------------------------------------------------------------------------

SAMPLE_MARGIN_RESPONSE = {
    "stat": "OK",
    "data": [
        # [0]=代號, [1]=名稱
        # [2]=融資買進, [3]=融資賣出, [4]=融資現金償還, [5]=融資前日餘額, [6]=融資今日餘額, [7]=融資限額
        # [8]=融券賣出, [9]=融券買進, [10]=融券現金償還, [11]=融券前日餘額, [12]=融券今日餘額, [13]=融券限額
        [
            "2330", "台積電",
            "500", "200", "0", "11,500", "12,000", "100,000",
            "100", "50", "0", "3,200", "3,000", "50,000",
        ],
        [
            "2454", "聯發科",
            "100", "300", "0", "5,000", "4,800", "50,000",
            "200", "100", "0", "1,000", "1,100", "30,000",
        ],
    ],
}


class TestFetchMarginTrading:
    def test_normal_parse(self):
        """正常解析融資融券資料，含 margin_change 與 short_change 計算"""
        with patch("chip_data.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = SAMPLE_MARGIN_RESPONSE
            mock_get.return_value = mock_resp

            result = fetch_margin_trading("20260505")

        assert "2330" in result
        assert result["2330"]["margin_balance"] == 12000.0
        assert result["2330"]["margin_prev"] == 11500.0
        assert result["2330"]["margin_change"] == 500.0      # 12000 - 11500
        assert result["2330"]["short_balance"] == 3000.0
        assert result["2330"]["short_prev"] == 3200.0
        assert result["2330"]["short_change"] == -200.0      # 3000 - 3200

        assert "2454" in result
        assert result["2454"]["margin_balance"] == 4800.0
        assert result["2454"]["margin_prev"] == 5000.0
        assert result["2454"]["margin_change"] == -200.0     # 4800 - 5000
        assert result["2454"]["short_balance"] == 1100.0
        assert result["2454"]["short_prev"] == 1000.0
        assert result["2454"]["short_change"] == 100.0       # 1100 - 1000

    def test_date_format_with_dashes(self):
        """接受 YYYY-MM-DD 格式，自動轉成 YYYYMMDD 送 API"""
        with patch("chip_data.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = SAMPLE_MARGIN_RESPONSE
            mock_get.return_value = mock_resp

            fetch_margin_trading("2026-05-05")

            called_url = mock_get.call_args[0][0]
            assert "20260505" in called_url
            assert "-" not in called_url.split("date=")[1].split("&")[0]

    def test_stat_not_ok_returns_empty(self):
        """API stat != OK 時回傳空 dict"""
        with patch("chip_data.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"stat": "FAILED", "data": []}
            mock_get.return_value = mock_resp

            result = fetch_margin_trading("20260505")

        assert result == {}

    def test_network_error_returns_empty(self):
        """網路錯誤時回傳空 dict，不 raise"""
        with patch("chip_data.requests.get") as mock_get:
            mock_get.side_effect = Exception("Network error")

            result = fetch_margin_trading("20260505")

        assert result == {}


# ---------------------------------------------------------------------------
# filter_margin_risk
# ---------------------------------------------------------------------------


class TestFilterMarginRisk:
    def _sample_data(self):
        return {
            "2330": {
                "margin_balance": 12000.0, "margin_prev": 10000.0,
                "margin_change": 2000.0,   # 增幅 20% > 10%
                "short_balance": 3000.0, "short_prev": 3200.0, "short_change": -200.0,
            },
            "2454": {
                "margin_balance": 5100.0, "margin_prev": 5000.0,
                "margin_change": 100.0,    # 增幅 2% <= 10%
                "short_balance": 1100.0, "short_prev": 1000.0, "short_change": 100.0,
            },
            "1234": {
                "margin_balance": 8000.0, "margin_prev": 6000.0,
                "margin_change": 2000.0,   # 增幅 33.3% > 10%
                "short_balance": 500.0, "short_prev": 500.0, "short_change": 0.0,
            },
        }

    def test_above_threshold_returns_codes(self):
        """融資增幅超過門檻的股票應被回傳"""
        data = self._sample_data()
        result = filter_margin_risk(data, max_margin_change_pct=10.0)
        assert "2330" in result   # 20% > 10%
        assert "1234" in result   # 33.3% > 10%
        assert "2454" not in result  # 2% <= 10%

    def test_below_threshold_excluded(self):
        """融資增幅未超過門檻的股票不應出現"""
        data = self._sample_data()
        result = filter_margin_risk(data, max_margin_change_pct=50.0)
        assert result == []  # 20% 和 33.3% 都 <= 50%

    def test_prev_zero_skipped(self):
        """margin_prev == 0 時跳過，避免除以零"""
        data = {
            "9999": {
                "margin_balance": 1000.0, "margin_prev": 0.0,
                "margin_change": 1000.0,
                "short_balance": 0.0, "short_prev": 0.0, "short_change": 0.0,
            },
        }
        result = filter_margin_risk(data, max_margin_change_pct=10.0)
        assert result == []

    def test_empty_data_returns_empty(self):
        """空資料回傳 []"""
        result = filter_margin_risk({})
        assert result == []


# ---------------------------------------------------------------------------
# fetch_monthly_revenue
# ---------------------------------------------------------------------------

# 模擬 MOPS 回傳的 HTML 片段（含兩筆正常資料與一筆無效資料）
SAMPLE_MOPS_HTML = """
<html><body>
<table>
<tr>
  <td>2330</td><td>台積電</td><td>250,000,000</td>
  <td>240,000,000</td><td>220,000,000</td><td>4.17</td><td>13.64</td><td>extra</td>
</tr>
<tr>
  <td>2454</td><td>聯發科</td><td>50,000,000</td>
  <td>48,000,000</td><td>60,000,000</td><td>4.17</td><td>-16.67</td><td>extra</td>
</tr>
<tr>
  <td>INVALID</td><td>壞資料</td><td>not_a_number</td>
  <td>48,000,000</td><td>60,000,000</td><td>4.17</td><td>-16.67</td><td>extra</td>
</tr>
</table>
</body></html>
"""


class TestFetchMonthlyRevenue:
    def test_normal_parse(self):
        """正常解析 MOPS HTML，含 revenue/mom_pct/yoy_pct"""
        with patch("chip_data.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.text = SAMPLE_MOPS_HTML
            mock_post.return_value = mock_resp

            result = fetch_monthly_revenue(2025, 3)

        assert "2330" in result
        assert result["2330"]["revenue"] == 250_000_000.0
        assert result["2330"]["prev_month_revenue"] == 240_000_000.0
        assert result["2330"]["prev_year_revenue"] == 220_000_000.0
        assert result["2330"]["mom_pct"] == pytest.approx(4.17)
        assert result["2330"]["yoy_pct"] == pytest.approx(13.64)

        assert "2454" in result
        assert result["2454"]["yoy_pct"] == pytest.approx(-16.67)

    def test_invalid_rows_skipped(self):
        """無效列（非數字營收）直接跳過，不影響其他資料"""
        with patch("chip_data.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.text = SAMPLE_MOPS_HTML
            mock_post.return_value = mock_resp

            result = fetch_monthly_revenue(2025, 3)

        assert "INVALID" not in result
        assert len(result) == 2

    def test_roc_year_and_month_padding(self):
        """民國年轉換正確（西元-1911），月份補零"""
        with patch("chip_data.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.text = SAMPLE_MOPS_HTML
            mock_post.return_value = mock_resp

            fetch_monthly_revenue(2025, 3)

            call_kwargs = mock_post.call_args
            posted_data = call_kwargs[1].get("data") or call_kwargs[0][1]
            assert posted_data["year"] == "114"   # 2025 - 1911
            assert posted_data["month"] == "03"   # 補零

    def test_network_error_returns_empty(self):
        """網路錯誤時回傳空 dict，不 raise"""
        with patch("chip_data.requests.post") as mock_post:
            mock_post.side_effect = Exception("Network error")

            result = fetch_monthly_revenue(2025, 3)

        assert result == {}


# ---------------------------------------------------------------------------
# filter_revenue_growth
# ---------------------------------------------------------------------------


class TestFilterRevenueGrowth:
    def _sample_data(self):
        return {
            "2330": {"revenue": 250_000_000.0, "prev_month_revenue": 240_000_000.0,
                     "prev_year_revenue": 220_000_000.0, "mom_pct": 4.17, "yoy_pct": 13.64},
            "2454": {"revenue": 50_000_000.0, "prev_month_revenue": 48_000_000.0,
                     "prev_year_revenue": 60_000_000.0, "mom_pct": 4.17, "yoy_pct": -16.67},
            "6505": {"revenue": 80_000_000.0, "prev_month_revenue": 75_000_000.0,
                     "prev_year_revenue": 70_000_000.0, "mom_pct": 6.67, "yoy_pct": 14.29},
        }

    def test_above_threshold_returned(self):
        """年增率 >= 門檻的股票回傳"""
        result = filter_revenue_growth(self._sample_data(), min_yoy_pct=10.0)
        assert "2330" in result   # yoy=13.64 >= 10
        assert "6505" in result   # yoy=14.29 >= 10
        assert "2454" not in result  # yoy=-16.67 < 10

    def test_below_threshold_excluded(self):
        """年增率 < 門檻不回傳"""
        result = filter_revenue_growth(self._sample_data(), min_yoy_pct=20.0)
        assert result == []

    def test_default_threshold_is_10(self):
        """預設門檻 10%"""
        result = filter_revenue_growth(self._sample_data())
        assert "2330" in result
        assert "2454" not in result

    def test_empty_data_returns_empty(self):
        """空資料回傳 []"""
        assert filter_revenue_growth({}) == []
