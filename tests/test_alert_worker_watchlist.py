"""AlertWorker 要接受 MonitorAgent 實際傳給它的形狀。

2026-08-17 實際故障
-------------------
    09:05 確認進場 2324 仁寶
    09:05 確認：1 繼續 / 7 放棄
    09:10 DT tick 監控啟動失敗: 'str' object has no attribute 'get'

四天來第一筆確認進場，tick 監控卻沒起來——移動停損、目標價、即時停損全部
失去監控，只剩 13:15 的強制平倉兜底。

肇因是同一段正規化程式碼被複製到兩個地方：

    MonitorAgent.set_watchlist(picks: list[dict])
        self._watchlist = {p["code"]: p for p in picks if p.get("code")}

    AlertWorker.__init__(..., watchlist=...)
        self._watchlist = {p["code"]: p for p in (watchlist or []) if p.get("code")}

MonitorAgent.start() 把**已經轉成 dict** 的 self._watchlist 傳給 AlertWorker，
AlertWorker 又當成 list 迭代一次；迭代 dict 得到的是 key（字串），
對字串呼叫 .get() 就爆了。

AlertWorker 兩種形狀都要能收：呼叫端有的傳 list（原始 picks），
有的傳已正規化的 dict。
"""

from __future__ import annotations

import queue
from unittest.mock import MagicMock

import pytest


def _picks() -> list[dict]:
    return [
        {"code": "2324", "name": "仁寶", "entry_price": 43.4,
         "target_price": 45.0, "stop_loss_price": 42.1},
        {"code": "2317", "name": "鴻海", "entry_price": 263.0,
         "target_price": 270.0, "stop_loss_price": 255.0},
    ]


def _worker(watchlist):
    from monitor_agent import AlertWorker
    return AlertWorker(
        queue.Queue(), "data/research.db", None,
        auto_execute=False, api=MagicMock(), watchlist=watchlist,
    )


# ── 這是 2026-08-17 炸掉的那條路徑 ─────────────────────────────────────────────

def test_accepts_already_normalised_dict():
    """MonitorAgent.start() 傳的就是這個形狀。"""
    normalised = {p["code"]: p for p in _picks()}

    w = _worker(normalised)

    assert set(w._watchlist) == {"2324", "2317"}
    assert w._watchlist["2324"]["entry_price"] == pytest.approx(43.4)


def test_accepts_raw_list():
    """既有呼叫端傳原始 list，不可因為修正而壞掉。"""
    w = _worker(_picks())

    assert set(w._watchlist) == {"2324", "2317"}
    assert w._watchlist["2317"]["name"] == "鴻海"


# ── 邊界 ─────────────────────────────────────────────────────────────────────

def test_none_watchlist():
    assert _worker(None)._watchlist == {}


def test_empty_forms():
    assert _worker([])._watchlist == {}
    assert _worker({})._watchlist == {}


def test_entries_without_code_are_dropped():
    """原本的意圖（修正六）要保留：沒有 code 的 pick 不可當 key。"""
    w = _worker([{"code": "2324", "name": "仁寶"}, {"name": "無代號"}, {"code": "", "name": "空字串"}])

    assert set(w._watchlist) == {"2324"}


def test_end_to_end_start_does_not_raise():
    """重現整條路徑：set_watchlist 之後 start() 不可因形狀不符而失敗。

    _subscribe_ticks 換成 no-op——這裡要驗的是 AlertWorker 的建構，
    不是真的去訂閱行情。
    """
    from monitor_agent import MonitorAgent

    agent = MonitorAgent(
        api_key="", secret_key="", simulation=False,
        db_path="data/research.db", telegram_chat_id=None, api=MagicMock(),
        trailing_start_pct=3.0, trailing_gap_pct=2.0, auto_execute=True,
    )
    agent.set_watchlist(_picks())
    agent._subscribe_ticks = lambda: None

    agent.start()   # 先前在此拋 AttributeError

    assert set(agent._worker._watchlist) == {"2324", "2317"}
    agent.stop()
