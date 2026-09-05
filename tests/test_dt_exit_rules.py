"""
tests/test_dt_exit_rules.py — 出場規則單一真相來源

為什麼需要這個模組
------------------
出場邏輯原本有兩份實作，門檻不同：

  monitor_agent.check_price_alerts()        tick 路徑（真實下單）
      停損 = ATR 絕對價、停利 = ATR 絕對價、無天花板停利、無時間強平
  daytrading_monitor.check_trailing_stop()  5 分鐘輪詢 / 紙上模擬
      停損 = 固定 3%、停利 = 固定 9%、有時間強平

也就是說：**紙上模擬跑的規則不是實盤跑的規則**。一檔 ATR 等於價格 1% 的
股票，tick 路徑在 −1.5% 出場，紙上在 −3.0% 出場——差一倍。用紙上資料
推論實盤績效因此完全不成立，而那正是整個複盤/回測計畫的基礎。

另一個更隱蔽的問題：舊版 check_price_alerts 在移動停損啟動後直接
`return alerts`（空 list），目標價與停損價都不再檢查。而 ATR 目標價
（entry + 2.5 ATR）通常大於移動停損啟動門檻（3%），所以價格幾乎一定
先啟動移動停損 → 目標價實質上永遠不會觸發。

統一後的優先順序
----------------
  1. 停損       絕對價（ATR）優先；沒有才用百分比
  2. 天花板停利 百分比（漲停前強制出場，避免漲停鎖死出不掉）
  3. 強制平倉   時間到
  4. 移動停損   峰值達門檻後回落
  5. 目標價     絕對價（ATR）

保護性出場（1–3）一律優先於計畫性出場（4–5）。
移動停損排在目標價**之前**：已經回落的部位先出場；但因為它不再提前
return，價格若一路衝到目標價仍會由第 5 條觸發——這正是舊版壞掉的地方。
"""
from datetime import datetime

import pytest

import dt_exit_rules as er


class _Cfg:
    """DaytradingConfig 的最小替身（鴨型別）。"""
    def __init__(self, **kw):
        self.stop_loss_pct = kw.get("stop_loss_pct", 3.0)
        self.take_profit_pct = kw.get("take_profit_pct", 9.0)
        self.trailing_start_pct = kw.get("trailing_start_pct", 3.0)
        self.trailing_gap_pct = kw.get("trailing_gap_pct", 2.0)
        self.force_close_time = kw.get("force_close_time", "13:00")


def _ev(**kw):
    """entry=100 的預設情境，只覆寫關心的欄位。"""
    base = dict(
        entry_price=100.0, current_price=100.0, peak_price=None,
        stop_loss=None, target_price=None,
        stop_loss_pct=3.0, take_profit_pct=9.0,
        trailing_start_pct=3.0, trailing_gap_pct=2.0,
        force_close_time=None, now=datetime(2026, 9, 5, 10, 0),
    )
    base.update(kw)
    return er.evaluate_exit(**base)


class TestNoExit:
    def test_flat_price_holds(self):
        assert _ev().should_exit is False

    def test_reason_empty_when_holding(self):
        assert _ev().reason == ""

    def test_small_gain_holds(self):
        assert _ev(current_price=101.0).should_exit is False


class TestStopLoss:
    """★ 統一的核心：ATR 絕對價優先於固定百分比。"""

    def test_atr_stop_wins_over_percentage(self):
        """ATR 停損在 −1.5%，固定停損在 −3%：應該在 −1.5% 就出場。

        舊版兩條路徑對這個情境會給出不同答案，這正是要修的東西。
        """
        d = _ev(current_price=98.5, stop_loss=98.5)
        assert d.should_exit is True
        assert d.reason == er.REASON_STOP_LOSS

    def test_percentage_used_when_no_atr_stop(self):
        d = _ev(current_price=97.0, stop_loss=None)
        assert d.should_exit is True
        assert d.reason == er.REASON_STOP_LOSS

    def test_percentage_not_reached_holds(self):
        assert _ev(current_price=98.0, stop_loss=None).should_exit is False

    def test_atr_stop_not_reached_holds(self):
        assert _ev(current_price=99.0, stop_loss=98.0).should_exit is False

    def test_atr_stop_wider_than_percentage_still_wins(self):
        """ATR 停損比固定 3% 更寬（−5%）時也以 ATR 為準——單一真相來源的意義
        就是不會有第二個門檻偷偷先觸發。"""
        d = _ev(current_price=96.0, stop_loss=95.0)
        assert d.should_exit is False

    def test_exact_touch_triggers(self):
        assert _ev(current_price=98.0, stop_loss=98.0).should_exit is True

    def test_stop_beats_everything_else_same_tick(self):
        """同一 tick 停損與目標價都成立時一律認賠——保守方向。"""
        d = _ev(current_price=98.0, stop_loss=98.0, target_price=97.0)
        assert d.reason == er.REASON_STOP_LOSS


