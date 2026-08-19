"""期貨的現貨指數要走 Shioaji，不能永遠掉到 yfinance。

2026-08-19 11:38 實際 log
-------------------------
    DEBUG: Shioaji spot index failed: 'Contracts' object has no attribute 'Indices'

futures_premium.fetch_spot_index 寫的是

    api.snapshots([api.Contracts.Indices["TAIEX"]])

屬性名與 key 都不對。Shioaji 的拼法是 ``Indexs``（不是 Indices），
加權指數是 ``Indexs.TSE["001"]``（不是 ["TAIEX"]）。同一個 repo 的
market_index.py:89 早就有正確寫法：

    api.Contracts.Indexs.TSE[_TSE_INDEX_CODE]

實測（2026-08-19 12:45）：

    api.Contracts.Indices["TAIEX"]        → AttributeError
    api.Contracts.Indexs.TSE["001"]       → close=44584.96

所以現貨永遠是 yfinance 拿的。這有兩個後果：

1. market_index 自己的註解就寫著「yfinance 在這台機器上長期取不到台股
   資料」。yfinance 一失敗，現貨就是 None，溢貼水整個算不出來——期貨
   價明明拿得到。
2. 溢貼水是「期貨價 − 現貨價」。期貨走 Shioaji、現貨走 yfinance，
   兩個來源的時間戳不同，差值直接被時間差污染。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _api_with_index(close: float):
    """做一個只認得 Shioaji 正確拼法的假 api。"""
    api = MagicMock()

    class _Contracts:
        class Indexs:
            TSE = {"001": "TSE_001_CONTRACT"}

        def __getattr__(self, name):
            raise AttributeError(f"'Contracts' object has no attribute '{name}'")

    api.Contracts = _Contracts()
    snap = MagicMock()
    snap.close = close
    api.snapshots.return_value = [snap]
    return api


# ── 這是 2026-08-19 log 裡那個 AttributeError ────────────────────────────────

def test_spot_index_comes_from_shioaji_when_a_session_exists():
    from futures_premium import fetch_spot_index

    api = _api_with_index(44584.96)

    # yfinance 整個關掉：要驗的是 Shioaji 那條路自己能通，
    # 不是「掉下去剛好也有值」。
    with patch("yfinance.Ticker", side_effect=AssertionError("不該走到 yfinance")):
        assert fetch_spot_index(api) == pytest.approx(44584.96)


def test_it_asks_for_the_contract_shioaji_actually_has():
    from futures_premium import fetch_spot_index

    api = _api_with_index(44584.96)
    with patch("yfinance.Ticker", side_effect=AssertionError("不該走到 yfinance")):
        fetch_spot_index(api)

    (contracts,), _ = api.snapshots.call_args
    assert contracts == ["TSE_001_CONTRACT"], \
        f"取的不是 Indexs.TSE['001']：{contracts}"


# ── 退路保留 ─────────────────────────────────────────────────────────────────

def test_falls_back_to_yfinance_when_shioaji_has_no_session():
    from futures_premium import fetch_spot_index

    df = MagicMock()
    df.empty = False
    df.__getitem__.return_value.iloc.__getitem__.return_value = 44700.0
    with patch("yfinance.Ticker") as t:
        t.return_value.history.return_value = df
        assert fetch_spot_index(None) == pytest.approx(44700.0)


def test_falls_back_to_yfinance_when_shioaji_raises():
    """Shioaji 有 session 但查詢失敗時，仍要退到 yfinance。"""
    from futures_premium import fetch_spot_index

    api = MagicMock()
    api.snapshots.side_effect = RuntimeError("boom")
    df = MagicMock()
    df.empty = False
    df.__getitem__.return_value.iloc.__getitem__.return_value = 44700.0
    with patch("yfinance.Ticker") as t:
        t.return_value.history.return_value = df
        assert fetch_spot_index(api) == pytest.approx(44700.0)


def test_everything_failing_is_none_not_zero():
    from futures_premium import fetch_spot_index

    api = MagicMock()
    api.snapshots.side_effect = RuntimeError("boom")
    with patch("yfinance.Ticker", side_effect=RuntimeError("no network")):
        assert fetch_spot_index(api) is None
