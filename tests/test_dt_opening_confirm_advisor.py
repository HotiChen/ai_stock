"""tests/test_dt_opening_confirm_advisor.py — main._opening_confirm_dt_positions

llm_mode="decider"（預設）：行為必須與現狀完全一致（零回歸）—— 呼叫
  daytrading_analyzer.run_opening_reconfirm，proceed 完全由它決定。
llm_mode="advisor"：改用 dt_rules.rule_opening_reconfirm 決定 proceed，
  可省略 LLM 呼叫（省成本），Telegram 訊息標明「規則決策」。
兩種 mode 都要把決策落庫到 ai_decision_log（stage="reconfirm"）。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_position(code="2330", name="台積電", entry_low=95.0, entry_high=105.0,
                    dt_score=8, status="watching", ai_summary="盤前建議做多"):
    from daytrading_monitor import DaytradingPosition
    return DaytradingPosition(
        code=code, name=name,
        entry_low=entry_low, entry_high=entry_high,
        target_price=110.0, stop_loss=90.0,
        dt_score=dt_score, status=status, ai_summary=ai_summary,
    )


def _cfg(llm_mode="decider"):
    from daytrading_config import DaytradingConfig
    return DaytradingConfig(llm_mode=llm_mode)


class _Ctx:
    """集中管理 _opening_confirm_dt_positions 所需的 patch，回傳各 mock 供斷言。"""

    def __init__(self, positions, market=None, snap=None,
                 llm_reconfirm_result=None, llm_reconfirm_side_effect=None,
                 chat_id="12345"):
        self.positions = positions
        self.market = market or {"index_change_pct": 0.2, "futures_premium_pct": 0.0}
        self.snap = snap if snap is not None else {
            "close": 100.0, "change_price": 0.5, "volume": 1000,
        }
        self.llm_reconfirm_result = llm_reconfirm_result
        self.llm_reconfirm_side_effect = llm_reconfirm_side_effect
        self.chat_id = chat_id

    def __enter__(self):
        self.patches = []

        def _p(*a, **kw):
            patcher = patch(*a, **kw)
            self.patches.append(patcher)
            return patcher.start()

        self.mock_load = _p("daytrading_monitor.load_daytrading_positions",
                             return_value=self.positions)
        self.mock_save = _p("daytrading_monitor.save_daytrading_positions")
        self.mock_mark_skipped = _p("daytrading_monitor.mark_skipped")
        self.mock_snapshot = _p("monitor_agent.get_snapshot", return_value=self.snap)
        self.mock_market = _p("daytrading_report._fetch_market", return_value=self.market)
        self.mock_send_text = _p("telegram_bot.send_text")

        if self.llm_reconfirm_side_effect is not None:
            self.mock_reconfirm = _p("daytrading_analyzer.run_opening_reconfirm",
                                      side_effect=self.llm_reconfirm_side_effect)
        else:
            self.mock_reconfirm = _p("daytrading_analyzer.run_opening_reconfirm",
                                      return_value=self.llm_reconfirm_result)

        self.mock_log = _p("daytrading_db.DaytradingDB.log_ai_decision")
        _p("main.TELEGRAM_CHAT_ID", self.chat_id)
        return self

    def __exit__(self, *a):
        for patcher in reversed(self.patches):
            patcher.stop()


def _llm_reconfirm(proceed=True, reason="AI 判斷"):
    from daytrading_analyzer import OpeningReconfirm
    return OpeningReconfirm(
        code="2330", name="台積電", proceed=proceed, reason=reason,
        updated_entry_low=None, updated_entry_high=None,
    )


class TestDeciderModeUnaffected:
    """decider 模式：零回歸——proceed 完全由 run_opening_reconfirm 決定。"""

    def test_calls_llm_reconfirm(self):
        from main import _opening_confirm_dt_positions
        pos = _make_position()
        with _Ctx([pos], llm_reconfirm_result=_llm_reconfirm(proceed=True)) as ctx:
            _opening_confirm_dt_positions(api=None, dt_config=_cfg("decider"))
        ctx.mock_reconfirm.assert_called_once()

    def test_llm_says_proceed_keeps_watching_not_skipped(self):
        from main import _opening_confirm_dt_positions
        pos = _make_position()
        with _Ctx([pos], llm_reconfirm_result=_llm_reconfirm(proceed=True)) as ctx:
            _opening_confirm_dt_positions(api=None, dt_config=_cfg("decider"))
        ctx.mock_mark_skipped.assert_not_called()

    def test_llm_says_reject_marks_skipped(self):
        from main import _opening_confirm_dt_positions
        pos = _make_position()
        with _Ctx([pos], llm_reconfirm_result=_llm_reconfirm(proceed=False, reason="大盤轉弱")) as ctx:
            _opening_confirm_dt_positions(api=None, dt_config=_cfg("decider"))
        ctx.mock_mark_skipped.assert_called_once_with("2330", path="data/daytrading_positions.json")


class TestAdvisorModeUsesRules:
    def test_does_not_call_llm_reconfirm(self):
        """advisor 模式應省略 LLM 呼叫（省成本）。"""
        from main import _opening_confirm_dt_positions
        pos = _make_position(entry_low=95.0, entry_high=105.0)
        with _Ctx([pos], snap={"close": 100.0, "change_price": 0.5, "volume": 1000},
                   market={"index_change_pct": 0.2}) as ctx:
            _opening_confirm_dt_positions(api=MagicMock(), dt_config=_cfg("advisor"))
        ctx.mock_reconfirm.assert_not_called()

    def test_rule_proceeds_when_price_in_range(self):
        from main import _opening_confirm_dt_positions
        pos = _make_position(entry_low=95.0, entry_high=105.0)
        with _Ctx([pos], snap={"close": 100.0, "change_price": 0.5, "volume": 1000},
                   market={"index_change_pct": 0.2}) as ctx:
            _opening_confirm_dt_positions(api=MagicMock(), dt_config=_cfg("advisor"))
        ctx.mock_mark_skipped.assert_not_called()

    def test_rule_skips_when_price_far_outside_range(self):
        from main import _opening_confirm_dt_positions
        pos = _make_position(entry_low=95.0, entry_high=105.0)
        with _Ctx([pos], snap={"close": 50.0, "change_price": 0.5, "volume": 1000},
                   market={"index_change_pct": 0.2}) as ctx:
            _opening_confirm_dt_positions(api=MagicMock(), dt_config=_cfg("advisor"))
        ctx.mock_mark_skipped.assert_called_once_with("2330", path="data/daytrading_positions.json")

    def test_telegram_message_marks_rule_decision(self):
        from main import _opening_confirm_dt_positions
        pos = _make_position(entry_low=95.0, entry_high=105.0)
        with _Ctx([pos], snap={"close": 100.0, "change_price": 0.5, "volume": 1000},
                   market={"index_change_pct": 0.2}) as ctx:
            _opening_confirm_dt_positions(api=MagicMock(), dt_config=_cfg("advisor"))
        assert ctx.mock_send_text.called
        sent_text = ctx.mock_send_text.call_args[0][1]
        assert "規則決策" in sent_text


class TestAiDecisionLogReconfirmStage:
    def test_logs_for_decider_mode(self):
        from main import _opening_confirm_dt_positions
        pos = _make_position()
        with _Ctx([pos], llm_reconfirm_result=_llm_reconfirm(proceed=True)) as ctx:
            _opening_confirm_dt_positions(api=None, dt_config=_cfg("decider"))
        assert ctx.mock_log.called
        _, kwargs = ctx.mock_log.call_args
        assert kwargs["stage"] == "reconfirm"
        assert kwargs["llm_mode"] == "decider"
        assert kwargs["final_action"] == "proceed"
        # decider 模式也順便算規則決策
        assert kwargs["rule_action"] in ("proceed", "skip")

    def test_logs_for_advisor_mode(self):
        from main import _opening_confirm_dt_positions
        pos = _make_position(entry_low=95.0, entry_high=105.0)
        with _Ctx([pos], snap={"close": 100.0, "change_price": 0.5, "volume": 1000},
                   market={"index_change_pct": 0.2}) as ctx:
            _opening_confirm_dt_positions(api=MagicMock(), dt_config=_cfg("advisor"))
        assert ctx.mock_log.called
        _, kwargs = ctx.mock_log.call_args
        assert kwargs["stage"] == "reconfirm"
        assert kwargs["llm_mode"] == "advisor"
        assert kwargs["rule_action"] == "proceed"
        assert kwargs["final_action"] == "proceed"
        # advisor 模式沒呼叫 LLM，parsed_action 應為 None
        assert kwargs["parsed_action"] is None

    def test_log_failure_does_not_raise(self):
        from main import _opening_confirm_dt_positions
        pos = _make_position()
        with _Ctx([pos], llm_reconfirm_result=_llm_reconfirm(proceed=True)) as ctx:
            ctx.mock_log.side_effect = Exception("db down")
            # 不應拋出
            _opening_confirm_dt_positions(api=None, dt_config=_cfg("decider"))
