"""
tests/test_doctor.py — 系統自檢

這三天的每一個故障都可以被一次自檢抓到，但我們是靠人工一行一行 grep 找出來的：

  2026-08-21  data/HALT 被誤觸 → 12 天靜默停擺
  2026-09-02  launchd 讀不到外接 SSD（macOS 權限）
  2026-09-02  pgrep 抓到正在關閉的 process，誤判「已在執行」
  2026-09-03  kbars 單次查詢上限 30 天（原有 bug，數月未被發現）
  2026-09-03  Kbars 是 pydantic 物件、沒有 .get → 指標永遠算不出來
  2026-09-03  .env 行內註解變成密碼雜湊 → 登入永遠失敗

最有價值的一項是「真的抓一支股票、驗證欄位齊全且數值合理」——上面最後三項
都會被它當場攔下。mock 測不出來的問題，只有真的打一次 API 才會現形。

每個檢查回傳 CheckResult，彼此獨立：一項失敗不得中斷其他檢查。
"""
from unittest.mock import MagicMock, patch

import pytest

import doctor


class TestCheckResult:
    def test_statuses(self):
        assert doctor.OK == "ok"
        assert doctor.WARN == "warn"
        assert doctor.FAIL == "fail"


class TestHaltCheck:
    def test_fails_loudly_when_halted(self, tmp_path, monkeypatch):
        """★ HALT 是 12 天靜默停擺的元凶，必須大聲。"""
        import halt as halt_mod
        monkeypatch.setattr(halt_mod, "_HALT_FILE", tmp_path / "HALT")
        halt_mod.halt(reason="test")
        r = doctor.check_halt()
        assert r.status == doctor.FAIL
        assert "暫停" in r.message

    def test_ok_when_not_halted(self, tmp_path, monkeypatch):
        import halt as halt_mod
        monkeypatch.setattr(halt_mod, "_HALT_FILE", tmp_path / "HALT")
        assert doctor.check_halt().status == doctor.OK


class TestEnvCheck:
    def test_detects_inline_comment_as_value(self, monkeypatch):
        """★ .env 行內註解變成設定值——登入永遠失敗且無提示的那個。"""
        monkeypatch.setenv("USER_PASSWORD_HASH", "# bcrypt 雜湊（建議）")
        r = doctor.check_env()
        assert r.status in (doctor.WARN, doctor.FAIL)
        assert "註解" in r.message or "註解" in r.detail

    def test_reports_missing_required_keys(self, monkeypatch):
        for k in ("SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY"):
            monkeypatch.delenv(k, raising=False)
        r = doctor.check_env()
        assert r.status in (doctor.WARN, doctor.FAIL)

    def test_ok_when_all_present(self, monkeypatch):
        monkeypatch.setenv("SHIOAJI_API_KEY", "k")
        monkeypatch.setenv("SHIOAJI_SECRET_KEY", "s")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
        monkeypatch.delenv("USER_PASSWORD_HASH", raising=False)
        assert doctor.check_env().status == doctor.OK


class TestMarketDataCheck:
    """★ 最有價值的一項：真的抓一支股票，驗證欄位齊全且數值合理。"""

    def _df(self, n=100):
        import pandas as pd
        idx = pd.date_range(end=pd.Timestamp("2026-09-03"), periods=n, freq="D")
        return pd.DataFrame({
            "Open": [100.0] * n, "High": [101.0] * n, "Low": [99.0] * n,
            "Close": [100.5] * n, "Volume": [1000.0] * n}, index=idx)

    def test_ok_when_data_and_indicators_valid(self):
        with patch("shioaji_history.fetch_daily", return_value=self._df()):
            r = doctor.check_market_data(api=MagicMock())
        assert r.status == doctor.OK

    def test_fails_when_no_data(self):
        """★ 2026-09-03 早上的情況：kbars 回不出東西，8:30 零候選。"""
        with patch("shioaji_history.fetch_daily", return_value=None):
            r = doctor.check_market_data(api=MagicMock())
        assert r.status == doctor.FAIL

    def test_fails_when_too_few_bars(self):
        """資料不足 80 根就算不出指標——早點知道比 8:30 才發現好。"""
        with patch("shioaji_history.fetch_daily", return_value=self._df(n=20)):
            r = doctor.check_market_data(api=MagicMock())
        assert r.status == doctor.FAIL

    def test_fails_when_indicators_have_none(self):
        """★ 指標含 None 曾讓整份當沖報告 TypeError 炸掉。"""
        with patch("shioaji_history.fetch_daily", return_value=self._df()), \
             patch("technical_indicators.calculate_indicators",
                   return_value={"current_price": None, "RSI": 50.0}):
            r = doctor.check_market_data(api=MagicMock())
        assert r.status == doctor.FAIL
        assert "None" in r.detail or "缺" in r.message

    def test_fails_without_connection(self):
        r = doctor.check_market_data(api=None)
        assert r.status == doctor.FAIL


