"""tests/test_daytrading_config.py — PR-6：當沖下單設定"""
from __future__ import annotations

import os
from unittest.mock import patch


# ── DaytradingConfig dataclass ────────────────────────────────────────────────

class TestDaytradingConfigDataclass:
    def test_all_fields_accessible(self):
        from daytrading_config import DaytradingConfig
        cfg = DaytradingConfig(
            budget_per_stock=30000.0,
            stop_loss_pct=3.0,
            take_profit_pct=9.0,
            trailing_start_pct=2.0,
            trailing_gap_pct=0.5,
            force_close_time="13:00",
            require_manual_confirm=True,
        )
        assert cfg.budget_per_stock == 30000.0
        assert cfg.stop_loss_pct == 3.0
        assert cfg.take_profit_pct == 9.0
        assert cfg.trailing_start_pct == 2.0
        assert cfg.trailing_gap_pct == 0.5
        assert cfg.force_close_time == "13:00"
        assert cfg.require_manual_confirm is True

    def test_manual_confirm_false(self):
        from daytrading_config import DaytradingConfig
        cfg = DaytradingConfig(
            budget_per_stock=50000.0, stop_loss_pct=2.0, take_profit_pct=8.0,
            trailing_start_pct=1.5, trailing_gap_pct=0.3,
            force_close_time="12:30", require_manual_confirm=False,
        )
        assert cfg.require_manual_confirm is False


# ── load_daytrading_config ────────────────────────────────────────────────────

class TestLoadDaytradingConfig:
    def test_defaults_when_no_env(self):
        env = {k: None for k in [
            "DT_BUDGET", "DT_STOP_LOSS_PCT", "DT_TAKE_PROFIT_PCT",
            "DT_TRAILING_START_PCT", "DT_TRAILING_GAP_PCT",
            "DT_FORCE_CLOSE_TIME", "DT_MANUAL_CONFIRM",
            "DT_DAILY_MAX_LOSS", "DT_RISK_PER_TRADE_PCT",
        ]}
        # patch os.getenv to return None → defaults used
        with patch.dict(os.environ, {}, clear=False):
            for key in env:
                os.environ.pop(key, None)
            from daytrading_config import load_daytrading_config
            cfg = load_daytrading_config()

        assert cfg.budget_per_stock == 30000.0
        assert cfg.stop_loss_pct == 3.0
        assert cfg.take_profit_pct == 9.0
        assert cfg.trailing_start_pct == 3.0
        assert cfg.trailing_gap_pct == 2.0
        assert cfg.force_close_time == "13:00"
        assert cfg.require_manual_confirm is True
        assert cfg.daily_max_loss == 3000.0
        assert cfg.risk_per_trade_pct == 1.0

    def test_env_overrides(self):
        env_overrides = {
            "DT_BUDGET": "50000",
            "DT_STOP_LOSS_PCT": "2.5",
            "DT_TAKE_PROFIT_PCT": "8.0",
            "DT_TRAILING_START_PCT": "1.5",
            "DT_TRAILING_GAP_PCT": "0.3",
            "DT_FORCE_CLOSE_TIME": "12:30",
            "DT_MANUAL_CONFIRM": "false",
        }
        with patch.dict(os.environ, env_overrides):
            from daytrading_config import load_daytrading_config
            cfg = load_daytrading_config()

        assert cfg.budget_per_stock == 50000.0
        assert cfg.stop_loss_pct == 2.5
        assert cfg.take_profit_pct == 8.0
        assert cfg.trailing_start_pct == 1.5
        assert cfg.trailing_gap_pct == 0.3
        assert cfg.force_close_time == "12:30"
        assert cfg.require_manual_confirm is False

    def test_manual_confirm_true_variants(self):
        for val in ["true", "True", "TRUE"]:
            with patch.dict(os.environ, {"DT_MANUAL_CONFIRM": val}):
                from daytrading_config import load_daytrading_config
                cfg = load_daytrading_config()
                assert cfg.require_manual_confirm is True

    def test_manual_confirm_false_variants(self):
        for val in ["false", "False", "FALSE", "0"]:
            with patch.dict(os.environ, {"DT_MANUAL_CONFIRM": val}):
                from daytrading_config import load_daytrading_config
                cfg = load_daytrading_config()
                assert cfg.require_manual_confirm is False


