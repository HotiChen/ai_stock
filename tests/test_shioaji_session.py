"""
tests/test_shioaji_session.py — 共用 Shioaji 連線

為什麼需要
----------
monitor_agent.ensure_connected() 每次呼叫都 sj.Shioaji() + login()，開一條新
session。把 yfinance 全面換成 Shioaji 之後，十幾個模組都需要 api——若各自
呼叫 ensure_connected，就會開十幾條連線（券商有連線數上限，且每次登入要數秒）。
正式環境的 log 早就出現過兩行 "Shioaji connected"，就是這個問題的徵兆。

本模組提供單一共用連線，並且：
  * 失敗要有冷卻期——否則每次查價都重試登入，會變成登入風暴
  * main.py 已經有連線時要能註冊進來，不重複登入
"""
import pytest

import shioaji_session


@pytest.fixture(autouse=True)
def _reset():
    shioaji_session.reset()
    yield
    shioaji_session.reset()


class _FakeApi:
    def __init__(self, tag="a"):
        self.tag = tag


class TestSharedSession:
    def test_connects_once_and_caches(self, monkeypatch):
        calls = []

        def fake_connect(*a, **k):
            calls.append(1)
            return _FakeApi()

        monkeypatch.setattr(shioaji_session, "_connect", fake_connect)
        first  = shioaji_session.get_api()
        second = shioaji_session.get_api()

        assert first is second
        assert len(calls) == 1, "第二次呼叫不應重新登入"

    def test_set_api_registers_existing_connection(self, monkeypatch):
        """main.py 啟動時已經連好線，要能直接註冊，不再登入一次。"""
        monkeypatch.setattr(shioaji_session, "_connect",
                            lambda *a, **k: pytest.fail("不該重新登入"))
        existing = _FakeApi("main")
        shioaji_session.set_api(existing)
        assert shioaji_session.get_api() is existing

    def test_set_api_none_clears(self, monkeypatch):
        shioaji_session.set_api(_FakeApi())
        shioaji_session.set_api(None)
        monkeypatch.setattr(shioaji_session, "_connect", lambda *a, **k: _FakeApi("new"))
        assert shioaji_session.get_api().tag == "new"


class TestFailureCooldown:
    def test_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(shioaji_session, "_connect", lambda *a, **k: None)
        assert shioaji_session.get_api() is None

    def test_does_not_retry_within_cooldown(self, monkeypatch):
        """★ 連線失敗後不得每次查價都重試——那會變成登入風暴，
        而且每次都要等 timeout，整個流程會被拖垮。"""
        calls = []
        monkeypatch.setattr(shioaji_session, "_connect",
                            lambda *a, **k: (calls.append(1), None)[1])
        now = [1000.0]
        monkeypatch.setattr(shioaji_session, "_now", lambda: now[0])

        shioaji_session.get_api()
        now[0] += 10            # 冷卻期內
        shioaji_session.get_api()
        assert len(calls) == 1

    def test_retries_after_cooldown(self, monkeypatch):
        calls = []
        monkeypatch.setattr(shioaji_session, "_connect",
                            lambda *a, **k: (calls.append(1), None)[1])
        now = [1000.0]
        monkeypatch.setattr(shioaji_session, "_now", lambda: now[0])

        shioaji_session.get_api()
        now[0] += shioaji_session.FAILURE_COOLDOWN_SEC + 1
        shioaji_session.get_api()
        assert len(calls) == 2

    def test_connect_false_never_connects(self, monkeypatch):
        """connect=False 用於「有連線就用、沒有就算了」的呼叫端
        （例如純顯示用途），不得因此觸發登入。"""
        monkeypatch.setattr(shioaji_session, "_connect",
                            lambda *a, **k: pytest.fail("不該登入"))
        assert shioaji_session.get_api(connect=False) is None

    def test_connect_false_returns_cached(self, monkeypatch):
        existing = _FakeApi("cached")
        shioaji_session.set_api(existing)
        assert shioaji_session.get_api(connect=False) is existing


class TestReset:
    def test_reset_clears_cache_and_cooldown(self, monkeypatch):
        calls = []
        monkeypatch.setattr(shioaji_session, "_connect",
                            lambda *a, **k: (calls.append(1), None)[1])
        shioaji_session.get_api()
        shioaji_session.reset()
        shioaji_session.get_api()
        assert len(calls) == 2