class TestQuotaCheck:
    def test_warns_when_low(self):
        with patch("shioaji_history.usage_report",
                   return_value={"used_mb": 480.0, "limit_mb": 500.0,
                                 "remaining_mb": 20.0, "remaining_pct": 4.0,
                                 "connections": 1}):
            assert doctor.check_quota(api=MagicMock()).status == doctor.WARN

    def test_ok_when_plenty(self):
        with patch("shioaji_history.usage_report",
                   return_value={"used_mb": 25.0, "limit_mb": 500.0,
                                 "remaining_mb": 475.0, "remaining_pct": 95.0,
                                 "connections": 1}):
            assert doctor.check_quota(api=MagicMock()).status == doctor.OK

    def test_warn_when_unavailable(self):
        with patch("shioaji_history.usage_report", return_value=None):
            assert doctor.check_quota(api=MagicMock()).status == doctor.WARN


class TestSchemaCheck:
    def test_detects_missing_column(self, tmp_path, monkeypatch):
        """★ CREATE TABLE IF NOT EXISTS 補不上新欄位，曾讓 adaptive_scorer
        靜默失敗數月（no such column: was_correct）。"""
        import sqlite3
        p = tmp_path / "review.db"
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE dt_prediction_log (date TEXT, code TEXT)")
        con.commit()
        con.close()
        r = doctor.check_db_schema(review_db=str(p))
        assert r.status == doctor.FAIL
        assert "was_correct" in r.detail or "欄位" in r.message

    def test_ok_after_migration(self, tmp_path):
        from daytrading_db import DaytradingDB
        p = str(tmp_path / "review.db")
        DaytradingDB(p)
        assert doctor.check_db_schema(review_db=p).status == doctor.OK


class TestReportAndExit:
    def _r(self, *statuses):
        return [doctor.CheckResult(name=f"c{i}", status=s, message="m")
                for i, s in enumerate(statuses)]

    def test_exit_code_zero_when_all_ok(self):
        assert doctor.exit_code(self._r("ok", "ok")) == 0

    def test_exit_code_zero_with_warnings(self):
        """warn 不該讓自動化流程失敗——只有 fail 才是紅燈。"""
        assert doctor.exit_code(self._r("ok", "warn")) == 0

    def test_exit_code_nonzero_on_failure(self):
        assert doctor.exit_code(self._r("ok", "fail")) != 0

    def test_report_lists_every_check(self):
        text = doctor.format_report(self._r("ok", "warn", "fail"))
        for n in ("c0", "c1", "c2"):
            assert n in text

    def test_telegram_report_leads_with_failures(self):
        """★ 推播要一眼看到壞的，不能讓人往下捲。"""
        results = [
            doctor.CheckResult("好的檢查", doctor.OK, "正常"),
            doctor.CheckResult("壞的檢查", doctor.FAIL, "掛了"),
        ]
        text = doctor.format_telegram(results)
        assert text.index("壞的檢查") < text.index("好的檢查")


