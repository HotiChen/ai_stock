"""
tests/test_dt_exit_plan.py — 沒有出場計畫就不進場

2026-09-04 的實際案例（3021 鴻名）：

  08:34 premarket  rule=skip  final=skip      系統同意跳過
  09:05 reconfirm  rule=skip  final=proceed   LLM 翻掉了規則的否決
  09:10 進場        entry=24.3  stop_loss=None  target_price=None

8:30 判定 skip 的標的，entry/target/stop 全是 None（見 daytrading_report
的設計：skip 也存成 watching，讓 9:05 結合開盤實況重新判斷）。9:05 翻案時
只會更新 entry 區間，**不會補上停損價**——於是部位帶著「沒有風險上限」進場。

而系統在那一刻是知道的：calc_risk_quantity 回 (0, "缺少停損價，改用固定
預算法")。它知道算不出風險額，然後退回固定預算照買。

這道檢查與「誰有最終決定權」無關：不管 decider 還是 advisor，不管規則或
LLM 說什麼，**沒有停損價就是沒有風險上限**，不能進場。
"""
import pytest

import dt_rules


class TestMissingStopLoss:
    def test_none_stop_is_rejected(self):
        r = dt_rules.check_exit_plan(stop_loss=None, entry_price=24.3)
        assert r.ok is False
        assert "停損" in r.reason

    def test_zero_stop_is_rejected(self):
        """0 是「沒有值」，不是「停損在 0 元」。"""
        assert dt_rules.check_exit_plan(stop_loss=0.0, entry_price=24.3).ok is False

    def test_negative_stop_is_rejected(self):
        assert dt_rules.check_exit_plan(stop_loss=-1.0, entry_price=24.3).ok is False

    def test_valid_stop_passes(self):
        assert dt_rules.check_exit_plan(stop_loss=23.5, entry_price=24.3).ok is True


class TestStopMustBeBelowEntry:
    """停損價不低於進場價 = 買進當下就觸發，等於沒有停損。

    calc_risk_quantity 對這個情況同樣是回 0 並退回固定預算法——
    又一次「知道有問題卻照買」。
    """

    def test_stop_above_entry_is_rejected(self):
        r = dt_rules.check_exit_plan(stop_loss=25.0, entry_price=24.3)
        assert r.ok is False
        assert "進場價" in r.reason

    def test_stop_equal_to_entry_is_rejected(self):
        assert dt_rules.check_exit_plan(stop_loss=24.3, entry_price=24.3).ok is False

    def test_stop_just_below_entry_passes(self):
        assert dt_rules.check_exit_plan(stop_loss=24.29, entry_price=24.3).ok is True


class TestMissingEntryPrice:
    def test_none_entry_is_rejected(self):
        assert dt_rules.check_exit_plan(stop_loss=23.5, entry_price=None).ok is False

    def test_zero_entry_is_rejected(self):
        """報價 0 代表「沒有報價」，不是「免費」。"""
        assert dt_rules.check_exit_plan(stop_loss=23.5, entry_price=0.0).ok is False


class TestReasonIsActionable:
    def test_reason_names_the_numbers(self):
        r = dt_rules.check_exit_plan(stop_loss=25.0, entry_price=24.3)
        assert "25" in r.reason and "24.3" in r.reason

    def test_ok_result_has_no_reason(self):
        assert dt_rules.check_exit_plan(stop_loss=23.5, entry_price=24.3).reason == ""


class TestEnvSwitch:
    def test_required_by_default(self, monkeypatch):
        monkeypatch.delenv("DT_REQUIRE_STOP_LOSS", raising=False)
        assert dt_rules.require_stop_loss_default() is True

    def test_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("DT_REQUIRE_STOP_LOSS", "false")
        assert dt_rules.require_stop_loss_default() is False

    def test_garbage_stays_safe(self, monkeypatch):
        monkeypatch.setenv("DT_REQUIRE_STOP_LOSS", "sometimes")
        assert dt_rules.require_stop_loss_default() is True


class TestBothBuyPathsUseIt:
    """★ 這個專案反覆出現的失敗模式：同一個判斷散在多處，改了一處忘了另一處。

    當沖有兩個買入入口，而 DT_MANUAL_CONFIRM 預設 true，所以平常走的是
    Telegram 那條。只在 auto-buy 加檢查等於沒加。
    """

    def _src(self, module_name, func_name):
        import importlib
        import inspect
        m = importlib.import_module(module_name)
        return inspect.getsource(getattr(m, func_name))

    def test_auto_buy_path_checks(self):
        assert "check_exit_plan" in self._src("main", "_auto_buy_dt_positions")

    def test_telegram_path_checks(self):
        assert "check_exit_plan" in self._src("telegram_bot", "_handle_dt_buy")

    def test_check_runs_before_placing_the_order(self):
        """順序很重要：檢查必須在 place_stock_order 之前。"""
        for mod, fn in (("main", "_auto_buy_dt_positions"),
                        ("telegram_bot", "_handle_dt_buy")):
            src = self._src(mod, fn)
            assert src.index("check_exit_plan") < src.index("place_stock_order("), \
                f"{mod}.{fn}：檢查排在下單之後等於沒有檢查"
