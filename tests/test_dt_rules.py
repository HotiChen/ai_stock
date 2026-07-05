"""tests/test_dt_rules.py — 確定性規則決策（llm_mode="advisor" 的核心邏輯）

dt_rules.py 是純函數模組，零 LLM 呼叫，涵蓋：
  - rule_decide_action    ：8:30 是否做多
  - rule_entry_range      ：8:30 進場區間
  - rule_opening_reconfirm：9:05 開盤是否繼續進場
"""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# rule_decide_action
# ══════════════════════════════════════════════════════════════════════════════

class TestRuleDecideAction:
    def _indicators(self, **overrides):
        base = {
            "current_price": 100.0,
            "volume_ratio": 2.0,
            "bullish_alignment": True,
            "bearish_alignment": False,
            "RSI": 55.0,
            "VWAP": 99.0,
        }
        base.update(overrides)
        return base

    def _market(self, **overrides):
        base = {"index_change_pct": 0.3}
        base.update(overrides)
        return base

    def test_all_conditions_met_returns_long(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(7, self._indicators(), self._market())
        assert result.action == "long"

    def test_score_below_threshold_skips(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(5, self._indicators(), self._market())
        assert result.action == "skip"
        assert "dt_score" in result.reason or "評分" in result.reason

    def test_score_exactly_threshold_passes(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(6, self._indicators(), self._market())
        assert result.action == "long"

    def test_low_volume_ratio_skips(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(7, self._indicators(volume_ratio=1.0), self._market())
        assert result.action == "skip"

    def test_missing_indicators_does_not_block_volume_check(self):
        """indicators 整體缺失時，量比條件不擋，但仍應在 reason 中註記。"""
        from dt_rules import rule_decide_action
        result = rule_decide_action(7, None, self._market())
        # 沒有多頭排列 / RSI 資料 → 排列條件不成立 → 最終仍是 skip
        assert result.action == "skip"
        assert "量比" in result.reason or "缺" in result.reason

    def test_weak_market_skips(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(7, self._indicators(), self._market(index_change_pct=-1.0))
        assert result.action == "skip"

    def test_missing_market_does_not_block(self):
        """market 缺失時，大盤條件不擋（其餘條件仍需成立）。"""
        from dt_rules import rule_decide_action
        result = rule_decide_action(7, self._indicators(), None)
        assert result.action == "long"

    def test_bearish_alignment_with_rsi_out_of_range_skips(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(
            7,
            self._indicators(bullish_alignment=False, RSI=80.0),
            self._market(),
        )
        assert result.action == "skip"

    def test_rsi_in_range_without_bullish_alignment_passes(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(
            7,
            self._indicators(bullish_alignment=False, RSI=50.0),
            self._market(),
        )
        assert result.action == "long"

    def test_rsi_boundary_45_passes(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(
            7, self._indicators(bullish_alignment=False, RSI=45.0), self._market(),
        )
        assert result.action == "long"

    def test_rsi_boundary_75_passes(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(
            7, self._indicators(bullish_alignment=False, RSI=75.0), self._market(),
        )
        assert result.action == "long"

    def test_reason_lists_multiple_unmet_conditions(self):
        from dt_rules import rule_decide_action
        result = rule_decide_action(
            2,
            self._indicators(volume_ratio=0.5, bullish_alignment=False, RSI=90.0),
            self._market(index_change_pct=-2.0),
        )
        assert result.action == "skip"
        assert result.reason  # 非空字串，說明未過條件


# ══════════════════════════════════════════════════════════════════════════════
# rule_entry_range
# ══════════════════════════════════════════════════════════════════════════════

class TestRuleEntryRange:
    def test_current_price_below_vwap(self):
        from dt_rules import rule_entry_range
        low, high = rule_entry_range({"current_price": 98.0, "VWAP": 100.0})
        assert low == pytest.approx(98.0 * 0.997, abs=0.01)
        assert high == pytest.approx(100.0 * 1.003, abs=0.01)

    def test_current_price_above_vwap(self):
        from dt_rules import rule_entry_range
        low, high = rule_entry_range({"current_price": 102.0, "VWAP": 100.0})
        assert low == pytest.approx(100.0 * 0.997, abs=0.01)
        assert high == pytest.approx(102.0 * 1.003, abs=0.01)

    def test_missing_indicators_returns_none_none(self):
        from dt_rules import rule_entry_range
        assert rule_entry_range(None) == (None, None)

    def test_missing_vwap_returns_none_none(self):
        from dt_rules import rule_entry_range
        assert rule_entry_range({"current_price": 100.0}) == (None, None)

    def test_missing_current_price_returns_none_none(self):
        from dt_rules import rule_entry_range
        assert rule_entry_range({"VWAP": 100.0}) == (None, None)

    def test_zero_price_returns_none_none(self):
        from dt_rules import rule_entry_range
        assert rule_entry_range({"current_price": 0.0, "VWAP": 100.0}) == (None, None)


# ══════════════════════════════════════════════════════════════════════════════
# rule_opening_reconfirm
# ══════════════════════════════════════════════════════════════════════════════

class _Pos:
    def __init__(self, entry_low=95.0, entry_high=105.0):
        self.entry_low = entry_low
        self.entry_high = entry_high


class TestRuleOpeningReconfirm:
    def test_all_conditions_met_proceeds(self):
        from dt_rules import rule_opening_reconfirm
        result = rule_opening_reconfirm(
            _Pos(), current_price=100.0, change_price=0.5, volume=1000,
            market={"index_change_pct": 0.2},
        )
        assert result.proceed is True

    def test_zero_or_negative_price_blocks(self):
        from dt_rules import rule_opening_reconfirm
        result = rule_opening_reconfirm(
            _Pos(), current_price=0.0, change_price=0.5, volume=1000,
            market={"index_change_pct": 0.2},
        )
        assert result.proceed is False

    def test_price_far_below_entry_range_blocks(self):
        from dt_rules import rule_opening_reconfirm
        result = rule_opening_reconfirm(
            _Pos(entry_low=95.0, entry_high=105.0),
            current_price=50.0, change_price=0.5, volume=1000,
            market={"index_change_pct": 0.2},
        )
        assert result.proceed is False

    def test_price_far_above_entry_range_blocks(self):
        from dt_rules import rule_opening_reconfirm
        result = rule_opening_reconfirm(
            _Pos(entry_low=95.0, entry_high=105.0),
            current_price=200.0, change_price=0.5, volume=1000,
            market={"index_change_pct": 0.2},
        )
        assert result.proceed is False

    def test_missing_entry_range_blocks(self):
        from dt_rules import rule_opening_reconfirm
        result = rule_opening_reconfirm(
            _Pos(entry_low=None, entry_high=None),
            current_price=100.0, change_price=0.5, volume=1000,
            market={"index_change_pct": 0.2},
        )
        assert result.proceed is False

    def test_weak_market_blocks(self):
        from dt_rules import rule_opening_reconfirm
        result = rule_opening_reconfirm(
            _Pos(), current_price=100.0, change_price=0.5, volume=1000,
            market={"index_change_pct": -1.0},
        )
        assert result.proceed is False

    def test_opening_drop_blocks(self):
        from dt_rules import rule_opening_reconfirm
        # -1.5% of a ~100 price ≈ -1.5 元；-3 元跌幅超過門檻
        result = rule_opening_reconfirm(
            _Pos(), current_price=100.0, change_price=-3.0, volume=1000,
            market={"index_change_pct": 0.2},
        )
        assert result.proceed is False

    def test_small_opening_drop_within_tolerance_proceeds(self):
        from dt_rules import rule_opening_reconfirm
        result = rule_opening_reconfirm(
            _Pos(), current_price=100.0, change_price=-0.5, volume=1000,
            market={"index_change_pct": 0.2},
        )
        assert result.proceed is True

    def test_reason_non_empty_on_block(self):
        from dt_rules import rule_opening_reconfirm
        result = rule_opening_reconfirm(
            _Pos(), current_price=0.0, change_price=0.5, volume=1000,
            market={"index_change_pct": 0.2},
        )
        assert result.reason

    def test_supports_dict_pos(self):
        """pos 也可以是 dict（供測試/其他呼叫方彈性使用）。"""
        from dt_rules import rule_opening_reconfirm
        result = rule_opening_reconfirm(
            {"entry_low": 95.0, "entry_high": 105.0},
            current_price=100.0, change_price=0.5, volume=1000,
            market={"index_change_pct": 0.2},
        )
        assert result.proceed is True
