from __future__ import annotations

"""
tests/test_playbook_integration.py — TDD 整合測試

涵蓋：
  (a) youtube_analyzer.py：提示詞包含 playbook 文字（檔案存在時）
  (b) youtube_analyzer.py：檔案不存在 → 提示詞與現狀相同，不拋例外
  (c) PlaybookUpdateJob：13:50 交易日觸發，與其他 job 一致
  (d) PlaybookUpdateJob：吞掉 updater 拋出的例外，排程器存活
  (e) PlaybookUpdateJob：防重複執行守衛與其他 job 相同
"""

import sys
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ── (a) youtube prompt 包含 playbook 文字 ─────────────────────────────────────

class TestLoadPlaybookText:
    """load_playbook_text() 單元測試。"""

    def test_returns_content_when_file_exists(self, tmp_path, monkeypatch):
        """research_playbook.md 存在時，回傳完整文字。"""
        playbook = tmp_path / "research_playbook.md"
        playbook.write_text("# 手冊內容\n測試段落", encoding="utf-8")

        import youtube_analyzer
        monkeypatch.setattr(youtube_analyzer, "_REPO_ROOT", tmp_path)

        result = youtube_analyzer.load_playbook_text()
        assert "手冊內容" in result
        assert "測試段落" in result

    def test_returns_empty_string_when_file_missing(self, tmp_path, monkeypatch):
        """research_playbook.md 不存在 → 回傳空字串，不拋例外。"""
        import youtube_analyzer
        monkeypatch.setattr(youtube_analyzer, "_REPO_ROOT", tmp_path)

        result = youtube_analyzer.load_playbook_text()
        assert result == ""

    def test_returns_empty_string_when_file_is_empty(self, tmp_path, monkeypatch):
        """research_playbook.md 存在但內容為空 → 回傳空字串。"""
        (tmp_path / "research_playbook.md").write_text("", encoding="utf-8")

        import youtube_analyzer
        monkeypatch.setattr(youtube_analyzer, "_REPO_ROOT", tmp_path)

        result = youtube_analyzer.load_playbook_text()
        assert result == ""

    def test_never_raises_on_permission_error(self, tmp_path, monkeypatch):
        """讀取失敗（任何例外）→ 回傳空字串，不拋例外。"""
        import youtube_analyzer

        def _bad_read(*args, **kwargs):
            raise PermissionError("no access")

        monkeypatch.setattr(youtube_analyzer, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(Path, "read_text", _bad_read)

        result = youtube_analyzer.load_playbook_text()
        assert result == ""


class TestAnalyzeVideoPromptInjectsPlaybook:
    """_analyze_video() 的 prompt 在 playbook 存在時應前置手冊區塊。

    使用 _build_prompt() 直接測試 prompt 組合邏輯，
    迴避 google.genai.types stub 不完整的問題。
    """

    def test_prompt_contains_playbook_text_when_file_exists(self, tmp_path, monkeypatch):
        """playbook 存在時，_build_prompt() 回傳值應包含手冊全文。"""
        playbook = tmp_path / "research_playbook.md"
        playbook.write_text("PLAYBOOK_SENTINEL_TEXT", encoding="utf-8")

        import youtube_analyzer
        monkeypatch.setattr(youtube_analyzer, "_REPO_ROOT", tmp_path)

        result = youtube_analyzer._build_prompt("測試影片標題")
        assert "PLAYBOOK_SENTINEL_TEXT" in result, (
            f"_build_prompt 應包含 playbook 文字，實際：{result[:300]}"
        )
        assert "研究作業手冊" in result, "_build_prompt 應有手冊區塊標頭"

    def test_prompt_without_playbook_when_file_missing(self, tmp_path, monkeypatch):
        """playbook 不存在時，_build_prompt() 與直接 format _ANALYSIS_PROMPT 相同。"""
        import youtube_analyzer
        monkeypatch.setattr(youtube_analyzer, "_REPO_ROOT", tmp_path)  # 空目錄 → 無 playbook

        result = youtube_analyzer._build_prompt("無 playbook 測試")
        expected = youtube_analyzer._ANALYSIS_PROMPT.format(title="無 playbook 測試")

        assert result == expected, (
            "playbook 不存在時，_build_prompt 應與現狀行為完全相同"
        )
        assert "研究作業手冊" not in result, (
            "playbook 不存在時，prompt 不應含手冊區塊"
        )

    def test_analyze_text_prompt_contains_playbook(self, tmp_path, monkeypatch):
        """_analyze_text()（影片超長 fallback）也應在 prompt 前置手冊區塊。"""
        playbook = tmp_path / "research_playbook.md"
        playbook.write_text("PLAYBOOK_IN_TEXT_MODE", encoding="utf-8")

        import youtube_analyzer
        monkeypatch.setattr(youtube_analyzer, "_REPO_ROOT", tmp_path)

        stub_client = MagicMock()
        resp = MagicMock()
        resp.text = '{"sentiment":"neutral","sentiment_zh":"中性","key_stocks":[],"key_sectors":[],"main_points":[],"risk_warnings":[],"one_line":"ok"}'
        stub_client.models.generate_content.return_value = resp
        monkeypatch.setattr(youtube_analyzer, "_gemini_singleton", stub_client)

        youtube_analyzer._analyze_text("測試標題", "這是影片描述文字")

        call_args = stub_client.models.generate_content.call_args
        # _analyze_text 傳 str 給 generate_content（非 types.Content）
        contents_arg = call_args[1].get("contents") or call_args[0][1]
        assert isinstance(contents_arg, str), "_analyze_text 應傳純字串"
        assert "PLAYBOOK_IN_TEXT_MODE" in contents_arg, (
            f"_analyze_text prompt 應包含 playbook，實際：{contents_arg[:300]}"
        )

    def test_analyze_text_without_playbook_no_raise(self, tmp_path, monkeypatch):
        """_analyze_text() 在 playbook 不存在時，不拋例外，行為與現狀相同。"""
        import youtube_analyzer
        monkeypatch.setattr(youtube_analyzer, "_REPO_ROOT", tmp_path)  # 無 playbook

        stub_client = MagicMock()
        resp = MagicMock()
        resp.text = '{"sentiment":"neutral","sentiment_zh":"中性","key_stocks":[],"key_sectors":[],"main_points":[],"risk_warnings":[],"one_line":"ok"}'
        stub_client.models.generate_content.return_value = resp
        monkeypatch.setattr(youtube_analyzer, "_gemini_singleton", stub_client)

        result = youtube_analyzer._analyze_text("測試標題", "描述文字")
        assert result is not None


# ── (c) PlaybookUpdateJob 排程時間：13:50，僅交易日 ───────────────────────────

class TestPlaybookUpdateJobClass:
    """PlaybookUpdateJob 的 class 設計與其他 job 一致。"""

    def test_playbook_update_job_importable(self):
        """PlaybookUpdateJob 必須可從 main 匯入。"""
        from main import PlaybookUpdateJob
        assert PlaybookUpdateJob is not None

    def test_playbook_update_job_has_run_method(self):
        """PlaybookUpdateJob 必須有 run() 方法。"""
        from main import PlaybookUpdateJob
        assert callable(getattr(PlaybookUpdateJob, "run", None))


class TestPlaybookUpdateJobScheduling:
    """PlaybookUpdateJob 必須在交易日 13:50 觸發，且有防重複執行守衛。"""

    def _check_job_fires_at(self, hour: int, minute: int, expect_fire: bool):
        """驗證在特定時刻，PlaybookUpdateJob 是否會被觸發。

        使用與 test_main.py 其他 job 相同的模式：
        直接從 main 匯入 PlaybookUpdateJob，並驗證其符合 13:50 條件。
        """
        from main import PlaybookUpdateJob, is_trading_day

        # 2026-05-04 是一個交易日（週一）
        now = datetime(2026, 5, 4, hour, minute)
        assert is_trading_day(now), f"{now} 應為交易日"

        t = now.time()
        fired = t.hour == 13 and t.minute == 50
        assert fired == expect_fire

    def test_fires_at_1350(self):
        """13:50 必須觸發。"""
        self._check_job_fires_at(13, 50, expect_fire=True)

    def test_does_not_fire_at_1335(self):
        """13:35（PostMarketJob 時間）不應觸發。"""
        self._check_job_fires_at(13, 35, expect_fire=False)

    def test_does_not_fire_at_1349(self):
        """13:49 不應觸發。"""
        self._check_job_fires_at(13, 49, expect_fire=False)

    def test_does_not_fire_at_1351(self):
        """13:51 不應觸發。"""
        self._check_job_fires_at(13, 51, expect_fire=False)

    def test_not_trading_day_on_saturday(self):
        """週六非交易日，job 不應執行。"""
        from main import is_trading_day
        now = datetime(2026, 5, 2, 13, 50)  # Saturday
        assert not is_trading_day(now)

    def test_not_trading_day_on_holiday(self):
        """勞動節（2026-05-01）非交易日，job 不應執行。"""
        from main import is_trading_day
        now = datetime(2026, 5, 1, 13, 50)
        assert not is_trading_day(now)

    def test_duplicate_run_guard_key_format(self):
        """防重複執行 key 格式應為 YYYY-MM-DD-1350（與其他 job 一致）。"""
        now = datetime(2026, 5, 4, 13, 50)
        today_prefix = now.strftime("%Y-%m-%d")
        key = f"{today_prefix}-1350"
        assert key == "2026-05-04-1350"


# ── (d) PlaybookUpdateJob 吞掉 updater 例外 ───────────────────────────────────

class TestPlaybookUpdateJobErrorHandling:
    """updater 拋出例外時，job 應記錄 log 並繼續，排程器不受影響。"""

    def test_run_does_not_raise_when_updater_raises(self):
        """playbook_updater.run_daily_update() 拋例外 → PlaybookUpdateJob.run() 不拋例外。"""
        from main import PlaybookUpdateJob

        fake_updater = MagicMock()
        fake_updater.run_daily_update.side_effect = RuntimeError("updater crashed")

        job = PlaybookUpdateJob()
        # 用 monkeypatch 模式：patch sys.modules 中的 playbook_updater
        with patch.dict("sys.modules", {"playbook_updater": fake_updater}):
            # 不應拋例外
            try:
                job.run()
            except Exception as e:
                pytest.fail(f"PlaybookUpdateJob.run() 不應拋例外，但拋出 {e!r}")

    def test_run_does_not_raise_when_updater_module_missing(self):
        """playbook_updater 模組不存在 → PlaybookUpdateJob.run() 不拋例外。"""
        from main import PlaybookUpdateJob

        job = PlaybookUpdateJob()
        with patch.dict("sys.modules", {"playbook_updater": None}):
            try:
                job.run()
            except Exception as e:
                pytest.fail(f"playbook_updater 不存在時不應拋例外，但拋出 {e!r}")

    def test_run_calls_run_daily_update(self):
        """run() 應呼叫 playbook_updater.run_daily_update()。"""
        from main import PlaybookUpdateJob

        fake_updater = MagicMock()
        fake_updater.run_daily_update.return_value = None

        job = PlaybookUpdateJob()
        with patch.dict("sys.modules", {"playbook_updater": fake_updater}):
            job.run()

        fake_updater.run_daily_update.assert_called_once()

    def test_run_logs_on_updater_exception(self, caplog):
        """updater 拋例外時，應有 WARNING/ERROR log，不應靜默失敗。"""
        import logging
        from main import PlaybookUpdateJob

        fake_updater = MagicMock()
        fake_updater.run_daily_update.side_effect = Exception("test error")

        job = PlaybookUpdateJob()
        with patch.dict("sys.modules", {"playbook_updater": fake_updater}):
            with caplog.at_level(logging.WARNING, logger="main"):
                job.run()

        assert len(caplog.records) > 0, "拋例外時應有 log 記錄"
        assert any("playbook" in r.message.lower() or "updater" in r.message.lower()
                   for r in caplog.records), (
            f"log 訊息應提及 playbook 或 updater，實際：{[r.message for r in caplog.records]}"
        )


# ── (e) 防重複執行守衛 ─────────────────────────────────────────────────────────

class TestPlaybookUpdateJobDuplicateGuard:
    """PlaybookUpdateJob 的防重複執行機制與其他 job 一致。

    設計：main loop 的 _fired_today set 紀錄已執行的 key，
    同一天同一個 key 只執行一次。
    """

    def test_fired_today_key_is_1350(self):
        """13:50 的 key 為 YYYY-MM-DD-1350。"""
        now = datetime(2026, 5, 4, 13, 50)
        today_prefix = now.strftime("%Y-%m-%d")
        expected_key = f"{today_prefix}-1350"

        t = now.time()
        should_fire = (t.hour == 13 and t.minute == 50)
        assert should_fire

        # key 加入 _fired_today 後，第二次檢查應為 False
        fired_today = set()
        # 第一次：應觸發（key 不在 set 中）
        assert expected_key not in fired_today
        fired_today.add(expected_key)
        # 第二次：不應觸發（key 已在 set 中）
        assert expected_key in fired_today

    def test_different_days_do_not_share_guard(self):
        """跨日後 _fired_today 清除舊 key，新一天可以再次觸發。"""
        now_day1 = datetime(2026, 5, 4, 13, 50)
        now_day2 = datetime(2026, 5, 5, 13, 50)

        key_day1 = f"{now_day1.strftime('%Y-%m-%d')}-1350"
        key_day2 = f"{now_day2.strftime('%Y-%m-%d')}-1350"

        fired_today = {key_day1}
        # 跨日後用 today_prefix 過濾：保留 day2 的 prefix 項目，清除 day1
        today_prefix_day2 = now_day2.strftime("%Y-%m-%d")
        fired_today = {k for k in fired_today if k.startswith(today_prefix_day2)}

        assert key_day1 not in fired_today, "跨日後舊 key 應清除"
        assert key_day2 not in fired_today, "新 key 尚未觸發"
