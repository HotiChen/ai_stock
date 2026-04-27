from __future__ import annotations

import pytest
from rules import evaluate_signals, score_signals


# ── helpers ───────────────────────────────────────────────────────────────────

def _ind(**kwargs) -> dict:
    """最小有效指標 dict，方便各測試只改關心的欄位。"""
    base = dict(
        current_price=100.0,
        MA5=98.0, MA10=97.0, MA20=95.0, MA60=90.0,
        RSI=50.0,
        KD_K=50.0, KD_D=50.0,
        KD_K_prev=48.0, KD_D_prev=49.0,
        MACD_hist=0.1, MACD_hist_prev=0.05,
        BB_upper=110.0, BB_lower=85.0, BB_position=0.5,
        volume_ratio=1.0,
        resistance=108.0, support=88.0,
        ATR=2.0,
        bullish_alignment=True,
        bearish_alignment=False,
        trailing_stop=93.0,
        stop_loss_ma20=94.05,
    )
    base.update(kwargs)
    return base


def _signal_names(signals: list) -> list[str]:
    return [s["name"] for s in signals]


def _signal_types(signals: list) -> list[str]:
    return [s["type"] for s in signals]


# ── evaluate_signals: 均線訊號 ─────────────────────────────────────────────────

class TestMaSignals:
    def test_golden_cross_buy(self):
        prev = _ind(MA5=94.0, MA20=95.0)
        curr = _ind(MA5=96.0, MA20=95.0)
        sigs = evaluate_signals(curr, prev)
        assert "均線黃金交叉" in _signal_names(sigs)
        cross = next(s for s in sigs if s["name"] == "均線黃金交叉")
        assert cross["type"] == "BUY"

    def test_death_cross_sell(self):
        prev = _ind(MA5=96.0, MA20=95.0)
        curr = _ind(MA5=94.0, MA20=95.0)
        sigs = evaluate_signals(curr, prev)
        assert "均線死亡交叉" in _signal_names(sigs)

    def test_bullish_alignment_buy(self):
        sigs = evaluate_signals(_ind(bullish_alignment=True))
        assert "均線多頭排列" in _signal_names(sigs)
        assert all(s["type"] == "BUY" for s in sigs if s["name"] == "均線多頭排列")

    def test_bearish_alignment_sell(self):
        sigs = evaluate_signals(_ind(bearish_alignment=True, bullish_alignment=False))
        assert "均線空頭排列" in _signal_names(sigs)

    def test_no_cross_without_prev(self):
        sigs = evaluate_signals(_ind())
        assert "均線黃金交叉" not in _signal_names(sigs)
        assert "均線死亡交叉" not in _signal_names(sigs)


# ── evaluate_signals: 支撐壓力訊號 ────────────────────────────────────────────

class TestSupportResistanceSignals:
    def test_volume_breakout_resistance(self):
        # price just above resistance with high volume
        sigs = evaluate_signals(_ind(current_price=108.5, resistance=108.0, volume_ratio=2.0))
        assert "帶量突破壓力" in _signal_names(sigs)

    def test_no_breakout_without_volume(self):
        sigs = evaluate_signals(_ind(current_price=108.5, resistance=108.0, volume_ratio=1.0))
        assert "帶量突破壓力" not in _signal_names(sigs)

    def test_break_support_sell(self):
        sigs = evaluate_signals(_ind(current_price=87.5, support=88.0))
        assert "跌破支撐" in _signal_names(sigs)

    def test_near_resistance_watch(self):
        # price between 97% and 100% of resistance
        sigs = evaluate_signals(_ind(current_price=107.0, resistance=108.0))
        assert "接近壓力位" in _signal_names(sigs)

    def test_near_support_watch(self):
        sigs = evaluate_signals(_ind(current_price=89.0, support=88.0))
        assert "接近支撐位" in _signal_names(sigs)


# ── evaluate_signals: 成交量訊號 ──────────────────────────────────────────────

class TestVolumeSignals:
    def test_volume_breakout_above_ma20(self):
        sigs = evaluate_signals(_ind(volume_ratio=2.0, current_price=100.0, MA20=95.0))
        assert "帶量站上均線" in _signal_names(sigs)

    def test_volume_shrink_at_support(self):
        # 縮量回測支撐
        sigs = evaluate_signals(_ind(volume_ratio=0.5, current_price=89.0, support=88.0))
        assert "量縮回測支撐" in _signal_names(sigs)

    def test_high_volume_black_candle_sell(self):
        # 爆量長黑：量 > 2x 且 price < MA20
        sigs = evaluate_signals(_ind(volume_ratio=2.5, current_price=90.0, MA20=95.0))
        assert "爆量長黑" in _signal_names(sigs)

    def test_volume_up_price_down_watch(self):
        prev = _ind(current_price=105.0)
        curr = _ind(current_price=100.0, volume_ratio=1.3)
        sigs = evaluate_signals(curr, prev)
        assert "量增價跌" in _signal_names(sigs)


# ── evaluate_signals: KD 訊號 ─────────────────────────────────────────────────

