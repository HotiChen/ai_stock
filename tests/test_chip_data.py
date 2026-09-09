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


# ── H1: requests.get 帶 timeout 參數 ──────────────────────────────────────────

class TestRequestTimeout:
    """驗證 fetch_institutional_investors / fetch_margin_trading 都傳 timeout 給 requests.get。"""

    def test_institutional_investors_passes_timeout(self):
        with patch("chip_data.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"stat": "OK", "data": []}
            mock_get.return_value = mock_resp

            fetch_institutional_investors("20260505")

            call_kwargs = mock_get.call_args
            assert "timeout" in call_kwargs.kwargs, \
                "requests.get 未傳 timeout 關鍵字參數"
            assert call_kwargs.kwargs["timeout"] > 0

    def test_margin_trading_passes_timeout(self):
        with patch("chip_data.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"stat": "OK", "data": []}
            mock_get.return_value = mock_resp

            fetch_margin_trading("20260505")

            call_kwargs = mock_get.call_args
            assert "timeout" in call_kwargs.kwargs, \
                "requests.get 未傳 timeout 關鍵字參數"
            assert call_kwargs.kwargs["timeout"] > 0

    def test_continuous_buy_uses_rate_limit_sleep(self):
        """多日查詢時應在呼叫之間 sleep，避免打爆 API。"""
        import time
        call_count = 0
        data_by_date = {
            "20260505": {"2330": {"foreign_net": 100.0, "investment_trust_net": 10.0}},
            "20260504": {"2330": {"foreign_net": 200.0, "investment_trust_net": 20.0}},
            "20260503": {"2330": {"foreign_net": 300.0, "investment_trust_net": 30.0}},
        }

        def counting_fetcher(date_str):
            nonlocal call_count
            call_count += 1
            return data_by_date.get(date_str, {})

        with patch("chip_data.time.sleep") as mock_sleep:
            get_continuous_buy_days("2330", "20260505", data_fetcher=counting_fetcher, days=3)
            # 3 日查詢，前 2 次後應各 sleep 一次（第一次不 sleep）
            assert mock_sleep.call_count >= 2, \
                f"days=3 應至少 sleep 2 次，實際 {mock_sleep.call_count} 次"


# ── fetch_latest_institutional ────────────────────────────────────────────────

class TestFetchLatestInstitutional:
    """★ 2026-09-09 發現的根因：08:30 去問 TWSE 要「今天」的三大法人資料。

    TWSE 的 T86 是**收盤後**（約 15:00–16:00）才公布。08:30 那個時間點
    今天的資料根本不存在，端點回 stat != OK，fetch_institutional_investors
    照設計回 {}，於是 chip_today.get(code) 對每一檔都是 None。

    後果：LLM 在 8:30 看到的每一檔都「沒有法人資料」。實際落庫的 ai_summary
    長這樣——

        「籌碼黑盒、大盤-0.66%空頭氣氛、RSI 69.88超買，量雖然4.7倍
          但沒有法人確認，訊號不夠清晰——跳過。」

    2026-09-01 到 09-09 共五個交易日、90 檔預測，action=long **0 檔**。
    使用者以為是資料鏈斷了，實際上資料鏈是通的，只是每天都在問一個
    七小時後才會有答案的問題。

    正確行為：往前找最近一個有資料的交易日，並回報那是哪一天——
    「昨天的籌碼」和「今天的籌碼」是不同的資訊，呼叫端有權知道。
    """

    def _fetcher(self, available: dict):
        """available: {日期字串: 資料}。沒列到的日期回 {}（模擬非交易日／未公布）。"""
        calls = []

        def fetch(date_str):
            calls.append(date_str)
            return available.get(date_str, {})

        fetch.calls = calls
        return fetch

    def test_falls_back_to_previous_day(self):
        from chip_data import fetch_latest_institutional
        f = self._fetcher({"20260908": {"2330": {"foreign_net": 1000}}})
        data, as_of = fetch_latest_institutional("20260909", fetch=f)
        assert "2330" in data
        assert as_of == "20260908"

    def test_today_wins_when_available(self):
        """收盤後執行（或回填）時今天的資料就有了，不該再往前找。"""
        from chip_data import fetch_latest_institutional
        f = self._fetcher({"20260909": {"2330": {}}, "20260908": {"2454": {}}})
        data, as_of = fetch_latest_institutional("20260909", fetch=f)
        assert as_of == "20260909"
        assert f.calls == ["20260909"]

    def test_skips_weekend_and_holiday_gaps(self):
        """週末與連假整段沒有資料，要能一路往前走到有的那天。"""
        from chip_data import fetch_latest_institutional
        f = self._fetcher({"20260904": {"2330": {}}})
        data, as_of = fetch_latest_institutional("20260907", fetch=f)
        assert as_of == "20260904"
        assert f.calls == ["20260907", "20260906", "20260905", "20260904"]

    def test_gives_up_after_max_lookback(self):
        """★ 找不到就是找不到，不能回一個看起來正常的空 dict 讓上游誤以為
        「今天沒有法人買賣超」——那正是原本的失敗形狀。"""
        from chip_data import fetch_latest_institutional
        f = self._fetcher({})
        data, as_of = fetch_latest_institutional("20260909", max_lookback=3, fetch=f)
        assert data == {}
        assert as_of is None
        assert len(f.calls) == 3

    def test_fetcher_exception_does_not_abort_the_walk(self):
        from chip_data import fetch_latest_institutional

        def fetch(date_str):
            if date_str == "20260909":
                raise RuntimeError("TWSE 逾時")
            return {"2330": {}} if date_str == "20260908" else {}

        data, as_of = fetch_latest_institutional("20260909", fetch=fetch)
        assert as_of == "20260908"

    def test_defaults_to_today(self):
        import datetime as _dt
        from chip_data import fetch_latest_institutional
        f = self._fetcher({})
        fetch_latest_institutional(max_lookback=1, fetch=f)
        assert f.calls == [_dt.date.today().strftime("%Y%m%d")]


class TestPremarketUsesLatestNotToday:
    """★ 結構測試：8:30 的選股不得直接問「今天」的三大法人。

    這是 2026-09 連續五個交易日 0 檔 long 的成因。改回去就會再發生一次，
    而且症狀是「AI 每天都說跳過」——看起來像策略保守，不像故障。
    """

    def test_fetch_chip_data_uses_the_lookback_helper(self):
        import inspect

        import daytrading_report as dr
        src = inspect.getsource(dr._fetch_chip_data)
        assert "fetch_latest_institutional" in src
        assert "fetch_institutional_investors" not in src

    def test_fetch_chip_data_returns_the_as_of_date(self):
        """呼叫端必須知道籌碼是哪一天的——不然沒辦法分辨
        「今天沒有法人買賣超」和「根本沒查到」。"""
        from unittest.mock import patch

        import daytrading_report as dr
        with patch("chip_data.fetch_latest_institutional",
                   return_value=({"2330": {}}, "20260908")):
            data, as_of = dr._fetch_chip_data("20260909")
        assert as_of == "20260908"

    def test_failure_reports_none_not_empty_dict_only(self):
        from unittest.mock import patch

        import daytrading_report as dr
        with patch("chip_data.fetch_latest_institutional",
                   side_effect=RuntimeError("boom")):
            data, as_of = dr._fetch_chip_data("20260909")
        assert data == {} and as_of is None