class TestTakeProfitCeiling:
    """漲停是 10%，9% 出場是刻意不賭最後 1%——漲停打不開就賣不掉。"""

    def test_ceiling_triggers(self):
        d = _ev(current_price=109.0)
        assert d.should_exit is True
        assert d.reason == er.REASON_TAKE_PROFIT_CEILING

    def test_ceiling_beats_target(self):
        """ATR 目標價若落在 9% 之外，天花板必須先出場。"""
        d = _ev(current_price=109.0, target_price=112.0)
        assert d.reason == er.REASON_TAKE_PROFIT_CEILING

    def test_below_ceiling_holds(self):
        assert _ev(current_price=108.0).should_exit is False


class TestForceClose:
    def test_triggers_at_time(self):
        d = _ev(force_close_time="13:00", now=datetime(2026, 9, 5, 13, 0))
        assert d.should_exit is True
        assert d.reason == er.REASON_FORCE_CLOSE

    def test_before_time_holds(self):
        d = _ev(force_close_time="13:00", now=datetime(2026, 9, 5, 12, 59))
        assert d.should_exit is False

    def test_no_time_configured_never_force_closes(self):
        d = _ev(force_close_time=None, now=datetime(2026, 9, 5, 23, 59))
        assert d.should_exit is False

    def test_stop_loss_still_wins_at_force_close_time(self):
        d = _ev(current_price=97.0, force_close_time="13:00",
                now=datetime(2026, 9, 5, 13, 0))
        assert d.reason == er.REASON_STOP_LOSS


class TestTrailingStop:
    def test_not_armed_below_threshold(self):
        """峰值只到 +2%，未達 3% 啟動門檻 → 回落不觸發。"""
        d = _ev(current_price=100.0, peak_price=102.0)
        assert d.should_exit is False

    def test_armed_and_dropped_triggers(self):
        """峰值 +5%，回落到 102（自峰值 −2.86%）→ 觸發。"""
        d = _ev(current_price=102.0, peak_price=105.0)
        assert d.should_exit is True
        assert d.reason == er.REASON_TRAILING

    def test_armed_but_not_dropped_enough_holds(self):
        """峰值 +3.5%，現價 102（自峰值 −1.45%）→ 未達 2% 間距。"""
        assert _ev(current_price=102.0, peak_price=103.5).should_exit is False

    def test_peak_defaults_to_entry_when_missing(self):
        assert _ev(current_price=100.0, peak_price=None).should_exit is False

    def test_trailing_stop_price_reported(self):
        d = _ev(current_price=102.0, peak_price=105.0)
        assert d.trailing_stop_price == pytest.approx(105.0 * 0.98)


class TestTargetPrice:
    """★ 舊版最嚴重的行為缺陷：移動停損啟動後直接 return，目標價再也碰不到。"""

    def test_target_triggers_when_trailing_not_armed(self):
        d = _ev(current_price=102.0, target_price=102.0,
                trailing_start_pct=50.0)   # 拉高門檻確保未啟動
        assert d.should_exit is True
        assert d.reason == er.REASON_TARGET

    def test_target_reachable_while_trailing_armed(self):
        """★ 回歸測試。

        峰值 +8%（移動停損已啟動），現價 108 就是峰值本身、沒有回落，
        且已達目標價 108。舊版在這裡回空 list（什麼都不做），
        部位就這樣一直掛著。統一後必須以目標價出場。
        """
        d = _ev(current_price=108.0, peak_price=108.0, target_price=108.0)
        assert d.should_exit is True
        assert d.reason == er.REASON_TARGET

    def test_trailing_wins_when_both_available(self):
        """已經回落到移動停損線之下，就算現價仍高於目標價也走移動停損——
        保護已實現的利潤優先於計畫價。"""
        d = _ev(current_price=102.0, peak_price=105.0, target_price=101.0)
        assert d.reason == er.REASON_TRAILING

    def test_no_target_configured_holds(self):
        assert _ev(current_price=999.0, target_price=None,
                   take_profit_pct=None).should_exit is False


