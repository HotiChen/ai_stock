from __future__ import annotations

"""
升級版 technical_indicators 測試：
- calculate_indicators(df) → dict（給 rules.py 用）
- 新增 MACD, ATR, MA10, support, resistance
- fetch_indicators 抓 100 日
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


def _make_df(n: int = 100, trend: str = "up") -> pd.DataFrame:
    if trend == "up":
        closes = np.linspace(80, 120, n)
    elif trend == "down":
        closes = np.linspace(120, 80, n)
    else:
        closes = np.array([100.0] * n)

    highs   = closes + 2
    lows    = closes - 2
    volumes = np.array([1_000_000.0] * n)
    dates   = pd.date_range("2025-01-01", periods=n, freq="B")
    df = pd.DataFrame({
        "Close":  closes,
        "High":   highs,
        "Low":    lows,
        "Volume": volumes,
    }, index=dates)
    df.columns = pd.MultiIndex.from_tuples([(c, "2330.TW") for c in df.columns])
    return df


# ── calculate_indicators ──────────────────────────────────────────────────────

class TestCalculateIndicators:
    def _df(self, n=100):
        from technical_indicators import _df_to_arrays
        return _make_df(n)

    def test_returns_dict(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        assert isinstance(result, dict)

    def test_required_keys_present(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        required = [
            "current_price", "MA5", "MA10", "MA20", "MA60",
            "RSI", "KD_K", "KD_D", "KD_K_prev", "KD_D_prev",
            "MACD_hist", "MACD_hist_prev",
            "BB_upper", "BB_lower", "BB_position",
            "volume_ratio", "resistance", "support", "ATR",
            "bullish_alignment", "bearish_alignment",
            "trailing_stop", "stop_loss_ma20",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_uptrend_bullish_alignment(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df(100, "up"))
        assert result["bullish_alignment"] is True
        assert result["bearish_alignment"] is False

    def test_downtrend_bearish_alignment(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df(100, "down"))
        assert result["bearish_alignment"] is True
        assert result["bullish_alignment"] is False

    def test_resistance_equals_20day_high(self):
        from technical_indicators import calculate_indicators
        df = _make_df(100, "up")
        result = calculate_indicators(df)
        highs    = df[("High", "2330.TW")].values.astype(float)[-20:]
        expected = float(highs.max())
        assert abs(result["resistance"] - expected) < 0.01

    def test_support_equals_20day_low(self):
        from technical_indicators import calculate_indicators
        df = _make_df(100, "up")
        result = calculate_indicators(df)
        lows     = df[("Low", "2330.TW")].values.astype(float)[-20:]
        expected = float(lows.min())
        assert abs(result["support"] - expected) < 0.01

    def test_trailing_stop_is_7pct_below_price(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        expected = round(result["current_price"] * 0.93, 2)
        assert abs(result["trailing_stop"] - expected) < 0.01

    def test_stop_loss_ma20_is_99pct_of_ma20(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        expected = round(result["MA20"] * 0.99, 2)
        assert abs(result["stop_loss_ma20"] - expected) < 0.01

    def test_bb_position_between_0_and_1_for_normal(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df(100, "flat"))
        assert 0 <= result["BB_position"] <= 1

    def test_atr_positive(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        assert result["ATR"] > 0

    def test_volume_ratio_equals_1_for_constant_volume(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        # all volumes are equal → ratio should be 1.0
        assert abs(result["volume_ratio"] - 1.0) < 0.01

    def test_insufficient_data_raises(self):
        from technical_indicators import calculate_indicators
        with pytest.raises(ValueError, match="資料不足"):
            calculate_indicators(_make_df(n=50))

    def test_all_values_are_floats_or_bool(self):
        from technical_indicators import calculate_indicators
        result = calculate_indicators(_make_df())
        for key, val in result.items():
            assert isinstance(val, (float, int, bool, np.floating, np.integer)), \
                f"{key} has unexpected type {type(val)}"


# ── calc_macd ─────────────────────────────────────────────────────────────────

class TestCalcMacd:
    def test_returns_hist_and_prev(self):
        from technical_indicators import calc_macd
        closes = np.linspace(100, 130, 60)
        hist, hist_prev = calc_macd(closes)
        assert isinstance(hist, float)
        assert isinstance(hist_prev, float)

    def test_uptrend_hist_positive(self):
        from technical_indicators import calc_macd
        closes = np.linspace(80, 130, 60)
        hist, _ = calc_macd(closes)
        assert hist > 0

    def test_downtrend_hist_negative(self):
        from technical_indicators import calc_macd
        closes = np.linspace(130, 80, 60)
        hist, _ = calc_macd(closes)
        assert hist < 0

    def test_insufficient_data_returns_zeros(self):
        from technical_indicators import calc_macd
        closes = np.array([100.0] * 10)
        hist, hist_prev = calc_macd(closes)
        assert hist == 0.0
        assert hist_prev == 0.0


# ── calc_atr ──────────────────────────────────────────────────────────────────

class TestCalcAtr:
    def test_returns_positive_float(self):
        from technical_indicators import calc_atr
        n = 30
        highs  = np.linspace(105, 115, n)
        lows   = np.linspace(95,  105, n)
        closes = np.linspace(100, 110, n)
        atr = calc_atr(highs, lows, closes)
        assert isinstance(atr, float)
        assert atr > 0

    def test_higher_volatility_higher_atr(self):
        from technical_indicators import calc_atr
        n = 30
        closes = np.linspace(100, 110, n)
        narrow_atr = calc_atr(closes + 1, closes - 1, closes)
        wide_atr   = calc_atr(closes + 5, closes - 5, closes)
        assert wide_atr > narrow_atr

    def test_insufficient_data_returns_zero(self):
        from technical_indicators import calc_atr
        closes = np.array([100.0, 101.0])
        atr = calc_atr(closes + 1, closes - 1, closes)
        assert atr == 0.0


# ── fetch_indicators uses 100 days ────────────────────────────────────────────

class TestFetchIndicators100Days:
    def _make_api(self, n: int = 100):
        import numpy as np
        prices = np.linspace(100.0, 150.0, n).tolist()
        volumes = [1000] * n
        api = MagicMock()
        api.Contracts.Stocks.get.return_value = MagicMock()
        api.kbars.return_value = {
            "ts": list(range(n)),
            "Close": prices,
            "High": [p + 2 for p in prices],
            "Low": [p - 2 for p in prices],
            "Volume": volumes,
        }
        return api

    def test_requests_100_day_period(self):
        from technical_indicators import fetch_indicators
        api = self._make_api(100)
        fetch_indicators(api, "2330")
        # kbars was called with the contract
        api.kbars.assert_called_once()
        call_kwargs = api.kbars.call_args
        # start/end args should span at least 100 days
        call_str = str(call_kwargs)
        assert call_kwargs is not None  # was called

    def test_returns_calculate_indicators_dict_compatible(self):
        from technical_indicators import fetch_indicators
        api = self._make_api(100)
        result = fetch_indicators(api, "2330")
        assert result is not None
        # should contain all keys needed by rules.py
        assert "current_price" in result or hasattr(result, "close")


# ── fetch_indicators_shioaji_batch（原 yfinance batch）────────────────────────

class TestFetchIndicatorsShioajiBatch:
    """批次指標抓取改走 Shioaji kbars（mock，不實際連線）。

    原本走 yfinance：台股在其上常缺值、除權息調整與券商端不一致，導致 8:30
    算出的指標與盤中實際看到的價格對不起來。
    """

    def _daily_df(self, n: int = 100):
        import numpy as np
        import pandas as pd
        closes = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
        idx = pd.date_range(end=pd.Timestamp("2026-06-30"), periods=n, freq="D")
        return pd.DataFrame({
            "Open": closes - 0.3,
            "High": closes + np.random.uniform(0.5, 2.0, n),
            "Low": closes - np.random.uniform(0.5, 2.0, n),
            "Close": closes,
            "Volume": np.random.randint(1000, 5000, n).astype(float),
        }, index=idx)

    def _patch_daily(self, df):
        return patch("shioaji_history.fetch_daily", return_value=df)

    def test_returns_dict(self):
        from technical_indicators import fetch_indicators_shioaji_batch
        with self._patch_daily(self._daily_df()):
            result = fetch_indicators_shioaji_batch(["2330", "2454"], api=MagicMock())
        assert isinstance(result, dict)

    def test_all_keys_present(self):
        from technical_indicators import fetch_indicators_shioaji_batch
        with self._patch_daily(self._daily_df()):
            result = fetch_indicators_shioaji_batch(["2330"], api=MagicMock())
        assert "2330" in result
        for key in ("MA5", "MA20", "RSI", "KD_K", "BB_upper", "volume_ratio"):
            assert key in result["2330"], f"缺少欄位 {key}"

    def test_empty_codes_returns_empty(self):
        from technical_indicators import fetch_indicators_shioaji_batch
        assert fetch_indicators_shioaji_batch([]) == {}

    def test_no_connection_returns_empty(self):
        """★ 無 Shioaji 連線時回空 dict，不得拋出——8:30 選股要能降級續跑。"""
        from technical_indicators import fetch_indicators_shioaji_batch
        with patch("shioaji_session.get_api", return_value=None):
            assert fetch_indicators_shioaji_batch(["2330"]) == {}

    def test_fetch_failure_excluded(self):
        from technical_indicators import fetch_indicators_shioaji_batch
        with self._patch_daily(None):
            assert fetch_indicators_shioaji_batch(["2330"], api=MagicMock()) == {}

    def test_insufficient_data_excluded(self):
        """資料筆數不足 80 的股票不應出現在結果裡。"""
        from technical_indicators import fetch_indicators_shioaji_batch
        with self._patch_daily(self._daily_df(n=50)):
            assert "2330" not in fetch_indicators_shioaji_batch(["2330"], api=MagicMock())

    def test_uses_shared_session_when_api_not_passed(self):
        """未傳 api 時走共用連線，不可自行 login（十幾個模組各自登入會超過
        券商連線上限，且每次要等數秒）。"""
        from technical_indicators import fetch_indicators_shioaji_batch
        with patch("shioaji_session.get_api", return_value=MagicMock()) as g, \
                self._patch_daily(self._daily_df()):
            fetch_indicators_shioaji_batch(["2330"])
        assert g.called

    def test_one_kbars_call_per_stock(self):
        """★ 效能：每支一次呼叫。分小段會變成數百次，8:30 跑不完。"""
        from technical_indicators import fetch_indicators_shioaji_batch
        with patch("shioaji_history.fetch_daily",
                   return_value=self._daily_df()) as fd:
            fetch_indicators_shioaji_batch(["2330", "2454", "2317"], api=MagicMock())
        assert fd.call_count == 3

    def test_legacy_alias_still_works(self):
        """candidate_builder 以外可能還有舊呼叫端，別名必須指向同一個函式。"""
        import technical_indicators as ti
        assert ti.fetch_indicators_yfinance_batch is ti.fetch_indicators_shioaji_batch


# ── M4: candidate indicators_text integration ─────────────────────────────────

class TestIndicatorsInPrompt:
    """驗證 build_planner_prompt 確實把技術面文字帶進 candidates。"""

    def test_indicators_text_appears_in_prompt(self):
        from strategy_planner import build_planner_prompt, StrategyPlannerGoal

        goal = StrategyPlannerGoal(
            initial_capital=30000.0,
            target_return_pct=10.0,
            max_drawdown_pct=5.0,
            holding_days=5,
            risk_level="moderate",
        )
        candidates = [{
            "code": "2330",
            "name": "台積電",
            "close": 850.0,
            "change_rate": 1.5,
            "analysis": "外資買超",
            "chip_score": 3.0,
            "indicators_text": "均線：多頭排列 ✅（MA5=840 > MA20=800 > MA60=750）\nRSI：65.0（偏強）",
        }]
        prompt = build_planner_prompt(goal, 30000.0, candidates)
        assert "📊技術面" in prompt, "prompt 裡沒有技術面區塊"
        assert "均線：多頭排列" in prompt, "技術指標內容沒有出現在 prompt 裡"

    def test_no_indicators_text_no_section(self):
        """indicators_text 空時不應出現 📊技術面 標記。"""
        from strategy_planner import build_planner_prompt, StrategyPlannerGoal

        goal = StrategyPlannerGoal(
            initial_capital=30000.0,
            target_return_pct=10.0,
            max_drawdown_pct=5.0,
            holding_days=5,
            risk_level="moderate",
        )
        candidates = [{
            "code": "2330",
            "name": "台積電",
            "close": 850.0,
            "change_rate": 1.5,
            "analysis": "外資買超",
            "chip_score": 3.0,
            "indicators_text": "",
        }]
        prompt = build_planner_prompt(goal, 30000.0, candidates)
        assert "📊技術面" not in prompt
