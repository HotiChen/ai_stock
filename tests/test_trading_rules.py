from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from trading_rules import (
    TradingRules,
    ValidationResult,
    calc_max_quantity,
    load_rules,
    save_rules,
    validate_decision,
    summarize_chat_to_strategy,
)
from chat_agent import ChatMessage


# ── Fixtures ──────────────────────────────────────────────────────────────────

DEFAULT_RULES = TradingRules()

def buy_decision(code="2330", qty=1, confidence=8) -> dict:
    return {"action": "buy", "stock_code": code, "quantity": qty,
            "reason": "RSI 低", "confidence": confidence}

def sell_decision(code="2330", qty=1, confidence=8) -> dict:
    return {"action": "sell", "stock_code": code, "quantity": qty,
            "reason": "停利", "confidence": confidence}

def hold_decision() -> dict:
    return {"action": "hold", "stock_code": "", "quantity": 1,
            "reason": "觀望", "confidence": 5}


# ── TradingRules ──────────────────────────────────────────────────────────────

class TestTradingRules:
    def test_has_default_values(self):
        r = TradingRules()
        assert r.max_trades_per_day >= 1
        assert 0 < r.max_position_pct <= 1.0
        assert r.stop_loss_pct > 0
        assert r.take_profit_pct > 0
        assert r.min_confidence >= 0

    def test_can_be_customized(self):
        r = TradingRules(
            max_trades_per_day=2,
            max_position_pct=0.5,
            stop_loss_pct=3.0,
            take_profit_pct=10.0,
            min_confidence=7,
        )
        assert r.max_trades_per_day == 2
        assert r.stop_loss_pct == pytest.approx(3.0)

    def test_default_stop_loss_reasonable(self):
        assert 1.0 <= TradingRules().stop_loss_pct <= 20.0

    def test_default_take_profit_reasonable(self):
        assert 3.0 <= TradingRules().take_profit_pct <= 50.0

    def test_default_max_position_is_fraction(self):
        assert TradingRules().max_position_pct < 1.0


# ── ValidationResult ──────────────────────────────────────────────────────────

class TestValidationResult:
    def test_allowed_true(self):
        r = ValidationResult(allowed=True, reason="通過所有規則")
        assert r.allowed is True

    def test_allowed_false_with_reason(self):
        r = ValidationResult(allowed=False, reason="超過倉位上限")
        assert r.allowed is False
        assert len(r.reason) > 0

    def test_adjusted_quantity_optional(self):
        r = ValidationResult(allowed=True, reason="ok", adjusted_quantity=2)
        assert r.adjusted_quantity == 2

    def test_default_adjusted_quantity_none(self):
        r = ValidationResult(allowed=True, reason="ok")
        assert r.adjusted_quantity is None


# ── save_rules / load_rules ───────────────────────────────────────────────────

