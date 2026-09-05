"""
tests/test_executor_restriction_guard.py — Guard 0b：處置股／注意股

守衛放在 place_stock_order 而不是各呼叫端，理由與 HALT 那道相同：三個買進
入口（MarketOpenJob、DT auto-buy、Telegram 快速下單）全部經過這個函式，
這是唯一的咽喉點。散在呼叫端遲早會漏一個。
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import executor
import twse_restrictions as tr


def _seed(codes=None, warnings=None, ok=True):
    """覆寫 conftest 注入的「今天沒有任何限制」。"""
    tr.reset_memo()
    if ok:
        tr._MEMO[date.today()] = tr.RestrictedSet(
            codes=codes or {}, warnings=warnings or {},
            ok=True, as_of=date.today())
    # ok=False 時不塞 memo，並讓抓取直接失敗（見各測試的 patch）


def _order(action="buy", code="3021", **kw):
    args = dict(
        api=MagicMock(), code=code, name="測試股", action=action,
        budget=30_000.0, price=50.0, paper_trading=True,
    )
    args.update(kw)
    return executor.place_stock_order(**args)


class TestBlocksRestrictedCode:
    def test_disposed_stock_is_rejected(self):
        _seed({"3021": "處置股票（分盤交易，多半禁止當沖）"})
        r = _order()
        assert r.success is False
        assert "交易限制" in r.reason

    def test_reason_names_the_restriction(self):
        _seed({"3021": "處置股票（分盤交易，多半禁止當沖）"})
        assert "處置" in _order().reason

    def test_clean_stock_passes_the_guard(self):
        _seed({"3021": "處置"})
        r = _order(code="2330")
        assert r.success is True

    def test_attention_stock_is_not_blocked(self):
        """★ 注意股交易方式完全正常，當沖做得了——擋掉是過度保護。

        原本這個測試叫 test_attention_stock_also_blocked，靠著把注意股直接
        塞進 codes（擋單清單）而「通過」。名字與實際驗證的東西不一致，
        等於用測試背書一個沒被驗證過的設計。
        """
        _seed(warnings={"2454": "注意股票（交易正常，但可能轉處置）"})
        assert _order(code="2454").success is True

    def test_attention_stock_is_logged(self, caplog):
        _seed(warnings={"2454": "注意股票（交易正常，但可能轉處置）"})
        with caplog.at_level("WARNING"):
            _order(code="2454")
        assert any("注意股票" in r.getMessage() for r in caplog.records)

    def test_disposed_beats_attention_when_both(self):
        _seed(codes={"2455": "處置股票"}, warnings={"2455": "注意股票"})
        assert _order(code="2455").success is False


class TestSellIsNeverBlocked:
    """★ 與 HALT、下單帳本同一個原則：緊急機制只能擋「進」不能擋「出」。

    被擋住的賣出會讓部位留到隔天變成交割義務——那正是這道守衛要防的事，
    在這裡擋賣單等於自己製造同一個問題。
    """

    def test_sell_passes_even_when_restricted(self):
        _seed({"3021": "處置股票"})
        assert _order(action="sell").success is True

    def test_sell_passes_even_when_list_unavailable(self):
        _seed(ok=False)
        with patch.object(tr, "fetch_restricted",
                          return_value=tr.RestrictedSet(ok=False, error="逾時")):
            assert _order(action="sell").success is True


class TestFailClosedOnUnknown:
    """查不到清單 → 擋買單。漏擋一檔可能違約交割，誤擋一檔只是少賺。"""

    def test_buy_blocked_when_list_unavailable(self):
        _seed(ok=False)
        with patch.object(tr, "fetch_restricted",
                          return_value=tr.RestrictedSet(ok=False, error="逾時")):
            r = _order()
        assert r.success is False
        assert "查不到" in r.reason or "無法確認" in r.reason

    def test_can_be_opted_out_by_env(self, monkeypatch):
        """放行必須是明確的選擇。"""
        monkeypatch.setenv("DT_BLOCK_ON_UNKNOWN_RESTRICTION", "false")
        _seed(ok=False)
        with patch.object(tr, "fetch_restricted",
                          return_value=tr.RestrictedSet(ok=False, error="逾時")):
            assert _order().success is True


class TestAppliesToPaperTrading:
    """★ 紙上模擬必須套用同一組限制。

    紙上若買得到實盤根本買不到的標的，模擬損益就不能推論實盤——
    那正是 dt_exit_rules 這次剛收斂掉的問題，不要在這裡重新製造一次。
    """

    def test_paper_mode_still_blocked(self):
        _seed({"3021": "處置股票"})
        assert _order(paper_trading=True).success is False

    def test_real_mode_also_blocked(self):
        _seed({"3021": "處置股票"})
        assert _order(paper_trading=False).success is False

    def test_guard_runs_before_paper_short_circuit(self):
        """紙上分支在守衛之後：被擋的單不該留下 [PAPER] 成交紀錄。"""
        _seed({"3021": "處置股票"})
        r = _order(paper_trading=True)
        assert r.quantity == 0 and r.amount == 0.0


class TestModuleFailureIsNotFatal:
    """程式壞掉（import 失敗、內部例外）與資料查不到是兩件事。

    後者由 check() 內部 fail-closed；前者若也擋住全部買單，等於一個 bug
    就讓系統整天不交易。放行並 log.error，讓它被發現而不是被隱藏。
    """

    def test_exception_inside_check_allows_the_order(self):
        _seed()
        with patch.object(tr, "check", side_effect=RuntimeError("模組壞了")):
            assert _order(code="2330").success is True

    def test_exception_is_logged_as_error(self, caplog):
        _seed()
        with patch.object(tr, "check", side_effect=RuntimeError("模組壞了")):
            with caplog.at_level("ERROR"):
                _order(code="2330")
        assert any("交易限制檢查異常" in r.getMessage() for r in caplog.records)


class TestGuardOrder:
    def test_restriction_checked_before_quantity_math(self):
        """預算不足與交易限制同時成立時，回報限制——那是更根本的原因。"""
        _seed({"3021": "處置股票"})
        r = _order(budget=1.0, price=50.0)
        assert "交易限制" in r.reason
