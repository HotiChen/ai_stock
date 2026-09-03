"""
tests/test_playbook_dates.py — Playbook 觀察紀錄不得由 LLM 自己編日期

2026-09-03 正式環境實例（Telegram 推播）：

    研究手冊（Playbook）盤後更新
    日期：2026-09-03
    新增觀察：
    - 2024-12-30：今日盤後無預測資料產出…年底最後一週市場參與度進一步下降，
      成交量處於極低水位…預期 2025 年 1 月 2 日開盤恢復正常交易後…

標頭是 2026-09-03，內文卻是 2024-12-30，而且整段市場描述都是編的。

兩個成因：
  1. prompt 要求「格式：- YYYY-MM-DD：觀察內容」，卻**從來沒告訴模型今天
     是幾號**（build_update_prompt 沒有 day 參數）。模型只能猜。
  2. 今日無預測資料時仍然呼叫 LLM 要它「根據今日交易結果」寫觀察——沒有
     素材，它就編。

修法：日期明確傳入並驗證；無資料時不呼叫 LLM，直接寫一條確定為真的紀錄。
"""
from datetime import date

import pytest

import playbook_updater as pu

DAY = date(2026, 9, 3)

_ADAPTIVE = """## 當前研究重點
- 規則 A

## 觀察紀錄
- 2026-09-01：舊觀察
"""


class TestPromptCarriesDate:
    def test_prompt_states_today(self):
        """★ 要求模型寫日期，就必須告訴它日期。"""
        p = pu.build_update_prompt([], _ADAPTIVE, day=DAY)
        assert "2026-09-03" in p

    def test_prompt_forbids_inventing_dates(self):
        p = pu.build_update_prompt([], _ADAPTIVE, day=DAY)
        assert "2026-09-03" in p
        # 明確要求用給定日期，而不是「YYYY-MM-DD」這種讓模型自由發揮的佔位符
        assert "YYYY-MM-DD" not in p


class TestNoDataPath:
    def test_no_outcomes_skips_llm(self, monkeypatch):
        """★ 沒有素材就不要問 LLM——它會編。

        正式環境那段「年底最後一週市場參與度下降、融資水位控制穩定」
        全是模型憑空生成的，當天根本沒有任何資料。
        """
        called = []
        monkeypatch.setattr(pu, "call_haiku",
                            lambda *a, **k: called.append(1) or "x")
        text = pu.build_no_data_observation(DAY)
        assert not called, "無資料時不得呼叫 LLM——它會編"
        assert "2026-09-03" in text

    def test_no_data_observation_states_only_facts(self):
        text = pu.build_no_data_observation(DAY)
        assert "無預測資料" in text
        # 不得出現任何未經證實的市場描述
        for invented in ("成交量", "融資", "外資", "市場參與"):
            assert invented not in text


class TestOutputValidation:
    def test_wrong_date_is_corrected(self):
        """★ 模型仍寫錯日期時，以系統日期為準改寫。"""
        bad = "## 觀察紀錄\n- 2024-12-30：某某觀察\n"
        fixed = pu.enforce_observation_date(bad, DAY)
        assert "2026-09-03：某某觀察" in fixed
        assert "2024-12-30" not in fixed

    def test_correct_date_untouched(self):
        good = "## 觀察紀錄\n- 2026-09-03：某某觀察\n"
        assert pu.enforce_observation_date(good, DAY) == good

    def test_only_the_newest_observation_is_touched(self):
        """★ 只修最後一條（今天新增的）——舊觀察的日期是歷史事實，不能改。"""
        text = ("## 觀察紀錄\n"
                "- 2026-08-01：舊的一\n"
                "- 2026-08-15：舊的二\n"
                "- 2024-12-30：今天新增但日期錯\n")
        fixed = pu.enforce_observation_date(text, DAY)
        assert "- 2026-08-01：舊的一" in fixed
        assert "- 2026-08-15：舊的二" in fixed
        assert "- 2026-09-03：今天新增但日期錯" in fixed

    def test_no_observation_lines_is_noop(self):
        text = "## 當前研究重點\n- 規則 A\n"
        assert pu.enforce_observation_date(text, DAY) == text

    def test_future_date_also_corrected(self):
        """模型寫未來日期一樣要修——觀察紀錄不能領先於系統時間。"""
        bad = "## 觀察紀錄\n- 2027-01-01：某某觀察\n"
        assert "2026-09-03：某某觀察" in pu.enforce_observation_date(bad, DAY)
