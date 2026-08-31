"""
tests/test_assess_none_indicators.py — 指標值為 None 時不得炸掉當沖評分

真實故障（2026-08-13 起每日重現）：
    DayTrading report push failed:
    '>=' not supported between instances of 'NoneType' and 'float'

成因：dict.get(key, default) 的 default 只在「key 不存在」時生效。技術指標
算不出來時（資料不足），key 是存在的、值為 None → 取到 None → 與門檻比較
即 TypeError。整個 build_daytrading_report 被 main.py 的 try/except 吞掉，
當沖預測從此一筆都沒產生（dt_prediction_log 0 筆）。

指標 / 籌碼 / 大盤三處都有同樣的取值模式，全部要能容忍 None。
"""
import pytest

from stock_query import _assess_day_trading


def _ind(**overrides):
    """完整且合法的技術指標；用 overrides 把個別欄位改成 None。"""
    base = {
        "volume_ratio": 1.5,
        "RSI": 55.0,
        "ATR": 3.0,
        "current_price": 100.0,
        "bullish_alignment": True,
        "bearish_alignment": False,
        "KD_K": 60.0,
        "KD_D": 50.0,
        "VWAP": 99.0,
    }
    base.update(overrides)
    return base


class TestNoneIndicatorsDoNotRaise:
    @pytest.mark.parametrize("field", [
        "volume_ratio", "RSI", "ATR", "current_price",
        "KD_K", "KD_D", "VWAP", "bullish_alignment", "bearish_alignment",
    ])
    def test_single_none_field(self, field):
        """任一技術指標為 None 都不得 raise，且仍回傳可用結果。"""
        result = _assess_day_trading(_ind(**{field: None}))
        assert isinstance(result["score"], int)
        assert isinstance(result["data_ok"], bool)

    def test_all_indicator_fields_none(self):
        """全部指標為 None（資料完全算不出來）也不得 raise。"""
        allnone = {k: None for k in _ind()}
        result = _assess_day_trading(allnone)
        assert isinstance(result["score"], int)


class TestNoneChipAndMarketDoNotRaise:
    @pytest.mark.parametrize("field", [
        "foreign_net", "investment_trust_net",
        "dealer_net", "foreign_continuous_buy",
    ])
    def test_none_chip_field(self, field):
        chip = {
            "foreign_net": 500, "investment_trust_net": 100,
            "dealer_net": 0, "foreign_continuous_buy": 2,
        }
        chip[field] = None
        result = _assess_day_trading(_ind(), chip=chip)
        assert isinstance(result["score"], int)

    @pytest.mark.parametrize("field", ["index_change_pct", "futures_premium_pct"])
    def test_none_market_field(self, field):
        market = {"index_change_pct": 0.5, "futures_premium_pct": 0.1}
        market[field] = None
        result = _assess_day_trading(_ind(), market=market)
        assert isinstance(result["score"], int)

    def test_everything_none_at_once(self):
        """指標 + 籌碼 + 大盤全 None：最壞情況也只能降級，不能炸。"""
        result = _assess_day_trading(
            {k: None for k in _ind()},
            chip={"foreign_net": None, "investment_trust_net": None,
                  "dealer_net": None, "foreign_continuous_buy": None},
            market={"index_change_pct": None, "futures_premium_pct": None},
        )
        assert isinstance(result["score"], int)


class TestNoRegressionOnGoodData:
    """修 None 不得改變正常資料的評分行為。"""

    def test_missing_key_still_uses_default(self):
        """key 不存在時（非 None），行為應與原本相同：用預設值、不炸。"""
        result = _assess_day_trading({"current_price": 100.0})
        assert isinstance(result["score"], int)

    def test_indicators_none_still_data_not_ok(self):
        """indicators 整個是 None 仍維持原本的『資料不足』語意。"""
        result = _assess_day_trading(None)
        assert result["data_ok"] is False
        assert result["score"] == 0

    def test_healthy_stock_scores_above_baseline(self):
        """量比大、RSI 健康、多頭排列、站上 VWAP → 應高於 baseline 5。"""
        result = _assess_day_trading(_ind(volume_ratio=2.5))
        assert result["score"] > 5
        assert result["data_ok"] is True

    def test_thin_volume_still_penalised(self):
        """量比不足的硬性否決不得因為 None 修補而失效。"""
        result = _assess_day_trading(_ind(volume_ratio=0.5))
        assert result["score"] <= 3
