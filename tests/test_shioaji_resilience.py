"""
tests/test_shioaji_resilience.py — 長時間抓取的韌性

正式環境徵狀（2026-09-02 回填）：
    抓到第 40 檔 → {'status_code': 401, 'detail': 'Token is expired'}
    之後每 30 秒印一次 "Not ready"，永遠不會結束，也不會回報失敗。

兩個缺陷：
  1. token 過期後不會重新登入
  2. 失敗後沒有逾時、沒有放棄條件——整個程序卡死，使用者只能 Ctrl-C，
     而且已經抓到的 40 檔資料全部丟掉

修法：單次呼叫加逾時；連續失敗達門檻先嘗試重新登入；再失敗就中止並
**回傳已取得的部分**，同時把原因講清楚。
"""
import pytest

import shioaji_history as sh
import shioaji_session


@pytest.fixture(autouse=True)
def _reset():
    shioaji_session.reset()
    yield
    shioaji_session.reset()


class TestCallWithTimeout:
    def test_returns_value_when_fast(self):
        assert sh._call_with_timeout(lambda: 42, timeout=5) == 42

    def test_returns_timeout_sentinel_when_slow(self):
        """★ 卡住的呼叫必須放棄等待。Shioaji 的 C++ 層自己會每 30 秒重試，
        沒有外層逾時就是永遠卡住。"""
        import time
        out = sh._call_with_timeout(lambda: time.sleep(5), timeout=0.2)
        assert out is sh.TIMEOUT

    def test_exception_becomes_none(self):
        assert sh._call_with_timeout(lambda: 1 / 0, timeout=5) is None


class TestReconnect:
    def test_reconnect_clears_cache_and_logs_in_again(self, monkeypatch):
        calls = []
        monkeypatch.setattr(shioaji_session, "_connect",
                            lambda *a, **k: (calls.append(1), object())[1])
        first = shioaji_session.get_api()
        second = shioaji_session.reconnect()
        assert second is not first
        assert len(calls) == 2

    def test_reconnect_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(shioaji_session, "_connect", lambda *a, **k: None)
        assert shioaji_session.reconnect() is None

    def test_reconnect_bypasses_failure_cooldown(self, monkeypatch):
        """★ 冷卻期是為了防止查價風暴，但「token 過期後主動重連」是明確的
        補救動作，不該被冷卻擋住。"""
        calls = []
        monkeypatch.setattr(shioaji_session, "_connect",
                            lambda *a, **k: (calls.append(1), None)[1])
        shioaji_session.get_api()          # 失敗，進入冷卻
        shioaji_session.reconnect()        # 必須仍然嘗試
        assert len(calls) == 2