# ── Task 4: unknown / legacy JSON fields must not crash ───────────────────────

class TestLoadConfigUnknownFields:
    def test_legacy_field_does_not_crash(self, tmp_path, caplog):
        """JSON 含舊欄位 trailing_stop_pct 時不 crash，其餘欄位正常載入。"""
        import json, logging
        from daytrading_config import load_daytrading_config

        path = str(tmp_path / "cfg.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "budget_per_stock": 45000.0,
                "stop_loss_pct": 2.5,
                "trailing_stop_pct": 1.23,   # 舊/未知欄位
                "some_removed_field": "x",
            }, f)

        with caplog.at_level(logging.WARNING):
            cfg = load_daytrading_config(path=path)

        # 已知欄位正常套用
        assert cfg.budget_per_stock == 45000.0
        assert cfg.stop_loss_pct == 2.5
        # 未知欄位不會變成屬性、也不會 crash
        assert not hasattr(cfg, "trailing_stop_pct")
        # 有 warning 列出未知欄位
        assert any("trailing_stop_pct" in r.message for r in caplog.records)

    def test_known_fields_only_still_works(self, tmp_path):
        import json
        from daytrading_config import load_daytrading_config
        path = str(tmp_path / "cfg.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"take_profit_pct": 7.5}, f)
        cfg = load_daytrading_config(path=path)
        assert cfg.take_profit_pct == 7.5


# ── Task: llm_mode（decider = 現狀 LLM 決策 / advisor = 規則決策，LLM 僅評論）──

class TestLlmMode:
    def test_default_is_decider(self):
        from daytrading_config import DaytradingConfig
        cfg = DaytradingConfig()
        assert cfg.llm_mode == "decider"

    def test_dataclass_accepts_advisor(self):
        from daytrading_config import DaytradingConfig
        cfg = DaytradingConfig(llm_mode="advisor")
        assert cfg.llm_mode == "advisor"

    def test_load_defaults_to_decider_when_no_env(self):
        import os
        from unittest.mock import patch
        from daytrading_config import load_daytrading_config
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DT_LLM_MODE", None)
            cfg = load_daytrading_config()
        assert cfg.llm_mode == "decider"

    def test_env_override_advisor(self):
        import os
        from unittest.mock import patch
        from daytrading_config import load_daytrading_config
        with patch.dict(os.environ, {"DT_LLM_MODE": "advisor"}):
            cfg = load_daytrading_config()
        assert cfg.llm_mode == "advisor"

    def test_env_override_decider_explicit(self):
        import os
        from unittest.mock import patch
        from daytrading_config import load_daytrading_config
        with patch.dict(os.environ, {"DT_LLM_MODE": "decider"}):
            cfg = load_daytrading_config()
        assert cfg.llm_mode == "decider"

    def test_invalid_env_value_falls_back_to_decider_with_warning(self, caplog):
        import os
        import logging
        from unittest.mock import patch
        from daytrading_config import load_daytrading_config
        with patch.dict(os.environ, {"DT_LLM_MODE": "auto_pilot"}):
            with caplog.at_level(logging.WARNING):
                cfg = load_daytrading_config()
        assert cfg.llm_mode == "decider"
        assert any("llm_mode" in r.message for r in caplog.records)

    def test_invalid_json_value_falls_back_to_decider_with_warning(self, tmp_path, caplog):
        import json
        import logging
        from daytrading_config import load_daytrading_config
        path = str(tmp_path / "cfg.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"llm_mode": "nonsense"}, f)
        with caplog.at_level(logging.WARNING):
            cfg = load_daytrading_config(path=path)
        assert cfg.llm_mode == "decider"
        assert any("llm_mode" in r.message for r in caplog.records)

    def test_json_advisor_value_accepted(self, tmp_path):
        import json
        from daytrading_config import load_daytrading_config
        path = str(tmp_path / "cfg.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"llm_mode": "advisor"}, f)
        cfg = load_daytrading_config(path=path)
        assert cfg.llm_mode == "advisor"
