"""指數連線的失敗不可是永久的，且失敗必須看得見。

2026-08-14 實際狀況
-------------------
08:30 的當沖預測產出了 8 檔候選，但 25 檔全部 ``action=skip``，AI 的理由是：

    「籌碼資料無法取得 + **大盤方向未知**，違反鐵律第4條」
    「技術爆表但**大盤籌碼全黑，沒大盤確認不敢動**」

落庫的 features 證實：

    market = {'index_change_pct': None, 'futures_premium_pct': None,
              'index_available': False, 'futures_available': False}

但同一時間手動呼叫 ``fetch_market_index_pct()`` 回傳 **1.1**。指數是拿得到的，
只是那個 process 拿不到。

兩個原因：

1. ``_get_index_api()`` 用 ``_index_api_tried`` 旗標做「試一次就永久放棄」。
   main.py 從 08:25 就常駐，若首次呼叫時 Shioaji 尚未就緒（開盤前多個
   session 同時登入容易失敗），這個 process 接下來**整天**都拿不到指數。
   當初這樣設計是怕盤中每分鐘重登拖慢流程，但代價是一次瞬時失敗毀掉一整天。

2. 本模組用 ``logging.getLogger`` 而非專案的 ``get_logger``，警告不會寫進
   logs/ai_stock.log。所以昨天特地加的診斷訊息，在事後查證時完全看不到——
   排查時只能靠猜。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_module_state():
    """每個測試都從乾淨的模組狀態開始（singleton 會跨測試殘留）。"""
    import market_index
    market_index._index_api = None
    market_index._index_api_tried = False
    yield
    market_index._index_api = None
    market_index._index_api_tried = False


def _snapshot(rate):
    s = MagicMock()
    s.change_rate = rate
    return [s]


# ── 重試 ─────────────────────────────────────────────────────────────────────

def test_login_failure_does_not_disable_the_session_permanently():
    """首次登入失敗後，下一次呼叫仍要再試一次。

    這是 2026-08-14 全數 skip 的直接原因：main.py 常駐 process 在開盤前
    某次呼叫失敗，之後整天都回 None。
    """
    import market_index

    good_api = MagicMock()
    good_api.snapshots.return_value = _snapshot(1.1)
    attempts = {"n": 0}

    def _login(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("Shioaji 尚未就緒")
        return good_api

    with patch.object(market_index, "_build_index_api", side_effect=_login):
        first = market_index.fetch_market_index_pct()
        second = market_index.fetch_market_index_pct()

    assert first is None, "第一次失敗，回 None 是對的"
    assert second == pytest.approx(1.1), "第二次必須重試並成功，不可沿用失敗狀態"
    assert attempts["n"] == 2


def test_successful_session_is_reused():
    """成功之後要沿用同一個連線，不可每次重登。"""
    import market_index

    api = MagicMock()
    api.snapshots.return_value = _snapshot(0.5)
    builds = {"n": 0}

    def _build():
        builds["n"] += 1
        return api

    with patch.object(market_index, "_build_index_api", side_effect=_build):
        market_index.fetch_market_index_pct()
        market_index.fetch_market_index_pct()
        market_index.fetch_market_index_pct()

    assert builds["n"] == 1, "連線成功後不該重複建立"


# ── 可見性 ───────────────────────────────────────────────────────────────────

def test_module_logs_into_the_project_log():
    """本模組的訊息必須進 ai_stock.log，否則事後無從查證。

    先前用 logging.getLogger，昨天加的「大盤指數取不到」警告從未出現在
    專案 log 裡，導致今天排查時只能靠猜。
    """
    import market_index

    handlers = market_index.log.handlers or market_index.log.parent.handlers
    paths = [
        getattr(h, "baseFilename", "")
        for h in handlers
    ]
    assert any("ai_stock.log" in p for p in paths), (
        f"market_index 的 logger 沒有寫入 ai_stock.log，實際 handlers: {paths}"
    )
