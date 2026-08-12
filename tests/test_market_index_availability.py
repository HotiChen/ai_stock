"""大盤指數：取不到資料時必須說「不知道」，不能說「盤平」。

2026-08-12 實際發生
-------------------
09:05 開盤確認時，log 顯示：

    DT 9:05 大盤：index=+0.00% futures=+0.00%
    DT 9:05 放棄 1563 巧新: ...大盤無方向...
    DT 9:05 放棄 2302 麗正: ...大盤無力...
    （共 8 檔全部放棄）
    DT 9:05 確認：0 繼續 / 8 放棄

當天加權指數實際上漲 **0.63%**（Shioaji: close=45407.05, open=45175.7）。
`fetch_market_index_change()` 走 yfinance，而 yfinance 在這台機器上長期取不到
台股資料，於是回傳預設值 0.0。AI 讀到「大盤 +0.00%」，合理地推論出今天沒有
盤勢可依循，把所有候選都放棄了。

判斷邏輯沒有錯，錯的是餵進去的資料。而且只要 yfinance 一直失敗，這件事會
**每天重演**，當沖系統永遠不會進場。

兩個要求
--------
1. 有 Shioaji 就用 Shioaji（實測可取得真實指數），yfinance 只當備援。
2. 兩個來源都失敗時回傳 ``None``，而不是 ``0.0``。
   「取不到資料」與「大盤持平」對 AI 是完全不同的訊號，不可混為一談——
   這和 daytrading_review 區分 ``untestable`` 與 ``neutral`` 是同一個道理。

既有的 ``fetch_market_index_change()`` 維持回傳 float（0.0 代表失敗）以維持
向後相容，見 test_market_index.py。新的語意走 ``fetch_market_index_pct()``。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _snapshot(change_rate: float):
    """模擬 Shioaji snapshots() 回傳的單一快照。"""
    snap = MagicMock()
    snap.change_rate = change_rate
    return [snap]


def _api_with_index(change_rate: float):
    api = MagicMock()
    api.snapshots.return_value = _snapshot(change_rate)
    return api


# ── 來源優先序 ────────────────────────────────────────────────────────────────

def test_prefers_shioaji_over_yfinance():
    """Shioaji 可用時就用它——yfinance 在這台機器上取不到台股資料。"""
    from market_index import fetch_market_index_pct

    with patch("market_index._get_index_api", return_value=_api_with_index(0.63)), \
         patch("market_index.yf") as mock_yf:
        result = fetch_market_index_pct()

    assert result == pytest.approx(0.63)
    mock_yf.Ticker.assert_not_called(), "Shioaji 成功時不該再打 yfinance"


def test_falls_back_to_yfinance_when_shioaji_unavailable():
    """沒有 Shioaji 連線時仍要能從 yfinance 取得。"""
    from market_index import fetch_market_index_pct

    with patch("market_index._get_index_api", return_value=None), \
         patch("market_index.yf") as mock_yf:
        ticker = MagicMock()
        ticker.history.return_value = pd.DataFrame({"Close": [18000.0, 18180.0]})
        mock_yf.Ticker.return_value = ticker
        result = fetch_market_index_pct()

    assert result == pytest.approx(1.0)


# ── 核心：取不到資料不可偽裝成盤平 ─────────────────────────────────────────────

def test_returns_none_when_both_sources_fail():
    """這是整個修正的重點：回 None，不是 0.0。"""
    from market_index import fetch_market_index_pct

    with patch("market_index._get_index_api", return_value=None), \
         patch("market_index.yf") as mock_yf:
        mock_yf.Ticker.side_effect = Exception("network error")
        result = fetch_market_index_pct()

    assert result is None, "取不到資料卻回傳 0.0，AI 會誤判成『大盤持平』"


def test_none_is_distinguishable_from_a_genuinely_flat_market():
    """大盤真的收平盤時回 0.0——與『取不到』必須分得開。"""
    from market_index import fetch_market_index_pct

    with patch("market_index._get_index_api", return_value=_api_with_index(0.0)):
        result = fetch_market_index_pct()

    assert result == 0.0
    assert result is not None


def test_shioaji_returning_none_change_rate_falls_through():
    """快照拿到了但欄位是 None——視同該來源失敗，不可當成 0%。"""
    from market_index import fetch_market_index_pct

    api = MagicMock()
    api.snapshots.return_value = _snapshot(None)

    with patch("market_index._get_index_api", return_value=api), \
         patch("market_index.yf") as mock_yf:
        mock_yf.Ticker.side_effect = Exception("no data")
        result = fetch_market_index_pct()

    assert result is None


# ── 向後相容 ─────────────────────────────────────────────────────────────────

def test_legacy_wrapper_still_returns_zero_on_failure():
    """舊介面維持原本契約，既有呼叫端與測試不受影響。"""
    from market_index import fetch_market_index_change

    with patch("market_index.fetch_market_index_pct", return_value=None):
        assert fetch_market_index_change() == 0.0


def test_legacy_wrapper_passes_through_real_values():
    from market_index import fetch_market_index_change

    with patch("market_index.fetch_market_index_pct", return_value=0.63):
        assert fetch_market_index_change() == pytest.approx(0.63)
