"""09:05 那行大盤 log 不可把「取不到」印成 +0.00%。

2026-08-17 09:05 正式環境，相鄰兩行：

    WARNING: 台指期溢貼水取不到——標示為『資料不可用』，不以 0%% 代替
    INFO:    DT 9:05 大盤：index=+0.66% futures=+0.00%

前一行才宣告資料不可用，下一行就印出一個看起來完全正常的 +0.00%。

features 落庫時是對的（futures_available: False），錯的只有這行 log——但
排查當沖為什麼不進場時，人看的就是這行。8/12 全數 skip 那次，誤導我們的
正是同一種「把不知道印成 0」的字串。

肇因是 index 有做 None 判斷而 futures 沒有：

    market.get("futures_premium_pct", 0) or 0

``.get`` 的預設只在 key 不存在時生效；key 在、值是 None 時回的是 None，
再被 ``or 0`` 轉成 0。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _capture(market: dict) -> str:
    """跑 09:05 的大盤 log，回傳那一行的內容。"""
    import main

    lines: list[str] = []

    class _Log:
        def info(self, fmt, *args, **kw):
            lines.append(fmt % args if args else fmt)
        def __getattr__(self, _):
            return lambda *a, **kw: None

    watching = SimpleNamespace(status="watching", code="2324", name="仁寶")

    with patch.object(main, "log", _Log()), \
         patch("daytrading_report._fetch_market", return_value=market), \
         patch("daytrading_monitor.load_daytrading_positions", return_value=[watching]):
        try:
            main._opening_confirm_dt_positions(MagicMock(), SimpleNamespace(llm_mode="decider"))
        except Exception:
            pass    # 後續流程不是這裡要驗的，只要那行 log 已經印出來

    hit = [l for l in lines if "DT 9:05 大盤" in l]
    assert hit, f"沒有印出大盤那行，實際印了：{lines}"
    return hit[0]


def _market(**kw) -> dict:
    m = {"index_change_pct": 0.66, "futures_premium_pct": 0.35,
         "index_available": True, "futures_available": True}
    m.update(kw)
    return m


# ── 這是 2026-08-17 誤導人的那一行 ────────────────────────────────────────────

def test_unavailable_futures_is_not_printed_as_zero():
    line = _capture(_market(futures_premium_pct=None, futures_available=False))

    assert "+0.00%" not in line, f"把取不到的期貨印成 0%：{line}"
    assert "資料無法取得" in line, f"應標示為取不到：{line}"


def test_unavailable_index_is_not_printed_as_zero():
    """index 這側先前已修，不可回退。"""
    line = _capture(_market(index_change_pct=None, index_available=False))

    assert "index=資料無法取得" in line, line


def test_both_unavailable():
    line = _capture(_market(index_change_pct=None, futures_premium_pct=None,
                            index_available=False, futures_available=False))

    assert "0.00%" not in line, line


# ── 有值時照常顯示 ────────────────────────────────────────────────────────────

def test_real_values_still_shown():
    line = _capture(_market())

    assert "+0.66%" in line and "+0.35%" in line, line


def test_a_genuine_zero_is_still_shown_as_zero():
    """真的平盤要印 0.00%，不可跟「取不到」混為一談。"""
    line = _capture(_market(futures_premium_pct=0.0))

    assert "+0.00%" in line, f"真實的 0 被吃掉了：{line}"
    assert "資料無法取得" not in line, line