class TestRunAllIsolation:
    def test_one_check_raising_does_not_stop_others(self, monkeypatch):
        """★ 自檢本身不得因為一項爆炸而全滅——那就完全失去意義了。"""
        monkeypatch.setattr(doctor, "check_halt",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        results = doctor.run_all_checks(api=None)
        assert len(results) > 1
        assert any(r.status == doctor.FAIL and "boom" in r.detail for r in results)


class TestDotenvLoaded:
    """★ doctor 必須自己載入 .env。

    2026-09-04 首次在正式機執行時誤報「缺少必填：SHIOAJI_API_KEY」——實際上
    .env 裡設好了，只是 doctor.py 直接讀 os.getenv 而沒有 load_dotenv()。
    健檢工具誤報比不檢查更糟：它會讓人不再相信它說的話。
    """

    def test_module_loads_dotenv_on_import(self):
        import inspect

        import doctor as d
        src = inspect.getsource(d)
        assert "load_dotenv" in src, "doctor 必須載入 .env"

    def test_env_check_sees_dotenv_values(self, tmp_path, monkeypatch):
        """把 .env 的值餵進來後，check_env 要看得到。"""
        monkeypatch.delenv("SHIOAJI_API_KEY", raising=False)
        monkeypatch.delenv("SHIOAJI_SECRET_KEY", raising=False)
        assert doctor.check_env().status == doctor.FAIL

        monkeypatch.setenv("SHIOAJI_API_KEY", "from-dotenv")
        monkeypatch.setenv("SHIOAJI_SECRET_KEY", "from-dotenv")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
        monkeypatch.delenv("USER_PASSWORD_HASH", raising=False)
        assert doctor.check_env().status == doctor.OK


class TestSdkVersionCheck:
    """永豐金伺服器會以 503 拒絕過舊的 shioaji：

        StatusCode: 503, Detail: Please update the version of shioaji by using
        `pip install -U shioaji`

    這不是憑證問題，但 doctor 原本會顯示「請確認 SHIOAJI_API_KEY」，
    把人導向完全錯誤的方向。
    """

    def test_503_reported_as_version_problem(self):
        from unittest.mock import patch
        err = ("StatusCode: 503, Detail: Please update the version of shioaji "
               "by using `pip install -U shioaji`")
        with patch("shioaji_session.get_api", side_effect=Exception(err)):
            r = doctor.check_shioaji()
        assert r.status == doctor.FAIL
        assert "版本" in r.message or "版本" in r.detail
        assert "pip install -U shioaji" in r.detail

    def test_other_errors_still_point_at_credentials(self):
        from unittest.mock import patch
        with patch("shioaji_session.get_api", return_value=None):
            r = doctor.check_shioaji()
        assert r.status == doctor.FAIL
        assert "SHIOAJI_API_KEY" in r.detail


@pytest.fixture(autouse=True)
def _no_session_leak():
    """★ 這組測試會把假 api 塞進 shioaji_session 的全域快取。

    monkeypatch 會還原被替換的函式，但**不會**清掉 get_api() 快取下來的
    物件——一個裸 object() 就這樣洩漏成全域連線，讓之後的測試拿到它並在
    api.Contracts 上炸掉（實際發生過，而且只在整套跑時才重現）。
    """
    import shioaji_session
    shioaji_session.reset()
    yield
    shioaji_session.reset()


class TestSdkVersionDetectionOnRealPath:
    """★ 上面的 TestSdkVersionCheck 用 side_effect 讓 get_api 拋例外，但真實
    路徑不是這樣：ensure_connected 會吞掉例外、log 之後回傳 None。

    所以 doctor 拿到的是 None，永遠走不到 503 分支——測試通過但現實不會動。
    修法：shioaji_session 記下最後一次失敗原因，doctor 讀它。
    """

    def test_session_records_last_error(self, monkeypatch):
        import shioaji_session
        shioaji_session.reset()
        err = ("StatusCode: 503, Detail: Please update the version of shioaji "
               "by using `pip install -U shioaji`")
        monkeypatch.setattr(shioaji_session, "_connect",
                            lambda *a, **k: (_ for _ in ()).throw(Exception(err)))
        assert shioaji_session.get_api() is None
        assert "503" in (shioaji_session.last_error() or "")

    def test_last_error_cleared_on_success(self, monkeypatch):
        import shioaji_session
        shioaji_session.reset()
        monkeypatch.setattr(shioaji_session, "_connect",
                            lambda *a, **k: (_ for _ in ()).throw(Exception("boom")))
        shioaji_session.get_api()
        shioaji_session.reset()
        monkeypatch.setattr(shioaji_session, "_connect", lambda *a, **k: object())
        assert shioaji_session.get_api() is not None
        assert shioaji_session.last_error() is None

    def test_doctor_uses_last_error_when_api_is_none(self, monkeypatch):
        """★ 這才是真實路徑：get_api 回 None，原因要從 last_error 拿。"""
        import shioaji_session
        monkeypatch.setattr(shioaji_session, "get_api", lambda *a, **k: None)
        monkeypatch.setattr(
            shioaji_session, "last_error",
            lambda: "StatusCode: 503, Detail: Please update the version of shioaji")
        r = doctor.check_shioaji()
        assert r.status == doctor.FAIL
        assert "版本" in r.message
        assert "pip install -U shioaji" in r.detail
