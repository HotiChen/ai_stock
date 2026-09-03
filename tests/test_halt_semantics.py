"""
tests/test_halt_semantics.py — HALT 是「禁買閘門」，不是「主迴圈開關」

原始設計意圖（halt.py docstring，2026-05-19）是三段式緊急煞車：
    🚨 緊急暫停 → 停止開新倉
    🔄 撤銷委託 → 撤掉未成交的
    💥 一鍵平倉 → 市價出清
最輕的那一顆只該「別再買了」。

但實作把檢查放在 main() 主迴圈最頂端：

    if is_halted():
        time.sleep(30); continue      # 底下全部跳過

於是被擋住的不只有下單，還包括 5 分鐘出場輪詢（停損／停利）、13:15 強制平倉、
13:35 收盤複盤。**抱著當沖部位按下緊急暫停，系統會停止保護那個部位**——
沒平掉的當沖隔天變成交割義務，付不出來就是違約交割。

修法：閘門移到 executor.place_stock_order（三個買進入口的唯一咽喉），
只擋 action=="buy"；主迴圈不再有全域擋，出場路徑全部恢復暢通。
"""
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def halted(tmp_path, monkeypatch):
    """建立一個生效中的 HALT 旗標，並把 halt.py 指向暫存檔。"""
    import halt as halt_mod
    flag = tmp_path / "HALT"
    monkeypatch.setattr(halt_mod, "_HALT_FILE", flag)
    halt_mod.halt(reason="test")
    assert halt_mod.is_halted()
    return flag


# ══════════════════════════════════════════════════════════════════════════════
# ★ 核心一：閘門只擋買進
# ══════════════════════════════════════════════════════════════════════════════

class TestBuyGate:
    def _api(self):
        api = MagicMock()
        trade = MagicMock()
        trade.order.id = "OID123"
        api.place_order.return_value = trade
        return api

    def _order(self, api, action):
        from executor import place_stock_order
        return place_stock_order(
            api=api, code="2330", name="台積電", action=action,
            budget=30_000, price=100.0, paper_trading=False,
        )

    def test_buy_blocked_while_halted(self, halted):
        api = self._api()
        r = self._order(api, "buy")
        assert r.success is False
        assert not api.place_order.called, "HALT 時不得送出買單"

    def test_buy_reason_mentions_halt(self, halted):
        r = self._order(self._api(), "buy")
        assert "暫停" in r.reason

    def test_sell_never_blocked(self, halted):
        """★ 最重要的一條：賣出（停損／平倉）永遠放行。

        擋住賣出等於鎖住逃生門——當沖沒平掉隔天就是交割義務。
        """
        api = self._api()
        r = self._order(api, "sell")
        assert r.success is True, "HALT 絕不能擋住賣出"
        assert api.place_order.called

    def test_buy_allowed_when_not_halted(self, tmp_path, monkeypatch):
        import halt as halt_mod
        monkeypatch.setattr(halt_mod, "_HALT_FILE", tmp_path / "HALT")
        api = self._api()
        assert self._order(api, "buy").success is True

    def test_force_stop_loss_not_blocked(self, halted):
        """force_stop_loss 是停損的實際執行者，必須完全不受 HALT 影響。"""
        from executor import force_stop_loss
        api = MagicMock()
        trade = MagicMock()
        trade.order.id = "SL1"
        api.place_order.return_value = trade
        api.list_positions.return_value = []
        ok = force_stop_loss(api, "2330", "台積電", 1, paper_trading=True)
        assert ok is True


# ══════════════════════════════════════════════════════════════════════════════
# ★ 核心二：出場路徑在 HALT 期間仍會執行
# ══════════════════════════════════════════════════════════════════════════════

class TestExitPathsRunWhileHalted:
    def test_force_close_job_runs(self, halted):
        """★ 13:15 強制平倉必須執行——這是清單裡唯一會造成違約交割的路徑。"""
        import main
        with patch.object(main, "ForceCloseJob") as Job:
            Job.return_value.run.return_value = []
            main._run_force_close_job(api=MagicMock(), db_path=":memory:")
        assert Job.return_value.run.called, "HALT 時 13:15 強平仍必須執行"

    def test_dt_poll_runs(self, halted):
        """5 分鐘出場輪詢（停損／停利）不得被 HALT 擋住。"""
        import main
        from daytrading_config import DaytradingConfig
        cfg = DaytradingConfig(paper_trade_only=False)
        with patch.object(main, "_dt_poll_tick", return_value=[]) as tick:
            main._maybe_dt_poll(
                datetime(2026, 9, 3, 10, 30), MagicMock(), cfg,
                {"last_poll": None},
            )
        assert tick.called, "HALT 時出場輪詢仍必須執行"

    def test_exit_warning_runs(self, halted):
        import main
        with patch("daytrading_monitor.load_daytrading_positions",
                   return_value=[]) as lp:
            main._exit_warning(MagicMock())
        assert lp.called