class TestSaveLoadRules:
    def test_roundtrip(self):
        rules = TradingRules(stop_loss_pct=7.0, take_profit_pct=20.0, min_confidence=8)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        save_rules(rules, path)
        loaded = load_rules(path)
        assert loaded.stop_loss_pct == pytest.approx(7.0)
        assert loaded.take_profit_pct == pytest.approx(20.0)
        assert loaded.min_confidence == 8

    def test_load_nonexistent_returns_defaults(self):
        loaded = load_rules("/tmp/nonexistent_rules_xyz.json")
        assert isinstance(loaded, TradingRules)
        assert loaded.stop_loss_pct == TradingRules().stop_loss_pct

    def test_saved_file_is_valid_json(self):
        rules = TradingRules()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        save_rules(rules, path)
        with open(path) as f:
            data = json.load(f)
        assert "stop_loss_pct" in data

    def test_partial_json_uses_defaults(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({"stop_loss_pct": 3.0}, f)
            path = f.name
        loaded = load_rules(path)
        assert loaded.stop_loss_pct == pytest.approx(3.0)
        assert loaded.take_profit_pct == TradingRules().take_profit_pct


# ── calc_max_quantity ─────────────────────────────────────────────────────────

class TestCalcMaxQuantity:
    def test_basic_calculation(self):
        # cash=100000, price=500, max_pct=1.0 → 可買 200 股 = 0張? no, 100000/500=200 shares=0.2張
        # cash=100000, price=100, max_pct=1.0 → 100000/100=1000股=1張
        qty = calc_max_quantity(cash=100_000, price=100.0, max_pct=1.0)
        assert qty == 1

    def test_respects_max_pct(self):
        # cash=200000, price=100, max_pct=0.5 → usable=100000 → 1張
        qty = calc_max_quantity(cash=200_000, price=100.0, max_pct=0.5)
        assert qty == 1

    def test_insufficient_cash_returns_zero(self):
        qty = calc_max_quantity(cash=500, price=1000.0, max_pct=1.0)
        assert qty == 0

    def test_returns_integer(self):
        qty = calc_max_quantity(cash=50_000, price=150.0, max_pct=0.7)
        assert isinstance(qty, int)

    def test_multiple_lots(self):
        # cash=600000, price=100, max_pct=1.0 → 600000/(100*1000)=6張
        qty = calc_max_quantity(cash=600_000, price=100.0, max_pct=1.0)
        assert qty == 6

    def test_zero_price_returns_zero(self):
        qty = calc_max_quantity(cash=100_000, price=0.0, max_pct=1.0)
        assert qty == 0


# ── validate_decision ─────────────────────────────────────────────────────────

class TestValidateDecision:
    # ── hold always allowed ──
    def test_hold_always_allowed(self):
        result = validate_decision(hold_decision(), DEFAULT_RULES, cash=30_000,
                                   current_price=100.0, buys_today=1, sells_today=1)
        assert result.allowed is True

    # ── confidence filter ──
    def test_low_confidence_buy_rejected(self):
        rules = TradingRules(min_confidence=7)
        result = validate_decision(buy_decision(confidence=4), rules, cash=30_000,
                                   current_price=100.0, buys_today=0, sells_today=0)
        assert result.allowed is False
        assert "信心" in result.reason

    def test_sufficient_confidence_passes(self):
        rules = TradingRules(min_confidence=6)
        result = validate_decision(buy_decision(confidence=8), rules, cash=100_000,
                                   current_price=100.0, buys_today=0, sells_today=0)
        assert result.allowed is True

    # ── daily trade limit ──
    def test_buy_rejected_when_reached_daily_limit(self):
        rules = TradingRules(max_trades_per_day=1, min_confidence=0)
        result = validate_decision(buy_decision(), rules, cash=100_000,
                                   current_price=100.0, buys_today=1, sells_today=0)
        assert result.allowed is False
        assert "次數" in result.reason or "已達" in result.reason or "上限" in result.reason

    def test_sell_rejected_when_reached_daily_limit(self):
        rules = TradingRules(max_trades_per_day=1, min_confidence=0)
        result = validate_decision(sell_decision(), rules, cash=0,
                                   current_price=100.0, buys_today=0, sells_today=1)
        assert result.allowed is False

    def test_buy_allowed_when_under_daily_limit(self):
        rules = TradingRules(max_trades_per_day=2, min_confidence=0)
        result = validate_decision(buy_decision(), rules, cash=200_000,
                                   current_price=100.0, buys_today=1, sells_today=0)
        assert result.allowed is True

    # ── position size limit ──
    def test_buy_rejected_when_insufficient_cash(self):
        rules = TradingRules(max_position_pct=0.7, min_confidence=0)
        # cash=5000, price=100 → max usable=3500 → can't buy 1張(=100000)
        result = validate_decision(buy_decision(qty=1), rules, cash=5_000,
                                   current_price=100.0, buys_today=0, sells_today=0)
        assert result.allowed is False
        assert "資金" in result.reason or "現金" in result.reason or "不足" in result.reason

    def test_quantity_adjusted_down_when_too_large(self):
        rules = TradingRules(max_position_pct=0.5, min_confidence=0)
        # cash=300000, price=100, max_pct=0.5 → usable=150000 → max 1張
        # but requested 5張 → should adjust to 1
        result = validate_decision(buy_decision(qty=5), rules, cash=300_000,
                                   current_price=100.0, buys_today=0, sells_today=0)
        assert result.allowed is True
        assert result.adjusted_quantity is not None
        assert result.adjusted_quantity < 5

    def test_valid_buy_passes_all_checks(self):
        rules = TradingRules(max_position_pct=1.0, min_confidence=5, max_trades_per_day=2)
        result = validate_decision(buy_decision(qty=1, confidence=8), rules,
                                   cash=200_000, current_price=100.0,
                                   buys_today=0, sells_today=0)
        assert result.allowed is True

    def test_none_price_buy_rejected(self):
        rules = TradingRules(min_confidence=0)
        result = validate_decision(buy_decision(), rules, cash=100_000,
                                   current_price=None, buys_today=0, sells_today=0)
        assert result.allowed is False


# ── summarize_chat_to_strategy ────────────────────────────────────────────────

class TestSummarizeChatToStrategy:
    def _make_chat(self) -> list[ChatMessage]:
        return [
            ChatMessage(role="user",      content="台積電現在適合買嗎？"),
            ChatMessage(role="assistant", content="根據 RSI 低檔和基本面良好，建議買進台積電"),
            ChatMessage(role="user",      content="好，我決定買 2 張"),
            ChatMessage(role="assistant", content="建議買進 2330 台積電 2 張，停損設在 -5%"),
        ]

    @patch("trading_rules.call_ollama")
    def test_returns_decision_dict(self, mock_ollama):
        mock_ollama.return_value = '{"action": "buy", "stock_code": "2330", "quantity": 2, "reason": "RSI低+基本面佳", "confidence": 8}'
        result = summarize_chat_to_strategy(self._make_chat())
        assert isinstance(result, dict)
        assert "action" in result

    @patch("trading_rules.call_ollama")
    def test_extracts_action_from_chat(self, mock_ollama):
        mock_ollama.return_value = '{"action": "buy", "stock_code": "2330", "quantity": 2, "reason": "ok", "confidence": 8}'
        result = summarize_chat_to_strategy(self._make_chat())
        assert result["action"] in ("buy", "sell", "hold")

    @patch("trading_rules.call_ollama")
    def test_includes_chat_content_in_prompt(self, mock_ollama):
        mock_ollama.return_value = '{"action": "hold", "stock_code": "", "quantity": 1, "reason": "觀望", "confidence": 5}'
        summarize_chat_to_strategy(self._make_chat())
        prompt_sent = mock_ollama.call_args[0][1]
        assert "台積電" in prompt_sent or "RSI" in prompt_sent

    @patch("trading_rules.call_ollama", side_effect=Exception("timeout"))
    def test_ollama_failure_returns_hold(self, mock_ollama):
        result = summarize_chat_to_strategy(self._make_chat())
        assert result["action"] == "hold"

    @patch("trading_rules.call_ollama")
    def test_empty_chat_returns_hold(self, mock_ollama):
        mock_ollama.return_value = '{"action": "hold"}'
        result = summarize_chat_to_strategy([])
        assert result["action"] == "hold"

    @patch("trading_rules.call_ollama")
    def test_result_has_all_required_keys(self, mock_ollama):
        mock_ollama.return_value = '{"action": "buy", "stock_code": "2330", "quantity": 1, "reason": "好", "confidence": 7}'
        result = summarize_chat_to_strategy(self._make_chat())
        for key in ("action", "stock_code", "quantity", "reason", "confidence"):
            assert key in result
