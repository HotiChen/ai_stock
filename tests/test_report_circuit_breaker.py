"""
tests/test_report_circuit_breaker.py — 指標連續取不到就停手

情境：Shioaji 歷史資料額度用完（每日 500 MB）。8:30 的 50 支候選會每支
都失敗，而每次失敗要等 45 秒逾時 —— 50 × 45 秒 = 37 分鐘，直接輾過 9:00
開盤，而且最後還是零候選。

失敗要快、要大聲。連續 N 支取不到指標就中止迴圈，用已取得的部分產報告，
並在報告裡明說發生了什麼。
"""
from unittest.mock import patch

import pytest

import daytrading_report as dr


class TestIndicatorCircuitBreaker:
    def _picks(self, n):
        return [{"code": f"{2000 + i}", "name": f"股{i}", "confidence": 5}
                for i in range(n)]

    def _run(self, indicators_side_effect, n=50):
        calls = []

        def _get_ind(code, api=None):
            calls.append(code)
            return indicators_side_effect(code)

        with patch.object(dr, "_get_stock_universe", return_value=self._picks(n)), \
             patch.object(dr, "_fetch_market",
                          return_value={"index_change_pct": 0.0,
                                        "futures_premium_pct": 0.0}), \
             patch.object(dr, "_fetch_chip_data", return_value={}), \
             patch.object(dr, "_fetch_historical_win_rate", return_value=None), \
             patch.object(dr, "_get_indicators", side_effect=_get_ind):
            msg = dr.build_daytrading_report(api=None, review_db_path=":memory:")
        return msg, calls

    def test_stops_after_consecutive_failures(self):
        """★ 全部取不到時不得跑滿 50 支——每支 45 秒逾時會拖 37 分鐘。"""
        msg, calls = self._run(lambda code: None)
        assert len(calls) <= dr.MAX_CONSECUTIVE_INDICATOR_FAILURES + 1, \
            f"應在連續 {dr.MAX_CONSECUTIVE_INDICATOR_FAILURES} 次失敗後中止，實際跑了 {len(calls)} 支"

    def test_report_explains_the_abort(self):
        """報告要說出真正原因，不能只寫「條件不成熟」讓人以為是市況問題。"""
        msg, _ = self._run(lambda code: None)
        assert "資料" in msg

    def test_intermittent_failures_do_not_abort(self):
        """零星失敗（個股停牌、查無合約）不該中止整批——只有連續失敗才算。"""
        good = {"current_price": 100.0, "RSI": 55.0, "volume_ratio": 2.0,
                "ATR": 3.0, "VWAP": 99.0, "KD_K": 50.0, "KD_D": 50.0,
                "bullish_alignment": True, "bearish_alignment": False,
                "resistance": 110.0, "support": 95.0}
        seen = {"n": 0}

        def alternating(code):
            seen["n"] += 1
            return None if seen["n"] % 2 else dict(good)

        msg, calls = self._run(alternating, n=20)
        assert len(calls) == 20, "交錯失敗不應觸發熔斷"

    def test_success_resets_the_streak(self):
        good = {"current_price": 100.0, "RSI": 55.0, "volume_ratio": 2.0,
                "ATR": 3.0, "VWAP": 99.0, "KD_K": 50.0, "KD_D": 50.0,
                "bullish_alignment": True, "bearish_alignment": False,
                "resistance": 110.0, "support": 95.0}
        n_fail = dr.MAX_CONSECUTIVE_INDICATOR_FAILURES - 1
        seq = ([None] * n_fail) + [dict(good)] + ([None] * n_fail) + [dict(good)]
        it = iter(seq)
        msg, calls = self._run(lambda code: next(it, None), n=len(seq))
        assert len(calls) == len(seq), "成功一次應把連續失敗計數歸零"