class TestKdSignals:
    def test_kd_low_golden_cross_buy(self):
        # K < 20, K 從下穿越 D
        prev = _ind(KD_K=15.0, KD_D=18.0, KD_K_prev=15.0, KD_D_prev=18.0)
        curr = _ind(KD_K=17.0, KD_D=16.0, KD_K_prev=15.0, KD_D_prev=18.0)
        sigs = evaluate_signals(curr, prev)
        assert "KD 低檔黃金交叉" in _signal_names(sigs)

    def test_kd_high_death_cross_sell(self):
        # K > 80, K 從上穿越 D
        prev = _ind(KD_K=85.0, KD_D=82.0, KD_K_prev=85.0, KD_D_prev=82.0)
        curr = _ind(KD_K=83.0, KD_D=84.0, KD_K_prev=85.0, KD_D_prev=82.0)
        sigs = evaluate_signals(curr, prev)
        assert "KD 高檔死亡交叉" in _signal_names(sigs)

    def test_kd_overbought_watch(self):
        sigs = evaluate_signals(_ind(KD_K=85.0, KD_D=82.0))
        assert "KD 超買鈍化" in _signal_names(sigs)


# ── evaluate_signals: RSI 訊號 ────────────────────────────────────────────────

class TestRsiSignals:
    def test_rsi_oversold_buy(self):
        sigs = evaluate_signals(_ind(RSI=25.0))
        assert "RSI 超賣" in _signal_names(sigs)
        assert any(s["type"] == "BUY" for s in sigs if s["name"] == "RSI 超賣")

    def test_rsi_overbought_watch(self):
        sigs = evaluate_signals(_ind(RSI=75.0))
        assert "RSI 超買" in _signal_names(sigs)

    def test_rsi_cross_50_buy(self):
        prev = _ind(RSI=48.0)
        curr = _ind(RSI=52.0)
        sigs = evaluate_signals(curr, prev)
        assert "RSI 穿越 50" in _signal_names(sigs)

    def test_rsi_normal_no_signal(self):
        sigs = evaluate_signals(_ind(RSI=55.0))
        names = _signal_names(sigs)
        assert "RSI 超賣" not in names
        assert "RSI 超買" not in names


# ── evaluate_signals: MACD 訊號 ───────────────────────────────────────────────

class TestMacdSignals:
    def test_macd_hist_turns_positive_buy(self):
        sigs = evaluate_signals(_ind(MACD_hist=0.1, MACD_hist_prev=-0.05))
        assert "MACD 柱狀翻正" in _signal_names(sigs)

    def test_macd_hist_turns_negative_sell(self):
        sigs = evaluate_signals(_ind(MACD_hist=-0.1, MACD_hist_prev=0.05))
        assert "MACD 柱狀翻負" in _signal_names(sigs)

    def test_macd_hist_same_sign_no_signal(self):
        sigs = evaluate_signals(_ind(MACD_hist=0.2, MACD_hist_prev=0.1))
        names = _signal_names(sigs)
        assert "MACD 柱狀翻正" not in names
        assert "MACD 柱狀翻負" not in names


# ── score_signals ─────────────────────────────────────────────────────────────

class TestScoreSignals:
    def _make_signals(self, buys=0, sells=0, watches=0, weight=1):
        signals = []
        for _ in range(buys):
            signals.append({"type": "BUY",   "name": "buy_sig",   "weight": weight})
        for _ in range(sells):
            signals.append({"type": "SELL",  "name": "sell_sig",  "weight": weight})
        for _ in range(watches):
            signals.append({"type": "WATCH", "name": "watch_sig", "weight": weight})
        return signals

    def test_strong_buy(self):
        sigs = self._make_signals(buys=5, weight=1)   # buy_score = 5
        result = score_signals(sigs)
        assert result["recommendation"] == "強力買進"

    def test_buy(self):
        sigs = self._make_signals(buys=3, weight=1)   # buy_score = 3
        result = score_signals(sigs)
        assert result["recommendation"] == "買進"

    def test_strong_sell(self):
        sigs = self._make_signals(sells=5, weight=1)
        result = score_signals(sigs)
        assert result["recommendation"] == "強力賣出"

    def test_sell(self):
        sigs = self._make_signals(sells=3, weight=1)
        result = score_signals(sigs)
        assert result["recommendation"] == "賣出"

    def test_watch_bullish_when_buy_nonzero_no_sell(self):
        sigs = self._make_signals(buys=1, weight=1)
        result = score_signals(sigs)
        assert result["recommendation"] == "觀察偏多"

    def test_watch_when_mixed(self):
        sigs = self._make_signals(buys=2, sells=2, weight=1)
        result = score_signals(sigs)
        assert result["recommendation"] == "觀察"

    def test_scores_summed_correctly(self):
        sigs = [
            {"type": "BUY",  "name": "a", "weight": 3},
            {"type": "BUY",  "name": "b", "weight": 2},
            {"type": "SELL", "name": "c", "weight": 1},
        ]
        result = score_signals(sigs)
        assert result["buy_score"]  == 5
        assert result["sell_score"] == 1

    def test_signals_list_in_output(self):
        sigs = [{"type": "BUY", "name": "均線黃金交叉", "weight": 2}]
        result = score_signals(sigs)
        assert "均線黃金交叉" in result["signals"]

    def test_empty_signals_is_watch(self):
        result = score_signals([])
        assert result["buy_score"] == 0
        assert result["sell_score"] == 0
        assert result["recommendation"] == "觀察"
