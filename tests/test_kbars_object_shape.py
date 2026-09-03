"""
tests/test_kbars_object_shape.py — Kbars 在不同 SDK 版本的形狀

2026-09-03 診斷結果（shioaji 1.7.4）：

    type: shioaji.data.Kbars      ← pydantic model，不是 dict
    hasattr(raw, "get"): False
    raw.keys(): ['ts','Open','High','Low','Close','Volume','Amount']
    raw.Close → list              ← 要用屬性存取

而程式裡到處寫著 `kbars.get("ts")`：
    shioaji_history.kbars_to_df                 → 每次都回 None
    technical_indicators.fetch_intraday_vwap    → VWAP 永遠 None
    technical_indicators.fetch_indicators（舊）  → 已改走 fetch_daily

kbars_to_df 的寫法是 `... if hasattr(kbars,"get") else None`，所以它不會拋錯，
只會**安靜地每次回 None**——8:30 選股因此一支指標都算不出來。

這裡同時釘住兩種形狀：舊版的 dict 與新版的 pydantic 物件。
"""
from datetime import datetime

import pytest

pd = pytest.importorskip("pandas")

import shioaji_history as sh


def _rows(n=3):
    base = datetime(2026, 9, 1, 9, 0)
    ts = [int(pd.Timestamp(base).value) + i * 60_000_000_000 for i in range(n)]
    return {
        "ts": ts,
        "Open":   [100.0 + i for i in range(n)],
        "High":   [101.0 + i for i in range(n)],
        "Low":    [99.0 + i for i in range(n)],
        "Close":  [100.5 + i for i in range(n)],
        "Volume": [1000.0] * n,
    }


class _PydanticLikeKbars:
    """模擬 shioaji.data.Kbars：**沒有 .get**，欄位是屬性，有 keys()。"""

    def __init__(self, data):
        for k, v in data.items():
            setattr(self, k, v)
        self.Amount = [0.0] * len(data["ts"])
        self._keys = list(data.keys()) + ["Amount"]

    def keys(self):
        return list(self._keys)


class TestFieldAccessor:
    def test_reads_dict_style(self):
        assert sh._kbars_field(_rows(), "Close")[0] == 100.5

    def test_reads_attribute_style(self):
        kb = _PydanticLikeKbars(_rows())
        assert sh._kbars_field(kb, "Close")[0] == 100.5

    def test_missing_field_returns_none(self):
        assert sh._kbars_field(_rows(), "NotThere") is None
        assert sh._kbars_field(_PydanticLikeKbars(_rows()), "NotThere") is None

    def test_none_kbars(self):
        assert sh._kbars_field(None, "Close") is None


class TestKbarsToDfHandlesBothShapes:
    def test_dict_shape(self):
        df = sh.kbars_to_df(_rows())
        assert df is not None and len(df) == 3

    def test_pydantic_shape(self):
        """★ 這是正式環境實際遇到的形狀（shioaji 1.7.4）。"""
        df = sh.kbars_to_df(_PydanticLikeKbars(_rows()))
        assert df is not None, "pydantic 形狀的 Kbars 必須也能轉換"
        assert len(df) == 3
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]

    def test_pydantic_values_correct(self):
        df = sh.kbars_to_df(_PydanticLikeKbars(_rows()))
        assert df["Close"].iloc[0] == 100.5
        assert df["High"].iloc[-1] == 103.0

    def test_pydantic_missing_column_returns_none(self):
        """欄位不全時仍要拒收——半份資料會算出看似合理卻錯誤的指標。"""
        data = _rows()
        del data["Volume"]
        kb = _PydanticLikeKbars({**data, "Volume": None})
        kb.Volume = None
        assert sh.kbars_to_df(kb) is None

    def test_empty_pydantic_returns_none(self):
        kb = _PydanticLikeKbars({k: [] for k in
                                 ("ts", "Open", "High", "Low", "Close", "Volume")})
        assert sh.kbars_to_df(kb) is None


class TestIntradayVwapHandlesPydantic:
    def test_vwap_computed_from_pydantic_kbars(self):
        """fetch_intraday_vwap 也寫著 kbars.get("ts")，在 1.7.4 會永遠回 None。"""
        from unittest.mock import MagicMock

        from technical_indicators import fetch_intraday_vwap
        api = MagicMock()
        api.Contracts.Stocks.get.return_value = MagicMock()
        api.kbars.return_value = _PydanticLikeKbars(_rows(60))
        assert fetch_intraday_vwap("2330", api=api) is not None