# ══════════════════════════════════════════════════════════════════════════════
# ★ 核心三：主迴圈不得再有全域擋
# ══════════════════════════════════════════════════════════════════════════════

class TestNoGlobalGateInLoop:
    """這是結構測試，刻意檢查原始碼。

    理由：主迴圈在 main() 內、無法單獨呼叫，但「把 is_halted 放回迴圈頂端」
    正是我們要防的那一次回歸——它會靜默地把所有出場與複盤一起關掉，而且
    任何單元測試都抓不到。與其沒有防護，不如用一條說明清楚的結構斷言。
    """

    def _loop_source(self) -> str:
        import main as main_mod
        # 用模組的 __file__ 而非相對路徑：pytest 的工作目錄不保證是 repo 根目錄
        src = Path(main_mod.__file__).read_text()
        start = src.index("while _RUNNING:")
        end = src.index("if t.hour == 8 and t.minute == 30")
        return src[start:end]

    def _executable_lines(self) -> str:
        """去掉註解行再檢查——說明這段歷史的註解本來就該留著。"""
        return "\n".join(
            ln for ln in self._loop_source().splitlines()
            if not ln.strip().startswith("#")
        )

    def test_no_halt_gate_before_job_dispatch(self):
        code = self._executable_lines()
        assert "is_halted" not in code, (
            "主迴圈頂端不得再檢查 is_halted——那會連停損、13:15 強制平倉、"
            "13:35 複盤一起擋掉。HALT 的閘門在 executor.place_stock_order。"
        )

    def test_comment_still_documents_why(self):
        """把「為什麼移除」留在原地，否則下一個人很可能又加回去。"""
        assert "禁買閘門" in self._loop_source()

    def test_trading_day_gate_still_present(self):
        """非交易日仍應跳過——不要把這個一起拿掉了。"""
        assert "is_trading_day" in self._loop_source()


class TestNoPointlessBuyPrompts:
    """Guard 0 已保證買單被擋，但若不提早跳過，HALT 期間仍會照常推播
    「要買嗎」的 Telegram 確認——使用者按下確認之後才發現訂單被擋，很困惑。
    """

    def test_auto_buy_skipped_while_halted(self, halted):
        import main
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import DaytradingPosition

        pos = DaytradingPosition(code="2330", name="台積電",
                                 entry_low=100.0, entry_high=101.0,
                                 target_price=110.0, stop_loss=97.0,
                                 dt_score=8, ai_summary="")
        with patch("executor.place_stock_order") as order, \
             patch.object(main, "TELEGRAM_CHAT_ID", ""):
            main._auto_buy_dt_positions(
                MagicMock(), [pos], DaytradingConfig(), db_path=":memory:",
            )
        assert not order.called, "HALT 時不應進入自動買進流程"

    def test_auto_buy_proceeds_when_not_halted(self, tmp_path, monkeypatch):
        """釘住前提：沒有 HALT 時這條路徑本來會走下去（否則上一條測試沒意義）。"""
        import halt as halt_mod
        monkeypatch.setattr(halt_mod, "_HALT_FILE", tmp_path / "HALT")
        import main
        from daytrading_config import DaytradingConfig
        from daytrading_monitor import DaytradingPosition

        pos = DaytradingPosition(code="2330", name="台積電",
                                 entry_low=100.0, entry_high=101.0,
                                 target_price=110.0, stop_loss=97.0,
                                 dt_score=8, ai_summary="")
        with patch("daytrading_monitor.load_daytrading_positions",
                   return_value=[pos]) as load, \
             patch("daytrading_monitor.fetch_current_price", return_value=100.5), \
             patch("executor.place_stock_order"), \
             patch.object(main, "TELEGRAM_CHAT_ID", ""):
            main._auto_buy_dt_positions(
                MagicMock(), [pos], DaytradingConfig(), db_path=":memory:",
            )
        assert load.called, "無 HALT 時應正常進入買進流程"


class TestHaltDocstringHonesty:
    def test_halt_does_not_claim_to_stop_monitor_agent(self):
        """halt() 的 docstring 寫「stop MonitorAgent if running」，但它只寫檔案。

        而且那句話描述的行為本身就是錯的：停掉 tick 監控等於再犯一次
        「鎖住逃生門」——tick 路徑正是停損的執行者之一。
        """
        import halt as halt_mod
        text = (halt_mod.__doc__ or "") + (halt_mod.halt.__doc__ or "")
        assert "stop MonitorAgent" not in text
