from __future__ import annotations

"""
tests/test_preflight.py — TDD 測試：啟動前環境檢查模組

涵蓋：
  (a) check_env：各種環境變數缺失與齊全情境
  (b) check_telegram：getMe 成功 / HTTP 錯誤 / 連線逾時 / token 未設定
  (c) check_shioaji：key 未設定（模擬模式）/ 連線成功 / ensure_connected 回傳 None / 模組載入失敗
  (d) check_db：目錄可寫 / 無寫入權限 / PRAGMA ok / PRAGMA 異常
  (e) check_playbook：檔案存在且標記完整 / 不存在 / 標記缺失
  (f) run_preflight：所有檢查爆炸也不 raise；Telegram 正常時發送摘要；Telegram 異常時略過
  (g) has_critical_failure：邏輯正確
  (h) main.py hook：preflight 例外被吞掉，排程器存活
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ── (a) check_env ──────────────────────────────────────────────────────────────

class TestCheckEnv:
    """check_env() 環境變數檢查。"""

    def _run(self, env: dict, monkeypatch):
        """清空全部相關 env，再注入指定的 env dict。"""
        keys = [
            "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY",
            "GEMINI_API_KEY", "GOOGLE_API_KEY",
        ]
        for k in keys:
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        import preflight
        return preflight.check_env()

    def test_all_required_set_returns_ok(self, monkeypatch):
        """必要環境變數全部設定 → ok=True，critical=False。"""
        result = self._run({
            "ANTHROPIC_API_KEY": "ak-test",
            "TELEGRAM_BOT_TOKEN": "tok-test",
            "TELEGRAM_CHAT_ID": "123",
        }, monkeypatch)
        assert result.ok is True
        assert result.critical is False

    def test_missing_anthropic_key_is_critical(self, monkeypatch):
        """缺少 ANTHROPIC_API_KEY → ok=False，critical=True。"""
        result = self._run({
            "TELEGRAM_BOT_TOKEN": "tok-test",
            "TELEGRAM_CHAT_ID": "123",
        }, monkeypatch)
        assert result.ok is False
        assert result.critical is True
        assert "ANTHROPIC_API_KEY" in result.detail

    def test_missing_telegram_bot_token_is_critical(self, monkeypatch):
        """缺少 TELEGRAM_BOT_TOKEN → ok=False，critical=True。"""
        result = self._run({
            "ANTHROPIC_API_KEY": "ak-test",
            "TELEGRAM_CHAT_ID": "123",
        }, monkeypatch)
        assert result.ok is False
        assert result.critical is True
        assert "TELEGRAM_BOT_TOKEN" in result.detail

    def test_missing_telegram_chat_id_is_critical(self, monkeypatch):
        """缺少 TELEGRAM_CHAT_ID → ok=False，critical=True。"""
        result = self._run({
            "ANTHROPIC_API_KEY": "ak-test",
            "TELEGRAM_BOT_TOKEN": "tok-test",
        }, monkeypatch)
        assert result.ok is False
        assert result.critical is True
        assert "TELEGRAM_CHAT_ID" in result.detail

    def test_missing_shioaji_keys_ok_noncritical(self, monkeypatch):
        """缺少 Shioaji key 但必要 key 齊全 → ok=True（模擬模式降級），critical=False。"""
        result = self._run({
            "ANTHROPIC_API_KEY": "ak-test",
            "TELEGRAM_BOT_TOKEN": "tok-test",
            "TELEGRAM_CHAT_ID": "123",
        }, monkeypatch)
        assert result.ok is True
        assert result.critical is False
        # detail 應提示模擬模式
        assert "模擬" in result.detail

    def test_missing_gemini_key_ok_noncritical(self, monkeypatch):
        """缺少 GEMINI_API_KEY 且 GOOGLE_API_KEY 也未設 → ok=True，非嚴重。"""
        result = self._run({
            "ANTHROPIC_API_KEY": "ak-test",
            "TELEGRAM_BOT_TOKEN": "tok-test",
            "TELEGRAM_CHAT_ID": "123",
        }, monkeypatch)
        assert result.ok is True
        assert result.critical is False

    def test_never_raises(self, monkeypatch):
        """check_env() 絕不拋例外。"""
        # 清空所有 key
        for k in ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                  "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"]:
            monkeypatch.delenv(k, raising=False)
        import preflight
        try:
            preflight.check_env()
        except Exception as e:
            pytest.fail(f"check_env() 不應拋例外，但拋出 {e!r}")


# ── (b) check_telegram ────────────────────────────────────────────────────────

class TestCheckTelegram:
    """check_telegram() 透過 getMe 驗證 Bot token。"""

    def test_200_returns_ok(self, monkeypatch):
        """getMe 回傳 200 → ok=True，detail 包含 bot username。"""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"result": {"username": "my_test_bot"}}

        import preflight
        with patch("preflight.requests") as mock_requests:
            mock_requests.get.return_value = fake_resp
            result = preflight.check_telegram()

        assert result.ok is True
        assert "my_test_bot" in result.detail

    def test_401_returns_fail_critical(self, monkeypatch):
        """getMe 回傳 401 → ok=False，critical=True。"""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bad-token")
        fake_resp = MagicMock()
        fake_resp.status_code = 401

        import preflight
        with patch("preflight.requests") as mock_requests:
            mock_requests.get.return_value = fake_resp
            result = preflight.check_telegram()

        assert result.ok is False
        assert result.critical is True
        assert "401" in result.detail

    def test_timeout_returns_fail(self, monkeypatch):
        """requests 逾時 → ok=False，不 raise。"""
        import requests as req_lib
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")

        import preflight
        with patch("preflight.requests") as mock_requests:
            mock_requests.get.side_effect = Exception("timed out")
            result = preflight.check_telegram()

        assert result.ok is False

    def test_token_missing_returns_fail_critical(self, monkeypatch):
        """TELEGRAM_BOT_TOKEN 未設定 → ok=False，critical=True，不發出任何請求。"""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        import preflight
        with patch("preflight.requests") as mock_requests:
            result = preflight.check_telegram()

        assert result.ok is False
        assert result.critical is True
        mock_requests.get.assert_not_called()

    def test_never_raises(self, monkeypatch):
        """check_telegram() 絕不拋例外。"""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
        import preflight
        with patch("preflight.requests") as mock_requests:
            mock_requests.get.side_effect = RuntimeError("network dead")
            try:
                preflight.check_telegram()
            except Exception as e:
                pytest.fail(f"check_telegram() 不應拋例外，但拋出 {e!r}")


# ── (c) check_shioaji ─────────────────────────────────────────────────────────

class TestCheckShioaji:
    """check_shioaji() Shioaji 連線狀態。"""

    def test_keys_unset_returns_ok_simulation(self, monkeypatch):
        """API key 未設定 → ok=True，detail 包含「模擬模式」，non-critical。"""
        monkeypatch.delenv("SHIOAJI_API_KEY", raising=False)
        monkeypatch.delenv("SHIOAJI_SECRET_KEY", raising=False)

        import preflight
        result = preflight.check_shioaji()

        assert result.ok is True
        assert result.critical is False
        assert "模擬" in result.detail

    def test_keys_set_connected_returns_ok(self, monkeypatch):
        """key 設定且 ensure_connected 成功 → ok=True。"""
        monkeypatch.setenv("SHIOAJI_API_KEY", "key-abc")
        monkeypatch.setenv("SHIOAJI_SECRET_KEY", "secret-xyz")
        monkeypatch.setenv("SHIOAJI_SIMULATION", "true")

        fake_api = MagicMock()
        fake_monitor = MagicMock()
        fake_monitor.ensure_connected.return_value = fake_api

        import preflight
        with patch.dict("sys.modules", {"monitor_agent": fake_monitor}):
            result = preflight.check_shioaji()

        assert result.ok is True
        assert result.critical is False

    def test_ensure_connected_returns_none_ok_noncritical(self, monkeypatch):
        """ensure_connected 回傳 None → ok=False，但 critical=False（降級模式）。"""
        monkeypatch.setenv("SHIOAJI_API_KEY", "key-abc")
        monkeypatch.setenv("SHIOAJI_SECRET_KEY", "secret-xyz")

        fake_monitor = MagicMock()
        fake_monitor.ensure_connected.return_value = None

        import preflight
        with patch.dict("sys.modules", {"monitor_agent": fake_monitor}):
            result = preflight.check_shioaji()

        assert result.ok is False
        assert result.critical is False

    def test_monitor_agent_import_fails_noncritical(self, monkeypatch):
        """monitor_agent 模組載入失敗 → ok=False，critical=False。"""
        monkeypatch.setenv("SHIOAJI_API_KEY", "key-abc")
        monkeypatch.setenv("SHIOAJI_SECRET_KEY", "secret-xyz")

        import preflight
        with patch.dict("sys.modules", {"monitor_agent": None}):
            result = preflight.check_shioaji()

        assert result.ok is False
        assert result.critical is False

    def test_never_raises(self, monkeypatch):
        """check_shioaji() 絕不拋例外。"""
        monkeypatch.setenv("SHIOAJI_API_KEY", "key-abc")
        monkeypatch.setenv("SHIOAJI_SECRET_KEY", "secret-xyz")

        import preflight
        with patch.dict("sys.modules", {"monitor_agent": None}):
            try:
                preflight.check_shioaji()
            except Exception as e:
                pytest.fail(f"check_shioaji() 不應拋例外，但拋出 {e!r}")


# ── (d) check_db ──────────────────────────────────────────────────────────────

class TestCheckDb:
    """check_db() 資料庫目錄與 SQLite 檢查。"""

    def test_writable_dir_ok_db(self, tmp_path, monkeypatch):
        """可寫目錄 + SQLite PRAGMA ok → ok=True，critical=False。"""
        db_path = tmp_path / "data" / "research.db"
        monkeypatch.setenv("DB_PATH", str(db_path))

        import preflight
        result = preflight.check_db()

        assert result.ok is True
        assert result.critical is False
        assert "SQLite 正常" in result.detail

    def test_nonexistent_parent_is_created(self, tmp_path, monkeypatch):
        """父目錄不存在時自動建立，仍回傳 ok=True。"""
        db_path = tmp_path / "newdir" / "sub" / "research.db"
        monkeypatch.setenv("DB_PATH", str(db_path))

        import preflight
        result = preflight.check_db()

        assert result.ok is True

    def test_nonwritable_dir_returns_fail_critical(self, tmp_path, monkeypatch):
        """無寫入權限的目錄 → ok=False，critical=True。"""
        data_dir = tmp_path / "readonly"
        data_dir.mkdir()
        data_dir.chmod(0o555)  # 唯讀

        db_path = data_dir / "research.db"
        monkeypatch.setenv("DB_PATH", str(db_path))

        import preflight
        import os
        with patch.object(os, "access", return_value=False):
            result = preflight.check_db()

        assert result.ok is False
        assert result.critical is True

    def test_pragma_corruption_returns_fail(self, tmp_path, monkeypatch):
        """PRAGMA quick_check 回傳非 'ok' → ok=False，critical=True。"""
        import sqlite3 as sqlite3_lib
        db_path = tmp_path / "bad.db"
        monkeypatch.setenv("DB_PATH", str(db_path))

        import preflight

        fake_conn = MagicMock()
        fake_conn.__enter__ = MagicMock(return_value=fake_conn)
        fake_conn.__exit__ = MagicMock(return_value=False)
        fake_conn.execute.return_value.fetchone.return_value = ("corruption found",)
        fake_conn.close = MagicMock()

        with patch("preflight.sqlite3") as mock_sqlite:
            mock_sqlite.connect.return_value = fake_conn
            result = preflight.check_db()

        assert result.ok is False
        assert result.critical is True

    def test_never_raises(self, monkeypatch):
        """check_db() 絕不拋例外。"""
        monkeypatch.setenv("DB_PATH", "/nonexistent/path/db.sqlite")

        import preflight
        import os
        with patch("preflight.sqlite3") as mock_sqlite:
            mock_sqlite.connect.side_effect = RuntimeError("db exploded")
            with patch.object(os.path, "exists", return_value=True), \
                 patch.object(os, "access", return_value=True):
                try:
                    preflight.check_db()
                except Exception as e:
                    pytest.fail(f"check_db() 不應拋例外，但拋出 {e!r}")


# ── (e) check_playbook ────────────────────────────────────────────────────────

class TestCheckPlaybook:
    """check_playbook() 確認 research_playbook.md 存在且標記完整。"""

    def test_file_exists_with_markers_returns_ok(self, tmp_path, monkeypatch):
        """檔案存在且含兩個 ADAPTIVE 標記 → ok=True，non-critical。"""
        playbook = tmp_path / "research_playbook.md"
        playbook.write_text(
            "# 手冊\n<!-- ADAPTIVE-SECTION-START -->\n內容\n<!-- ADAPTIVE-SECTION-END -->",
            encoding="utf-8",
        )

        import preflight
        monkeypatch.setattr(preflight, "_REPO_ROOT", tmp_path)
        result = preflight.check_playbook()

        assert result.ok is True
        assert result.critical is False

    def test_file_missing_returns_fail_noncritical(self, tmp_path, monkeypatch):
        """檔案不存在 → ok=False，critical=False（非嚴重）。"""
        import preflight
        monkeypatch.setattr(preflight, "_REPO_ROOT", tmp_path)
        result = preflight.check_playbook()

        assert result.ok is False
        assert result.critical is False

    def test_missing_adaptive_start_marker(self, tmp_path, monkeypatch):
        """缺少 ADAPTIVE-SECTION-START → ok=False，non-critical。"""
        playbook = tmp_path / "research_playbook.md"
        playbook.write_text("# 手冊\n<!-- ADAPTIVE-SECTION-END -->", encoding="utf-8")

        import preflight
        monkeypatch.setattr(preflight, "_REPO_ROOT", tmp_path)
        result = preflight.check_playbook()

        assert result.ok is False
        assert result.critical is False
        assert "ADAPTIVE-SECTION-START" in result.detail

    def test_missing_adaptive_end_marker(self, tmp_path, monkeypatch):
        """缺少 ADAPTIVE-SECTION-END → ok=False，non-critical。"""
        playbook = tmp_path / "research_playbook.md"
        playbook.write_text("# 手冊\n<!-- ADAPTIVE-SECTION-START -->", encoding="utf-8")

        import preflight
        monkeypatch.setattr(preflight, "_REPO_ROOT", tmp_path)
        result = preflight.check_playbook()

        assert result.ok is False
        assert result.critical is False
        assert "ADAPTIVE-SECTION-END" in result.detail

    def test_never_raises(self, tmp_path, monkeypatch):
        """check_playbook() 絕不拋例外。"""
        import preflight
        monkeypatch.setattr(preflight, "_REPO_ROOT", tmp_path)

        with patch.object(Path, "read_text", side_effect=PermissionError("no read")):
            try:
                preflight.check_playbook()
            except Exception as e:
                pytest.fail(f"check_playbook() 不應拋例外，但拋出 {e!r}")


# ── (f) run_preflight ─────────────────────────────────────────────────────────

class TestRunPreflight:
    """run_preflight() 彙總行為。"""

    def test_returns_list_of_check_results(self, monkeypatch):
        """回傳 CheckResult 清單，長度 >= 1。"""
        import preflight

        # 讓所有個別 check 回傳固定的 ok 結果
        ok_result = preflight.CheckResult(name="test", ok=True, detail="ok", critical=False)
        with patch("preflight.check_env", return_value=ok_result), \
             patch("preflight.check_telegram", return_value=ok_result), \
             patch("preflight.check_shioaji", return_value=ok_result), \
             patch("preflight.check_db", return_value=ok_result), \
             patch("preflight.check_playbook", return_value=ok_result):
            results = preflight.run_preflight(send_telegram=False)

        assert isinstance(results, list)
        assert len(results) == 5

    def test_never_raises_even_when_all_checks_explode(self):
        """所有 check 函式都拋例外時，run_preflight 仍回傳結果清單，不 raise。"""
        import preflight

        with patch("preflight.check_env",       side_effect=RuntimeError("boom")), \
             patch("preflight.check_telegram",   side_effect=RuntimeError("boom")), \
             patch("preflight.check_shioaji",    side_effect=RuntimeError("boom")), \
             patch("preflight.check_db",         side_effect=RuntimeError("boom")), \
             patch("preflight.check_playbook",   side_effect=RuntimeError("boom")):
            try:
                results = preflight.run_preflight(send_telegram=False)
            except Exception as e:
                pytest.fail(f"run_preflight() 不應拋例外，但拋出 {e!r}")

        assert isinstance(results, list)
        assert len(results) == 5
        # 所有項目應被標記為失敗
        assert all(not r.ok for r in results)

    def test_telegram_summary_sent_when_telegram_ok(self, monkeypatch):
        """Telegram check ok 時，應呼叫 send_text 送出摘要。"""
        import preflight

        ok_tg = preflight.CheckResult(name="Telegram", ok=True, detail="ok", critical=False)
        ok_other = preflight.CheckResult(name="other", ok=True, detail="ok", critical=False)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

        with patch("preflight.check_env",      return_value=ok_other), \
             patch("preflight.check_telegram",  return_value=ok_tg), \
             patch("preflight.check_shioaji",   return_value=ok_other), \
             patch("preflight.check_db",        return_value=ok_other), \
             patch("preflight.check_playbook",  return_value=ok_other):

            fake_tgbot = MagicMock()
            with patch.dict("sys.modules", {"telegram_bot": fake_tgbot}):
                preflight.run_preflight(send_telegram=True)

        fake_tgbot.send_text.assert_called_once()
        args = fake_tgbot.send_text.call_args[0]
        assert args[0] == "123456"
        assert "啟動前環境檢查" in args[1]

    def test_telegram_summary_skipped_when_telegram_down(self, monkeypatch):
        """Telegram check 失敗時，不嘗試發送通知。"""
        import preflight

        fail_tg = preflight.CheckResult(name="Telegram", ok=False, detail="down", critical=True)
        ok_other = preflight.CheckResult(name="other", ok=True, detail="ok", critical=False)

        with patch("preflight.check_env",      return_value=ok_other), \
             patch("preflight.check_telegram",  return_value=fail_tg), \
             patch("preflight.check_shioaji",   return_value=ok_other), \
             patch("preflight.check_db",        return_value=ok_other), \
             patch("preflight.check_playbook",  return_value=ok_other):

            fake_tgbot = MagicMock()
            with patch.dict("sys.modules", {"telegram_bot": fake_tgbot}):
                preflight.run_preflight(send_telegram=True)

        fake_tgbot.send_text.assert_not_called()

    def test_send_telegram_false_skips_notification(self, monkeypatch):
        """send_telegram=False 時，即使 Telegram ok 也不發送。"""
        import preflight

        ok_tg = preflight.CheckResult(name="Telegram", ok=True, detail="ok", critical=False)
        ok_other = preflight.CheckResult(name="other", ok=True, detail="ok", critical=False)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

        with patch("preflight.check_env",      return_value=ok_other), \
             patch("preflight.check_telegram",  return_value=ok_tg), \
             patch("preflight.check_shioaji",   return_value=ok_other), \
             patch("preflight.check_db",        return_value=ok_other), \
             patch("preflight.check_playbook",  return_value=ok_other):

            fake_tgbot = MagicMock()
            with patch.dict("sys.modules", {"telegram_bot": fake_tgbot}):
                preflight.run_preflight(send_telegram=False)

        fake_tgbot.send_text.assert_not_called()

    def test_telegram_send_failure_does_not_propagate(self, monkeypatch):
        """send_text 拋例外時，run_preflight 仍回傳結果，不 raise。"""
        import preflight

        ok_tg = preflight.CheckResult(name="Telegram", ok=True, detail="ok", critical=False)
        ok_other = preflight.CheckResult(name="other", ok=True, detail="ok", critical=False)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

        with patch("preflight.check_env",      return_value=ok_other), \
             patch("preflight.check_telegram",  return_value=ok_tg), \
             patch("preflight.check_shioaji",   return_value=ok_other), \
             patch("preflight.check_db",        return_value=ok_other), \
             patch("preflight.check_playbook",  return_value=ok_other):

            fake_tgbot = MagicMock()
            fake_tgbot.send_text.side_effect = RuntimeError("telegram send failed")
            with patch.dict("sys.modules", {"telegram_bot": fake_tgbot}):
                try:
                    results = preflight.run_preflight(send_telegram=True)
                except Exception as e:
                    pytest.fail(f"run_preflight() 不應拋例外，但拋出 {e!r}")

        assert isinstance(results, list)


# ── (g) has_critical_failure ──────────────────────────────────────────────────

class TestHasCriticalFailure:
    """has_critical_failure() 邏輯。"""

    def test_empty_list_returns_false(self):
        """空清單 → False。"""
        import preflight
        assert preflight.has_critical_failure([]) is False

    def test_all_ok_returns_false(self):
        """所有項目 ok=True → False。"""
        import preflight
        results = [
            preflight.CheckResult("a", ok=True,  detail="", critical=True),
            preflight.CheckResult("b", ok=True,  detail="", critical=False),
        ]
        assert preflight.has_critical_failure(results) is False

    def test_noncritical_failure_returns_false(self):
        """critical=False 的失敗不觸發 → False。"""
        import preflight
        results = [
            preflight.CheckResult("a", ok=False, detail="warn", critical=False),
        ]
        assert preflight.has_critical_failure(results) is False

    def test_critical_failure_returns_true(self):
        """critical=True 且 ok=False → True。"""
        import preflight
        results = [
            preflight.CheckResult("a", ok=True,  detail="", critical=False),
            preflight.CheckResult("b", ok=False, detail="err", critical=True),
        ]
        assert preflight.has_critical_failure(results) is True

    def test_multiple_critical_failures_returns_true(self):
        """多個 critical 失敗 → True。"""
        import preflight
        results = [
            preflight.CheckResult("a", ok=False, detail="", critical=True),
            preflight.CheckResult("b", ok=False, detail="", critical=True),
        ]
        assert preflight.has_critical_failure(results) is True


# ── (h) main.py hook：preflight 例外被吞掉 ────────────────────────────────────

class TestMainPreflightHook:
    """main() 啟動時，preflight 任何例外都不應導致排程器崩潰。

    模式與 TestPlaybookUpdateJobErrorHandling 一致：
    patch sys.modules 注入 fake preflight，讓 run_preflight 拋例外，
    確認 main() 不傳播例外。

    注意：main() 內使用 `from notifier import ...` 等 lazy import，
    因此用 patch.dict("sys.modules", ...) 取代 patch("main.xxx")。
    """

    def _fake_notifier(self):
        """建立 notifier stub，讓 from notifier import ... 不拋 ImportError。"""
        mod = MagicMock()
        mod.notify_system_start = MagicMock()
        mod.notify_system_stop = MagicMock()
        mod.notify_market_open = MagicMock()
        mod.notify_market_close = MagicMock()
        return mod

    def test_main_swallows_preflight_exception(self, monkeypatch):
        """preflight.run_preflight() 拋例外 → main() 繼續執行，不重拋。

        由於 main() 包含無限迴圈，我們只測試 main() 在第一次迭代前的
        startup 區段不會因 preflight 失敗而 raise。
        透過讓 _RUNNING = False 立即結束迴圈，避免測試卡住。
        """
        import main as main_mod

        fake_preflight = MagicMock()
        fake_preflight.run_preflight.side_effect = RuntimeError("preflight exploded")

        # 讓排程器迴圈立即結束
        monkeypatch.setattr(main_mod, "_RUNNING", False)

        fake_notifier = self._fake_notifier()
        with patch.dict("sys.modules", {
                "preflight": fake_preflight,
                "notifier": fake_notifier,
            }), \
             patch("main.init_db",              MagicMock()), \
             patch("main.ensure_connected",     MagicMock(return_value=None)), \
             patch("main.load_daytrading_config", MagicMock(return_value=MagicMock(
                 budget_per_stock=0, stop_loss_pct=0, take_profit_pct=0,
                 trailing_start_pct=0, trailing_gap_pct=0,
                 force_close_time="13:25", require_manual_confirm=False,
             ))), \
             patch("main.os.makedirs", MagicMock()):
            try:
                main_mod.main()
            except Exception as e:
                pytest.fail(f"main() 不應因 preflight 例外崩潰，但拋出 {e!r}")

    def test_main_hook_calls_run_preflight(self, monkeypatch):
        """main() 啟動時應呼叫 preflight.run_preflight()。"""
        import main as main_mod

        fake_preflight = MagicMock()
        fake_preflight.run_preflight.return_value = []

        monkeypatch.setattr(main_mod, "_RUNNING", False)

        fake_notifier = self._fake_notifier()
        with patch.dict("sys.modules", {
                "preflight": fake_preflight,
                "notifier": fake_notifier,
            }), \
             patch("main.init_db",              MagicMock()), \
             patch("main.ensure_connected",     MagicMock(return_value=None)), \
             patch("main.load_daytrading_config", MagicMock(return_value=MagicMock(
                 budget_per_stock=0, stop_loss_pct=0, take_profit_pct=0,
                 trailing_start_pct=0, trailing_gap_pct=0,
                 force_close_time="13:25", require_manual_confirm=False,
             ))), \
             patch("main.os.makedirs", MagicMock()):
            main_mod.main()

        fake_preflight.run_preflight.assert_called_once()
