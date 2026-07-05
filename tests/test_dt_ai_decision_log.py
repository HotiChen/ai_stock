"""tests/test_dt_ai_decision_log.py — AI 決策全落庫（daytrading_db.ai_decision_log）

目的：讓 dt_counterfactual.py 之類的反事實分析工具能重建「LLM 決策 vs 規則決策」
的完整歷史，不論 llm_mode 為何都要落庫，且落庫失敗不得影響交易流程。
"""
from __future__ import annotations

import sqlite3

import pytest


def _make_db(tmp_path):
    from daytrading_db import DaytradingDB
    return DaytradingDB(path=str(tmp_path / "review.db"))


class TestAiDecisionLogTable:
    def test_table_created(self, tmp_path):
        db = _make_db(tmp_path)
        with sqlite3.connect(db.path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_decision_log'"
            ).fetchone()
        assert row is not None


class TestLogAiDecision:
    def test_log_decider_mode_stores_all_fields(self, tmp_path):
        db = _make_db(tmp_path)
        db.log_ai_decision(
            date="2026-07-05", time="08:30", code="2330", stage="premarket",
            llm_mode="decider", dt_score=8,
            prompt="判斷 2330 是否做多", raw_response='{"action":"long"}',
            parsed_action="long", rule_action="long", final_action="long",
            features={"indicators": {"RSI": 55}, "market": {"index_change_pct": 0.3}},
        )
        rows = db.get_ai_decisions("2026-07-05")
        assert len(rows) == 1
        r = rows[0]
        assert r["code"] == "2330"
        assert r["stage"] == "premarket"
        assert r["llm_mode"] == "decider"
        assert r["dt_score"] == 8
        assert r["prompt_hash"] is not None
        assert len(r["prompt_hash"]) == 16
        assert r["raw_response"] == '{"action":"long"}'
        assert r["parsed_action"] == "long"
        assert r["rule_action"] == "long"
        assert r["final_action"] == "long"
        assert "RSI" in r["features_json"]
        assert r["created_at"] is not None

    def test_log_advisor_mode_rule_action_differs_from_parsed(self, tmp_path):
        """advisor 模式下 LLM 只是評論，rule_action 才是實際決策（final_action）。"""
        db = _make_db(tmp_path)
        db.log_ai_decision(
            date="2026-07-05", time="08:30", code="2454", stage="premarket",
            llm_mode="advisor", dt_score=7,
            prompt="評論用", raw_response='{"action":"skip"}',
            parsed_action="skip", rule_action="long", final_action="long",
            features={},
        )
        rows = db.get_ai_decisions("2026-07-05")
        assert rows[0]["parsed_action"] == "skip"
        assert rows[0]["rule_action"] == "long"
        assert rows[0]["final_action"] == "long"

    def test_log_reconfirm_stage(self, tmp_path):
        db = _make_db(tmp_path)
        db.log_ai_decision(
            date="2026-07-05", time="09:05", code="2330", stage="reconfirm",
            llm_mode="decider", dt_score=8,
            prompt=None, raw_response=None,
            parsed_action=None, rule_action="proceed", final_action="proceed",
            features={"current_price": 101.0},
        )
        rows = db.get_ai_decisions("2026-07-05", stage="reconfirm")
        assert len(rows) == 1
        assert rows[0]["stage"] == "reconfirm"
        assert rows[0]["prompt_hash"] is None

    def test_get_ai_decisions_filters_by_stage(self, tmp_path):
        db = _make_db(tmp_path)
        db.log_ai_decision(
            date="2026-07-05", time="08:30", code="2330", stage="premarket",
            llm_mode="decider", dt_score=8, prompt="p", raw_response="r",
            parsed_action="long", rule_action="long", final_action="long", features={},
        )
        db.log_ai_decision(
            date="2026-07-05", time="09:05", code="2330", stage="reconfirm",
            llm_mode="decider", dt_score=8, prompt="p2", raw_response="r2",
            parsed_action="proceed", rule_action="proceed", final_action="proceed",
            features={},
        )
        premarket = db.get_ai_decisions("2026-07-05", stage="premarket")
        reconfirm = db.get_ai_decisions("2026-07-05", stage="reconfirm")
        both = db.get_ai_decisions("2026-07-05")
        assert len(premarket) == 1 and premarket[0]["stage"] == "premarket"
        assert len(reconfirm) == 1 and reconfirm[0]["stage"] == "reconfirm"
        assert len(both) == 2

    def test_get_ai_decisions_empty_date_returns_empty_list(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.get_ai_decisions("2020-01-01") == []

    def test_log_failure_does_not_raise(self, tmp_path, monkeypatch):
        """落庫失敗（例如 DB 檔案不可寫）不得拋出例外，交易流程不能被打斷。"""
        db = _make_db(tmp_path)

        def _boom(*a, **kw):
            raise sqlite3.OperationalError("disk full (simulated)")

        monkeypatch.setattr(db, "_conn", _boom)
        # 不應拋出
        db.log_ai_decision(
            date="2026-07-05", time="08:30", code="2330", stage="premarket",
            llm_mode="decider", dt_score=8, prompt="p", raw_response="r",
            parsed_action="long", rule_action="long", final_action="long", features={},
        )

    def test_prompt_none_gives_none_hash(self, tmp_path):
        db = _make_db(tmp_path)
        db.log_ai_decision(
            date="2026-07-05", time="08:30", code="2330", stage="premarket",
            llm_mode="advisor", dt_score=8, prompt=None, raw_response=None,
            parsed_action=None, rule_action="skip", final_action="skip", features={},
        )
        rows = db.get_ai_decisions("2026-07-05")
        assert rows[0]["prompt_hash"] is None

    def test_features_json_serializable_with_nested_dict(self, tmp_path):
        db = _make_db(tmp_path)
        db.log_ai_decision(
            date="2026-07-05", time="08:30", code="2330", stage="premarket",
            llm_mode="decider", dt_score=8, prompt="p", raw_response="r",
            parsed_action="long", rule_action="long", final_action="long",
            features={"indicators": {"RSI": 55.5, "bullish_alignment": True},
                      "chip": None, "market": {"index_change_pct": -0.1}},
        )
        rows = db.get_ai_decisions("2026-07-05")
        import json
        parsed = json.loads(rows[0]["features_json"])
        assert parsed["indicators"]["RSI"] == 55.5
        assert parsed["chip"] is None
