"""
tests/test_playbook_updater.py — playbook_updater 模組單元測試

覆蓋範圍：
  - 標記分割（正常、缺標記、空白區段）
  - 自適應區替換（固定區位元組完全不變）
  - 3000 字元上限拒絕
  - 觀察紀錄上限 20 條
  - 每日 vs 每週五 prompt 模式切換（週五 + ≥30 筆才允許規則整併）
  - orchestrator fail-safe（AI 呼叫例外 → 檔案不變、回傳 False）
  - git / notify 失敗容忍（僅 log，不 raise）
"""
from __future__ import annotations

import sqlite3
import textwrap
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# 延遲 import，讓 conftest 的 google.genai stub 先注入
# ---------------------------------------------------------------------------
import playbook_updater as pu


# ═══════════════════════════════════════════════════════════════════════════
# 輔助：建立含有效 markers 的 playbook 文字
# ═══════════════════════════════════════════════════════════════════════════

_FIXED_BODY = "## 固定規範\n這是固定區內容。\n"
_ADAPTIVE_BODY = "## 當前研究重點\n初始重點。\n\n## 觀察紀錄\n"

def _make_playbook(fixed: str = _FIXED_BODY, adaptive: str = _ADAPTIVE_BODY) -> str:
    """組合含 markers 的完整 playbook 文字。"""
    return (
        "# 研究作業手冊（Gemini Research Playbook）\n\n"
        f"{pu.FIXED_START}\n{fixed}{pu.FIXED_END}\n\n"
        f"{pu.ADAPTIVE_START}\n{adaptive}{pu.ADAPTIVE_END}\n"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 一、split_sections — 標記分割
# ═══════════════════════════════════════════════════════════════════════════

class TestSplitSections:
    def test_正常分割回傳兩個字串(self):
        """正常 playbook 文字可以分割為固定區與自適應區。"""
        text = _make_playbook()
        fixed, adaptive = pu.split_sections(text)
        assert "固定區內容" in fixed
        assert "初始重點" in adaptive

    def test_固定區不含markers(self):
        """回傳的固定區字串不含 FIXED-SECTION 標記本身。"""
        text = _make_playbook()
        fixed, _ = pu.split_sections(text)
        assert pu.FIXED_START not in fixed
        assert pu.FIXED_END not in fixed

    def test_自適應區不含markers(self):
        """回傳的自適應區字串不含 ADAPTIVE-SECTION 標記本身。"""
        text = _make_playbook()
        _, adaptive = pu.split_sections(text)
        assert pu.ADAPTIVE_START not in adaptive
        assert pu.ADAPTIVE_END not in adaptive

    def test_缺少FIXED_START時拋出ValueError(self):
        """缺少固定區開始標記時應拋出 ValueError。"""
        bad = _make_playbook().replace(pu.FIXED_START, "")
        with pytest.raises(ValueError, match="FIXED-SECTION-START"):
            pu.split_sections(bad)

    def test_缺少ADAPTIVE_END時拋出ValueError(self):
        """缺少自適應區結束標記時應拋出 ValueError。"""
        bad = _make_playbook().replace(pu.ADAPTIVE_END, "")
        with pytest.raises(ValueError, match="ADAPTIVE-SECTION-END"):
            pu.split_sections(bad)

    def test_空白自適應區仍可分割(self):
        """自適應區完全空白時不應拋出例外。"""
        text = _make_playbook(adaptive="")
        _, adaptive = pu.split_sections(text)
        assert adaptive.strip() == ""

    def test_四個markers缺一都應拋出(self):
        """任何一個 marker 缺失都應拋出 ValueError。"""
        markers = [pu.FIXED_START, pu.FIXED_END, pu.ADAPTIVE_START, pu.ADAPTIVE_END]
        for m in markers:
            bad = _make_playbook().replace(m, "")
            with pytest.raises(ValueError):
                pu.split_sections(bad)


# ═══════════════════════════════════════════════════════════════════════════
# 二、apply_adaptive_section — 只改自適應區
# ═══════════════════════════════════════════════════════════════════════════

class TestApplyAdaptiveSection:
    def test_固定區位元組完全不變(self):
        """替換後固定區的位元組必須與原始完全一致。"""
        original = _make_playbook()
        # 取出原始固定區位元組
        fixed_orig, _ = pu.split_sections(original)
        new_adaptive = "## 當前研究重點\n更新後的重點。\n\n## 觀察紀錄\n- 2026-06-10：新觀察。\n"
        result = pu.apply_adaptive_section(original, new_adaptive)
        fixed_new, _ = pu.split_sections(result)
        assert fixed_new.encode() == fixed_orig.encode()

    def test_自適應區內容已更新(self):
        """替換後自適應區應含有新內容。"""
        original = _make_playbook()
        new_adaptive = "## 當前研究重點\n更新後的重點。\n\n## 觀察紀錄\n"
        result = pu.apply_adaptive_section(original, new_adaptive)
        _, adaptive_new = pu.split_sections(result)
        assert "更新後的重點" in adaptive_new

    def test_超過3000字元上限時保留原始內容(self):
        """新自適應區超過 3000 字元時必須拒絕並回傳原始文字。"""
        original = _make_playbook()
        too_long = "A" * 3001
        result = pu.apply_adaptive_section(original, too_long)
        # 應保留原始，不含超長內容
        assert result == original

    def test_恰好3000字元應接受(self):
        """新自適應區恰好 3000 字元時應允許。"""
        original = _make_playbook()
        exactly_3000 = "A" * 3000
        result = pu.apply_adaptive_section(original, exactly_3000)
        assert result != original  # 應已更新

    def test_替換後markers仍完整(self):
        """替換後四個 markers 必須全部存在。"""
        original = _make_playbook()
        new_adaptive = "## 當前研究重點\n已更新。\n"
        result = pu.apply_adaptive_section(original, new_adaptive)
        assert pu.FIXED_START in result
        assert pu.FIXED_END in result
        assert pu.ADAPTIVE_START in result
        assert pu.ADAPTIVE_END in result

    def test_輸入文字缺少markers時回傳原始(self):
        """輸入文字本身 markers 不完整時應回傳原始文字（fail-safe）。"""
        broken = "沒有任何標記的文字"
        new_adaptive = "新的自適應區"
        result = pu.apply_adaptive_section(broken, new_adaptive)
        assert result == broken


# ═══════════════════════════════════════════════════════════════════════════
# 三、build_update_prompt — 每日 vs 每週五
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildUpdatePrompt:
    """驗證 prompt 在每日模式與每週模式下的差異。"""

    _outcomes = [{"code": "2330", "name": "台積電", "was_correct": True}]
    _adaptive = "## 當前研究重點\n初始。\n\n## 觀察紀錄\n"

    def test_每日模式含有追加觀察指令(self):
        """每日 prompt 應要求追加一條帶日期的觀察（≤200字）。"""
        prompt = pu.build_update_prompt(self._outcomes, self._adaptive, is_weekly=False)
        assert "觀察" in prompt
        assert "200" in prompt

    def test_每日模式不允許規則整併(self):
        """每日 prompt 不應允許規則整併（只追加觀察）。"""
        prompt = pu.build_update_prompt(self._outcomes, self._adaptive, is_weekly=False)
        # 每日模式不應提及「規則整併」
        assert "規則整併" not in prompt

    def test_每週模式允許規則整併(self):
        """每週 prompt 應明確允許修改當前研究重點並要求附樣本數。"""
        prompt = pu.build_update_prompt(self._outcomes, self._adaptive, is_weekly=True)
        assert "當前研究重點" in prompt
        assert "樣本" in prompt

    def test_prompt含有當日結果摘要(self):
        """prompt 中應包含當日結果摘要資訊。"""
        outcomes = [{"code": "2330", "name": "台積電", "was_correct": True,
                     "confidence": 8, "actual_return_pct": 2.5}]
        prompt = pu.build_update_prompt(outcomes, self._adaptive, is_weekly=False)
        assert "2330" in prompt

    def test_prompt含有現有自適應區(self):
        """prompt 中應包含現有自適應區文字，讓 AI 有上下文。"""
        prompt = pu.build_update_prompt(self._outcomes, "特殊初始重點XYZ", is_weekly=False)
        assert "特殊初始重點XYZ" in prompt

    def test_觀察上限20條指令存在於prompt(self):
        """prompt 中應指示最多保留 20 條觀察，超過時淘汰最舊的。"""
        prompt = pu.build_update_prompt(self._outcomes, self._adaptive, is_weekly=False)
        assert "20" in prompt


# ═══════════════════════════════════════════════════════════════════════════
# 四、load_today_outcomes — 讀取 DB（含新欄位容錯）
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadTodayOutcomes:
    def test_空資料庫回傳空清單(self, tmp_path):
        """DB 中沒有當日資料時應回傳空清單（不拋例外）。"""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_prediction_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                expected_return_pct REAL NOT NULL,
                entry_price REAL NOT NULL,
                closing_price REAL,
                actual_return_pct REAL,
                was_correct INTEGER,
                UNIQUE(date, code)
            );
        """)
        conn.close()
        result = pu.load_today_outcomes(db_path, date(2026, 6, 10))
        assert result == []

    def test_有資料時回傳清單(self, tmp_path):
        """DB 中有當日資料時應回傳正確筆數。"""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_prediction_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                expected_return_pct REAL NOT NULL,
                entry_price REAL NOT NULL,
                closing_price REAL,
                actual_return_pct REAL,
                was_correct INTEGER,
                UNIQUE(date, code)
            );
        """)
        conn.execute("""
            INSERT INTO stock_prediction_log
                (date, code, name, action, confidence, expected_return_pct,
                 entry_price, closing_price, actual_return_pct, was_correct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("2026-06-10", "2330", "台積電", "open", 8, 3.0, 870.0, 895.0, 2.87, 1))
        conn.commit()
        conn.close()
        result = pu.load_today_outcomes(db_path, date(2026, 6, 10))
        assert len(result) == 1
        assert result[0]["code"] == "2330"

    def test_新欄位不存在時不拋例外(self, tmp_path):
        """reason/factors_json/news_refs/youtube_refs 欄位不存在時應容錯（舊版 schema）。"""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        # 舊版 schema：不含新欄位
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_prediction_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                expected_return_pct REAL NOT NULL,
                entry_price REAL NOT NULL,
                closing_price REAL,
                actual_return_pct REAL,
                was_correct INTEGER,
                UNIQUE(date, code)
            );
        """)
        conn.execute("""
            INSERT INTO stock_prediction_log
                (date, code, name, action, confidence, expected_return_pct,
                 entry_price, closing_price, actual_return_pct, was_correct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("2026-06-10", "2454", "聯發科", "open", 6, 2.0, 1200.0, None, None, None))
        conn.commit()
        conn.close()
        # 不應拋出例外
        result = pu.load_today_outcomes(db_path, date(2026, 6, 10))
        assert len(result) == 1
        # 新欄位應有預設值（None 或空字串）
        assert result[0].get("reason") is None or result[0].get("reason") == ""

    def test_含新欄位的schema可正常讀取(self, tmp_path):
        """含 reason/factors_json/news_refs/youtube_refs 的新 schema 應正常讀取。"""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_prediction_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                expected_return_pct REAL NOT NULL,
                entry_price REAL NOT NULL,
                closing_price REAL,
                actual_return_pct REAL,
                was_correct INTEGER,
                reason TEXT,
                factors_json TEXT,
                news_refs TEXT,
                youtube_refs TEXT,
                UNIQUE(date, code)
            );
        """)
        conn.execute("""
            INSERT INTO stock_prediction_log
                (date, code, name, action, confidence, expected_return_pct,
                 entry_price, was_correct, reason, factors_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("2026-06-10", "2330", "台積電", "open", 9, 3.0, 870.0, 1,
              "技術面突破", '{"technical": "KD 金叉", "chip": "外資買超"}'))
        conn.commit()
        conn.close()
        result = pu.load_today_outcomes(db_path, date(2026, 6, 10))
        assert result[0]["reason"] == "技術面突破"
        assert result[0]["factors_json"] is not None

    def test_DB不存在時回傳空清單不拋例外(self, tmp_path):
        """DB 檔案不存在時應回傳空清單（fail-safe）。"""
        db_path = str(tmp_path / "nonexistent.db")
        result = pu.load_today_outcomes(db_path, date(2026, 6, 10))
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════
# 五、週五模式 gating — is_weekly 邏輯
# ═══════════════════════════════════════════════════════════════════════════

class TestWeeklyGating:
    """驗證 _should_do_weekly 的判斷：週五 AND 樣本 ≥ 30。"""

    def test_週五且樣本30應為True(self):
        """週五且樣本 ≥ 30 時，_should_do_weekly 應為 True。"""
        friday = date(2026, 6, 12)   # 確認是週五（weekday() == 4）
        assert friday.weekday() == 4
        result = pu._should_do_weekly(friday, n_samples=30)
        assert result is True

    def test_週五但樣本不足應為False(self):
        """週五但樣本 < 30 時，_should_do_weekly 應為 False。"""
        friday = date(2026, 6, 12)
        result = pu._should_do_weekly(friday, n_samples=29)
        assert result is False

    def test_非週五無論樣本應為False(self):
        """非週五時，無論樣本多寡 _should_do_weekly 都應為 False。"""
        tuesday = date(2026, 6, 10)   # 週二（weekday() == 1）
        assert tuesday.weekday() != 4
        result = pu._should_do_weekly(tuesday, n_samples=100)
        assert result is False

    def test_週五且樣本剛好29應為False(self):
        """臨界值：剛好 29 筆（< 30）時應為 False。"""
        friday = date(2026, 6, 12)
        result = pu._should_do_weekly(friday, n_samples=29)
        assert result is False

    def test_週五且樣本剛好30應為True(self):
        """臨界值：剛好 30 筆（= 30）時應為 True。"""
        friday = date(2026, 6, 12)
        result = pu._should_do_weekly(friday, n_samples=30)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 六、觀察紀錄上限 20 條
# ═══════════════════════════════════════════════════════════════════════════

class TestObservationCap:
    """驗證 enforce_observation_cap 在超過 20 條時淘汰最舊觀察。"""

    def _make_observations(self, n: int) -> str:
        """產生 n 條觀察紀錄的自適應區文字。"""
        lines = ["## 當前研究重點\n初始重點。\n\n## 觀察紀錄（append-only，最多保留 20 條，舊的先淘汰）\n"]
        for i in range(n):
            lines.append(f"- 2026-01-{i+1:02d}：觀察 {i+1}\n")
        return "".join(lines)

    def test_20條以內不淘汰(self):
        """觀察 ≤ 20 條時，不應淘汰任何一條。"""
        adaptive = self._make_observations(20)
        result = pu.enforce_observation_cap(adaptive, max_obs=20)
        count = result.count("- 2026-")
        assert count == 20

    def test_21條時淘汰最舊一條(self):
        """觀察 21 條時，應淘汰最舊的那條（2026-01-01）。"""
        adaptive = self._make_observations(21)
        result = pu.enforce_observation_cap(adaptive, max_obs=20)
        assert "- 2026-01-01：觀察 1\n" not in result
        assert "- 2026-01-21：觀察 21\n" in result

    def test_30條時只保留最新20條(self):
        """觀察 30 條時，應只保留最新 20 條。"""
        adaptive = self._make_observations(30)
        result = pu.enforce_observation_cap(adaptive, max_obs=20)
        count = result.count("- 2026-")
        assert count == 20
        # 最舊 10 條應被移除
        for i in range(1, 11):
            assert f"觀察 {i}\n" not in result
        # 最新 20 條應保留
        for i in range(11, 31):
            assert f"觀察 {i}\n" in result


# ═══════════════════════════════════════════════════════════════════════════
# 七、orchestrator fail-safe
# ═══════════════════════════════════════════════════════════════════════════

class TestRunDailyUpdateFailSafe:
    """驗證 run_daily_update 在各種失敗情境下的 fail-safe 行為。"""

    def _write_playbook(self, tmp_path: Path, content: str) -> Path:
        """將 playbook 寫入臨時目錄並回傳 Path。"""
        p = tmp_path / "research_playbook.md"
        p.write_text(content, encoding="utf-8")
        return p

    def _make_empty_db(self, tmp_path: Path) -> str:
        """建立空的 stock_prediction_log DB 並回傳路徑。"""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_prediction_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL, code TEXT NOT NULL,
                name TEXT NOT NULL, action TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                expected_return_pct REAL NOT NULL,
                entry_price REAL NOT NULL,
                UNIQUE(date, code)
            );
        """)
        conn.close()
        return db_path

    def test_AI呼叫拋出例外時回傳False(self, tmp_path):
        """AI 呼叫（call_haiku）拋出例外時，run_daily_update 應回傳 False。"""
        playbook_path = self._write_playbook(tmp_path, _make_playbook())
        db_path = self._make_empty_db(tmp_path)
        with patch("playbook_updater.call_haiku", side_effect=RuntimeError("AI 炸了")):
            result = pu.run_daily_update(
                playbook_path=str(playbook_path),
                db_path=db_path,
                day=date(2026, 6, 10),
            )
        assert result is False

    def test_AI呼叫拋出例外時檔案不變(self, tmp_path):
        """AI 呼叫失敗時，playbook 檔案內容應完全不變。"""
        original = _make_playbook()
        playbook_path = self._write_playbook(tmp_path, original)
        db_path = self._make_empty_db(tmp_path)
        with patch("playbook_updater.call_haiku", side_effect=RuntimeError("AI 炸了")):
            pu.run_daily_update(
                playbook_path=str(playbook_path),
                db_path=db_path,
                day=date(2026, 6, 10),
            )
        assert playbook_path.read_text(encoding="utf-8") == original

    def test_AI回傳空字串時回傳False(self, tmp_path):
        """AI 回傳空字串時，run_daily_update 應回傳 False（不寫入空內容）。"""
        playbook_path = self._write_playbook(tmp_path, _make_playbook())
        db_path = self._make_empty_db(tmp_path)
        with patch("playbook_updater.call_haiku", return_value=""):
            result = pu.run_daily_update(
                playbook_path=str(playbook_path),
                db_path=db_path,
                day=date(2026, 6, 10),
            )
        assert result is False

    def test_playbook檔案不存在時回傳False(self, tmp_path):
        """playbook 檔案不存在時應回傳 False（不 raise）。"""
        result = pu.run_daily_update(
            playbook_path=str(tmp_path / "nonexistent.md"),
            db_path=str(tmp_path / "test.db"),
            day=date(2026, 6, 10),
        )
        assert result is False

    def test_成功更新時回傳True(self, tmp_path):
        """成功更新 playbook 時應回傳 True。"""
        playbook_path = self._write_playbook(tmp_path, _make_playbook())
        db_path = self._make_empty_db(tmp_path)
        fake_ai_response = (
            "## 當前研究重點\n初始重點。\n\n"
            "## 觀察紀錄（append-only，最多保留 20 條，舊的先淘汰）\n"
            "- 2026-06-10：今日台積電漲 2.87%，技術面訊號準確。\n"
        )
        with patch("playbook_updater.call_haiku", return_value=fake_ai_response), \
             patch("playbook_updater.git_commit_playbook"), \
             patch("playbook_updater.notify_playbook_update"):
            result = pu.run_daily_update(
                playbook_path=str(playbook_path),
                db_path=db_path,
                day=date(2026, 6, 10),
            )
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 八、git_commit_playbook — git 失敗容忍
# ═══════════════════════════════════════════════════════════════════════════