class TestBatchResilience:
    def _api(self):
        class Api:
            pass
        return Api()

    def test_stops_after_consecutive_failures(self, monkeypatch):
        """★ 連續失敗達門檻要中止，不能耗到天荒地老。"""
        calls = []

        def always_fail(api, code, start, end, chunk_days=30):
            calls.append(code)
            return None

        monkeypatch.setattr(sh, "fetch_daily", always_fail)
        from datetime import date
        out = sh.fetch_daily_batch(
            self._api(), [f"{i:04d}" for i in range(50)],
            date(2026, 1, 1), date(2026, 6, 30),
            abort_after_failures=5,
        )
        assert out == {}
        assert len(calls) <= 6, "應在連續失敗 5 次後中止"

    def test_returns_partial_results_on_abort(self, monkeypatch):
        """★ 已經抓到的不能丟掉——原本的行為是整批作廢，40 檔白抓。"""
        import pandas as pd
        good = pd.DataFrame(
            {"Open": [1.0] * 100, "High": [1.0] * 100, "Low": [1.0] * 100,
             "Close": [1.0] * 100, "Volume": [1.0] * 100},
            index=pd.date_range("2026-01-01", periods=100, freq="D"),
        )

        def fail_after_two(api, code, start, end, chunk_days=30):
            return good if code in ("A", "B") else None

        monkeypatch.setattr(sh, "fetch_daily", fail_after_two)
        from datetime import date
        out = sh.fetch_daily_batch(
            self._api(), ["A", "B"] + [f"X{i}" for i in range(20)],
            date(2026, 1, 1), date(2026, 6, 30),
            abort_after_failures=3,
        )
        assert set(out) == {"A", "B"}

    def test_reconnect_attempted_before_aborting(self, monkeypatch):
        """token 過期是可復原的：中止前要先試一次重新登入。"""
        reconnects = []
        monkeypatch.setattr(sh, "fetch_daily",
                            lambda *a, **k: None)
        from datetime import date
        sh.fetch_daily_batch(
            self._api(), [f"{i:04d}" for i in range(20)],
            date(2026, 1, 1), date(2026, 6, 30),
            abort_after_failures=5,
            reconnect=lambda: (reconnects.append(1), None)[1],
        )
        assert reconnects, "中止前應嘗試重新登入"

    def test_success_resets_failure_streak(self, monkeypatch):
        """零星失敗不該累積成中止——只有『連續』失敗才代表連線壞了。"""
        import pandas as pd
        good = pd.DataFrame(
            {"Open": [1.0] * 100, "High": [1.0] * 100, "Low": [1.0] * 100,
             "Close": [1.0] * 100, "Volume": [1.0] * 100},
            index=pd.date_range("2026-01-01", periods=100, freq="D"),
        )
        seq = iter([None, good, None, good, None, good])
        monkeypatch.setattr(sh, "fetch_daily",
                            lambda *a, **k: next(seq, good))
        from datetime import date
        out = sh.fetch_daily_batch(
            self._api(), [f"C{i}" for i in range(6)],
            date(2026, 1, 1), date(2026, 6, 30),
            abort_after_failures=2,
        )
        assert len(out) == 3, "交錯失敗不應觸發中止"


class TestUsageReport:
    """Shioaji 對歷史資料有每日流量上限。回填 81 檔 × 8 個月的分鐘 K 是
    數百萬根 K 線，很容易撞上限——撞到之後的徵狀是 'Token is expired' 和
    無止盡的 'Not ready'，完全看不出真正原因。抓之前先問一次用量。"""

    def test_returns_none_when_api_missing(self):
        assert sh.usage_report(None) is None

    def test_returns_none_when_usage_unsupported(self):
        class Api:
            pass
        assert sh.usage_report(Api()) is None

    def test_parses_usage_fields(self):
        class U:
            connections = 1
            bytes = 50_000_000
            limit_bytes = 500_000_000
            remaining_bytes = 450_000_000
        class Api:
            def usage(self):
                return U()
        r = sh.usage_report(Api())
        assert r["used_mb"] == pytest.approx(47.68, abs=0.1)
        assert r["limit_mb"] == pytest.approx(476.8, abs=0.5)
        assert r["remaining_pct"] == pytest.approx(90.0, abs=0.5)

    def test_survives_usage_raising(self):
        class Api:
            def usage(self):
                raise RuntimeError("not ready")
        assert sh.usage_report(Api()) is None

    def test_handles_dict_style_usage(self):
        """不同 SDK 版本回 dict 或物件，兩種都要吃。"""
        class Api:
            def usage(self):
                return {"bytes": 10_000_000, "limit_bytes": 100_000_000,
                        "remaining_bytes": 90_000_000, "connections": 1}
        r = sh.usage_report(Api())
        assert r["remaining_pct"] == pytest.approx(90.0, abs=0.5)


class TestHistoryCache:
    def test_roundtrip(self, tmp_path):
        import pandas as pd
        df = pd.DataFrame(
            {"Open": [1.0], "High": [1.0], "Low": [1.0],
             "Close": [1.0], "Volume": [1.0]},
            index=pd.date_range("2026-01-01", periods=1, freq="D"),
        )
        sh.cache_save(str(tmp_path), "2330", df)
        got = sh.cache_load(str(tmp_path), "2330")
        assert got is not None and len(got) == 1

    def test_missing_returns_none(self, tmp_path):
        assert sh.cache_load(str(tmp_path), "9999") is None

    def test_corrupt_cache_returns_none_not_raise(self, tmp_path):
        """★ 壞掉的快取檔不得讓整批回填爆掉——重抓就好。"""
        import pathlib
        (pathlib.Path(tmp_path) / "9999.pkl").write_text("not a pickle")
        assert sh.cache_load(str(tmp_path), "9999") is None
