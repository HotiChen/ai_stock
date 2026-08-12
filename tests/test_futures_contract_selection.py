"""台指期近月合約要取對，取不到時不可偽裝成溢貼水 0%。

2026-08-12 實測
---------------
    fetch_spot_index(api)      → 45518.07   ✅
    fetch_futures_price(api)   → None       ❌
    fetch_futures_premium(api) → None

`fetch_futures_price` 拿的是 ``api.Contracts.Futures.TXF``，但那是**合約集合**
（``StreamMultiContract``），不是單一合約：

    可用合約: TXF202608, TXF202609, TXF202610, ..., TXFR1, TXFR2

把集合丟給 ``api.snapshots()`` 取不到報價。正確做法是取近月連續 ``TXFR1``
（實測 TXF202608 close=46126.0，到期 2026/08/19）。

連帶問題：``daytrading_report._fetch_market()`` 在期貨取不到時，讓
``futures_premium_pct`` 停留在預設的 ``0.0``，於是 9:05 的 prompt 顯示
「台指期溢貼水 +0.00%」——與大盤指數同一個病：把「不知道」講成「剛好持平」。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _snap(close: float):
    s = MagicMock()
    s.close = close
    return [s]


def _api_with_contracts(**contracts):
    """建立一個 Contracts.Futures.TXF 底下有指定合約的假 api。"""
    txf = MagicMock()
    for name, obj in contracts.items():
        setattr(txf, name, obj)
    api = MagicMock()
    api.Contracts.Futures.TXF = txf
    return api


# ── 合約選取 ─────────────────────────────────────────────────────────────────

def test_uses_near_month_continuous_contract():
    """要拿 TXFR1（近月連續），不是 TXF 這個集合本身。"""
    from futures_premium import fetch_futures_price

    r1 = MagicMock(name="TXFR1")
    api = _api_with_contracts(TXFR1=r1)
    api.snapshots.return_value = _snap(46126.0)

    result = fetch_futures_price(api=api)

    assert result == pytest.approx(46126.0)
    passed = api.snapshots.call_args[0][0]
    assert passed == [r1], "應該把 TXFR1 這個單一合約傳進 snapshots()"


def test_does_not_pass_the_contract_collection():
    """把集合丟進 snapshots() 是原本的 bug——不可再發生。"""
    from futures_premium import fetch_futures_price

    r1 = MagicMock(name="TXFR1")
    api = _api_with_contracts(TXFR1=r1)
    api.snapshots.return_value = _snap(46126.0)

    fetch_futures_price(api=api)

    passed = api.snapshots.call_args[0][0][0]
    assert passed is not api.Contracts.Futures.TXF, "傳進去的是合約集合，不是單一合約"


def test_falls_back_when_near_month_missing():
    """沒有 TXFR1 時（模擬環境偶有缺漏）要能退回明確的月份合約。"""
    from futures_premium import fetch_futures_price

    month = MagicMock(name="TXF202608")
    api = MagicMock()
    txf = MagicMock(spec=["TXF202608"])
    txf.TXF202608 = month
    api.Contracts.Futures.TXF = txf
    api.snapshots.return_value = _snap(46126.0)

    assert fetch_futures_price(api=api) == pytest.approx(46126.0)


# ── 取不到時的表達 ────────────────────────────────────────────────────────────

def test_market_marks_futures_unavailable_rather_than_zero():
    """_fetch_market 取不到期貨時要標示不可用，不可留 0.0 當成平水。"""
    from daytrading_report import _fetch_market

    with patch("market_index.fetch_market_index_pct", return_value=0.5), \
         patch("futures_premium.fetch_futures_premium", return_value=None):
        m = _fetch_market()

    assert m["futures_premium_pct"] is None, (
        "期貨取不到卻回 0.0，prompt 會顯示『台指期溢貼水 +0.00%』"
    )
    assert m["futures_available"] is False


def test_market_reports_real_futures_when_available():
    from daytrading_report import _fetch_market

    fp = MagicMock()
    fp.premium_pct = 1.34

    with patch("market_index.fetch_market_index_pct", return_value=0.5), \
         patch("futures_premium.fetch_futures_premium", return_value=fp):
        m = _fetch_market()

    assert m["futures_premium_pct"] == pytest.approx(1.34)
    assert m["futures_available"] is True