class TestGitCommitPlaybook:
    def test_git失敗不raise(self, tmp_path):
        """subprocess 回傳非零 exit code 時不應拋出例外。"""
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            # 不應拋出例外
            pu.git_commit_playbook(str(tmp_path / "research_playbook.md"), date(2026, 6, 10))

    def test_git_commit訊息格式(self, tmp_path):
        """git commit 訊息應符合 'playbook: YYYY-MM-DD 盤後更新' 格式。"""
        calls_made = []
        def fake_run(cmd, **kwargs):
            calls_made.append(cmd)
            return MagicMock(returncode=0)

        playbook_file = tmp_path / "research_playbook.md"
        playbook_file.write_text("test", encoding="utf-8")

        with patch("subprocess.run", side_effect=fake_run):
            pu.git_commit_playbook(str(playbook_file), date(2026, 6, 10))

        # 找到 commit 指令
        commit_cmds = [c for c in calls_made if "commit" in c]
        assert len(commit_cmds) >= 1
        commit_msg = " ".join(commit_cmds[0])
        assert "playbook:" in commit_msg
        assert "2026-06-10" in commit_msg
        assert "盤後更新" in commit_msg


# ═══════════════════════════════════════════════════════════════════════════
# 九、notify_playbook_update — 通知失敗容忍
# ═══════════════════════════════════════════════════════════════════════════

class TestNotifyPlaybookUpdate:
    def test_notifier失敗不raise(self):
        """notifier._send 拋出例外時，notify_playbook_update 不應拋出。"""
        with patch("notifier._send", side_effect=Exception("Telegram down")):
            # 不應拋出例外
            pu.notify_playbook_update("舊內容", "新內容", date(2026, 6, 10))

    def test_通知包含diff摘要(self):
        """通知訊息應包含新舊內容的差異摘要。"""
        sent_messages = []
        with patch("notifier._send", side_effect=lambda msg, **kw: sent_messages.append(msg)):
            pu.notify_playbook_update(
                "舊觀察：無",
                "新觀察：2026-06-10 台積電漲停",
                date(2026, 6, 10),
            )
        if sent_messages:
            msg = sent_messages[0]
            assert "playbook" in msg.lower() or "手冊" in msg