class TestNoEntryPrice:
    """watching 狀態（尚未成交）只有絕對價可判斷，所有百分比規則必須跳過，
    不能拿 None 去算除法。"""

    def test_absolute_stop_still_works(self):
        d = _ev(entry_price=None, current_price=98.0, stop_loss=98.0)
        assert d.reason == er.REASON_STOP_LOSS

    def test_absolute_target_still_works(self):
        d = _ev(entry_price=None, current_price=110.0, target_price=110.0)
        assert d.reason == er.REASON_TARGET

    def test_percentage_rules_skipped(self):
        assert _ev(entry_price=None, current_price=1.0).should_exit is False

    def test_zero_entry_treated_as_missing(self):
        assert _ev(entry_price=0.0, current_price=1.0).should_exit is False


class TestPriorityOrderIsStable:
    """優先順序本身就是規則，改動它必須是明確的決定而不是重構的副作用。"""

    def test_declared_order(self):
        assert er.PRIORITY == (
            er.REASON_STOP_LOSS,
            er.REASON_TAKE_PROFIT_CEILING,
            er.REASON_FORCE_CLOSE,
            er.REASON_TRAILING,
            er.REASON_TARGET,
        )

    def test_every_reason_has_a_label(self):
        for r in er.PRIORITY:
            assert er.SELL_LABEL[r]


class TestFromConfig:
    """兩條路徑都是拿 DaytradingConfig 來呼叫，這個轉接器是它們共用的入口。"""

    def test_reads_all_thresholds(self):
        d = er.evaluate_exit_from_config(
            config=_Cfg(), entry_price=100.0, current_price=97.0,
            now=datetime(2026, 9, 5, 10, 0),
        )
        assert d.reason == er.REASON_STOP_LOSS

    def test_atr_prices_passed_through(self):
        d = er.evaluate_exit_from_config(
            config=_Cfg(), entry_price=100.0, current_price=98.5,
            stop_loss=98.5, now=datetime(2026, 9, 5, 10, 0),
        )
        assert d.reason == er.REASON_STOP_LOSS

    def test_force_close_time_read_from_config(self):
        d = er.evaluate_exit_from_config(
            config=_Cfg(force_close_time="13:00"), entry_price=100.0,
            current_price=100.0, now=datetime(2026, 9, 5, 13, 1),
        )
        assert d.reason == er.REASON_FORCE_CLOSE

    def test_bad_force_close_time_does_not_raise(self):
        d = er.evaluate_exit_from_config(
            config=_Cfg(force_close_time="not-a-time"), entry_price=100.0,
            current_price=100.0, now=datetime(2026, 9, 5, 13, 1),
        )
        assert d.should_exit is False


class TestMessagesAreUseful:
    """訊息會直接推到 Telegram，出場當下要看得懂為什麼。"""

    @pytest.mark.parametrize("kw,frag", [
        (dict(current_price=97.0), "停損"),
        (dict(current_price=109.0), "天花板"),
        (dict(current_price=102.0, peak_price=105.0), "追蹤"),
        (dict(current_price=108.0, peak_price=108.0, target_price=108.0), "目標"),
    ])
    def test_message_names_the_rule(self, kw, frag):
        assert frag in _ev(**kw).message

    def test_force_close_message_names_the_time(self):
        d = _ev(force_close_time="13:00", now=datetime(2026, 9, 5, 13, 0))
        assert "13:00" in d.message

    def test_trigger_price_always_current_price(self):
        assert _ev(current_price=97.0).trigger_price == 97.0
