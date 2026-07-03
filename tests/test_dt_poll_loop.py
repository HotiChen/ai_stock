"""tests/test_dt_poll_loop.py — Task 1/3/9: 主迴圈輪詢出場、watchlist 刷新、分支防護.

Task 1: 5 分鐘輪詢出場路徑（run_daytrading_monitor → _run_dt_sell_alerts）
Task 3: tick 監控 watchlist 定期從 daytrading_positions.json 重載
Task 9: 13:25 ForceCloseJob / 13:35 PostMarketJob 包 try/except + 強平告警
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


def _seed_active(path, code="2330", entry_price=100.0, quantity=1000):
    from daytrading_monitor import DaytradingPosition, save_daytrading_positions
    save_daytrading_positions([
        DaytradingPosition(
            code=code, name="台積電", entry_low=None, entry_high=None,
            target_price=None, stop_loss=None, dt_score=90, status="active",
            entry_price=entry_price, peak_price=entry_price,
            quantity=quantity, lot_type="common",
        ),
    ], path=path)


def _cfg(**kw):
    from daytrading_config import DaytradingConfig
    base = dict(force_close_time="00:00", paper_trade_only=False)
    base.update(kw)
    return DaytradingConfig(**base)


# ── Task 1: 5 分鐘輪詢出場路徑 ────────────────────────────────────────────────

class TestMaybeDtPoll:
    def test_active_force_close_calls_sell_and_closes(self, tmp_path):
        """(a) active 持倉觸發 force_close 時間規則 → _run_dt_sell_alerts 被呼叫、
        賣單執行、position 轉 closed。"""
        import main
        from daytrading_monitor import load_daytrading_positions
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)   # force_close_time="00:00" → 任何盤中時間都觸發
        state = {"last_poll": None}
        now = datetime(2026, 7, 3, 10, 0, 0)   # 交易時段內

        with patch("daytrading_monitor.fetch_current_price", return_value=101.0), \
             patch("main._run_dt_sell_alerts", wraps=main._run_dt_sell_alerts) as spy_alerts, \
             patch("main.force_stop_loss", return_value=True) as mock_sell:
            sells = main._maybe_dt_poll(now, MagicMock(), _cfg(), state, dt_path=pos_path)

        assert spy_alerts.called                     # _run_dt_sell_alerts 接上了
        assert mock_sell.called                      # 出場賣單被執行
        assert sells and sells[0].sell_required
        assert sells[0].alert_type == "force_close"
        positions = {p.code: p for p in load_daytrading_positions(path=pos_path)}
        assert positions["2330"].status == "closed"  # 部位轉 closed

    def test_no_sell_signal_no_sell_call(self, tmp_path):
        """未觸發任何出場規則時，不呼叫 _run_dt_sell_alerts。"""
        import main
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        state = {"last_poll": None}
        cfg = _cfg(force_close_time="13:00")   # 10:00 未達強平時間

        with patch("daytrading_monitor.fetch_current_price", return_value=100.5), \
             patch("main._run_dt_sell_alerts") as mock_alerts:
            sells = main._maybe_dt_poll(
                datetime(2026, 7, 3, 10, 0, 0), MagicMock(), cfg, state, dt_path=pos_path)

        assert sells == []
        assert not mock_alerts.called

    def test_throttle_no_repeat_within_5min(self, tmp_path):
        """(b) 5 分鐘內不重複執行。"""
        import main
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        state = {"last_poll": None}
        cfg = _cfg()

        with patch("main._dt_poll_tick", return_value=[]) as mock_tick:
            main._maybe_dt_poll(datetime(2026, 7, 3, 10, 0, 0), MagicMock(), cfg, state, dt_path=pos_path)
            # 2 分鐘後再呼叫 → 應被節流跳過
            main._maybe_dt_poll(datetime(2026, 7, 3, 10, 2, 0), MagicMock(), cfg, state, dt_path=pos_path)
        assert mock_tick.call_count == 1

    def test_throttle_runs_again_after_5min(self, tmp_path):
        import main
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        state = {"last_poll": None}
        cfg = _cfg()
        with patch("main._dt_poll_tick", return_value=[]) as mock_tick:
            main._maybe_dt_poll(datetime(2026, 7, 3, 10, 0, 0), MagicMock(), cfg, state, dt_path=pos_path)
            main._maybe_dt_poll(datetime(2026, 7, 3, 10, 5, 0), MagicMock(), cfg, state, dt_path=pos_path)
        assert mock_tick.call_count == 2

    def test_paper_mode_skips_path(self, tmp_path):
        """(c) 紙上模式不走輪詢出場路徑。"""
        import main
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        state = {"last_poll": None}
        with patch("main._dt_poll_tick") as mock_tick:
            result = main._maybe_dt_poll(
                datetime(2026, 7, 3, 10, 0, 0), MagicMock(),
                _cfg(paper_trade_only=True), state, dt_path=pos_path)
        assert result is None
        assert not mock_tick.called

    def test_outside_window_skips(self, tmp_path):
        import main
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        cfg = _cfg()
        with patch("main._dt_poll_tick") as mock_tick:
            # 09:14 尚未到 09:15 輪詢起點；13:31 已過 13:30 終點
            main._maybe_dt_poll(datetime(2026, 7, 3, 9, 14, 0), MagicMock(), cfg,
                                {"last_poll": None}, dt_path=pos_path)
            main._maybe_dt_poll(datetime(2026, 7, 3, 13, 31, 0), MagicMock(), cfg,
                                {"last_poll": None}, dt_path=pos_path)
        assert not mock_tick.called

    def test_boundary_times_run(self, tmp_path):
        """09:15 / 13:30 邊界時間應執行。"""
        import main
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        cfg = _cfg()
        with patch("main._dt_poll_tick", return_value=[]) as mock_tick:
            main._maybe_dt_poll(datetime(2026, 7, 3, 9, 15, 0), MagicMock(), cfg,
                                {"last_poll": None}, dt_path=pos_path)
            main._maybe_dt_poll(datetime(2026, 7, 3, 13, 30, 0), MagicMock(), cfg,
                                {"last_poll": None}, dt_path=pos_path)
        assert mock_tick.call_count == 2

    def test_reloads_positions_from_json_each_pass(self, tmp_path):
        """跨 process 同步：每輪都從 JSON 重新載入持倉（Telegram process 寫入的
        active 持倉能被本 process 的輪詢看到並出場）。"""
        import main
        from daytrading_monitor import load_daytrading_positions
        pos_path = str(tmp_path / "pos.json")
        # 第一輪：JSON 尚無持倉 → 無警報
        from daytrading_monitor import save_daytrading_positions
        save_daytrading_positions([], path=pos_path)
        state = {"last_poll": None}
        with patch("daytrading_monitor.fetch_current_price", return_value=101.0), \
             patch("main.force_stop_loss", return_value=True):
            sells1 = main._maybe_dt_poll(
                datetime(2026, 7, 3, 10, 0, 0), MagicMock(), _cfg(), state, dt_path=pos_path)
            assert sells1 == []

            # 模擬另一個 process（Telegram）成交後寫入 active 持倉
            _seed_active(pos_path)

            sells2 = main._maybe_dt_poll(
                datetime(2026, 7, 3, 10, 6, 0), MagicMock(), _cfg(), state, dt_path=pos_path)

        assert sells2 and sells2[0].code == "2330"
        positions = load_daytrading_positions(path=pos_path)
        assert positions[0].status == "closed"


# ── Task 3: watchlist 刷新 ────────────────────────────────────────────────────

class TestRefreshWatchlist:
    def test_refresh_picks_up_json_changes(self, tmp_path):
        """(d) Telegram process 買入後（直接改 JSON 模擬），refresh 後 watchlist
        內 entry_price / quantity 更新。"""
        import main
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions
        pos_path = str(tmp_path / "pos.json")
        # 起始：watching，尚未進場（entry_price=None, quantity=0）—— 過期快照情境
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=90,
                               status="watching", entry_price=None, quantity=0),
        ], path=pos_path)

        agent = MagicMock()
        main._refresh_dt_watchlist(agent, dt_path=pos_path)
        first = agent.set_watchlist.call_args[0][0]
        assert first[0]["entry_price"] is None
        assert first[0]["quantity"] == 0

        # 模擬 Telegram process 成交：直接改 JSON 為 active + 實際進場價/數量
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="台積電", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=90,
                               status="active", entry_price=101.5, quantity=1000),
        ], path=pos_path)

        main._refresh_dt_watchlist(agent, dt_path=pos_path)
        second = agent.set_watchlist.call_args[0][0]
        assert second[0]["entry_price"] == 101.5
        assert second[0]["quantity"] == 1000

    def test_refresh_excludes_closed_and_skipped(self, tmp_path):
        import main
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions
        pos_path = str(tmp_path / "pos.json")
        save_daytrading_positions([
            DaytradingPosition(code="2330", name="A", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=90, status="active",
                               entry_price=100.0, quantity=1000),
            DaytradingPosition(code="2454", name="B", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=80, status="closed"),
            DaytradingPosition(code="2317", name="C", entry_low=None, entry_high=None,
                               target_price=None, stop_loss=None, dt_score=70, status="skipped"),
        ], path=pos_path)
        agent = MagicMock()
        watchlist = main._refresh_dt_watchlist(agent, dt_path=pos_path)
        codes = [w["code"] for w in watchlist]
        assert codes == ["2330"]

    def test_refresh_empty_watchlist_does_not_clobber_agent(self, tmp_path):
        """全部持倉出場後，不用空清單覆蓋 agent（避免 tick 監控清空）。"""
        import main
        from daytrading_monitor import save_daytrading_positions
        pos_path = str(tmp_path / "pos.json")
        save_daytrading_positions([], path=pos_path)
        agent = MagicMock()
        main._refresh_dt_watchlist(agent, dt_path=pos_path)
        assert not agent.set_watchlist.called

    def test_poll_tick_refreshes_agent(self, tmp_path):
        """_dt_poll_tick 每次輪詢先刷新 tick agent 的 watchlist。"""
        import main
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        agent = MagicMock()
        with patch("daytrading_monitor.fetch_current_price", return_value=100.5), \
             patch("main.force_stop_loss", return_value=True):
            main._dt_poll_tick(MagicMock(), _cfg(force_close_time="13:00"),
                               dt_agent=agent, dt_path=pos_path)
        assert agent.set_watchlist.called

    def test_poll_tick_agent_refresh_failure_does_not_block_exit_path(self, tmp_path):
        """watchlist 刷新失敗不得阻擋輪詢出場路徑。"""
        import main
        from daytrading_monitor import load_daytrading_positions
        pos_path = str(tmp_path / "pos.json")
        _seed_active(pos_path)
        agent = MagicMock()
        agent.set_watchlist.side_effect = RuntimeError("boom")
        with patch("daytrading_monitor.fetch_current_price", return_value=101.0), \
             patch("main.force_stop_loss", return_value=True):
            sells = main._dt_poll_tick(MagicMock(), _cfg(), dt_agent=agent, dt_path=pos_path)
        assert sells and sells[0].alert_type == "force_close"
        assert load_daytrading_positions(path=pos_path)[0].status == "closed"


# ── Task 9: 13:25 / 13:35 分支防護 ────────────────────────────────────────────

class TestBranchGuards:
    def test_force_close_job_raise_does_not_propagate(self):
        """(e) ForceCloseJob.run raise → 不拋出、發 Telegram 資金安全告警。"""
        import main
        boom = MagicMock()
        boom.run.side_effect = RuntimeError("boom")
        with patch("main.ForceCloseJob", return_value=boom), \
             patch("telegram_bot.send_text") as mock_send:
            main._run_force_close_job(MagicMock(), db_path=":memory:", chat_id="999")
        assert boom.run.called
        assert mock_send.called
        assert "強制平倉失敗" in mock_send.call_args[0][1]

    def test_force_close_alert_failure_still_swallowed(self):
        """告警本身失敗也不得拋出。"""
        import main
        boom = MagicMock()
        boom.run.side_effect = RuntimeError("boom")
        with patch("main.ForceCloseJob", return_value=boom), \
             patch("telegram_bot.send_text", side_effect=RuntimeError("tg down")):
            main._run_force_close_job(MagicMock(), db_path=":memory:", chat_id="999")

    def test_force_close_job_success_no_alert(self):
        import main
        ok = MagicMock()
        with patch("main.ForceCloseJob", return_value=ok), \
             patch("telegram_bot.send_text") as mock_send:
            main._run_force_close_job(MagicMock(), db_path=":memory:", chat_id="999")
        assert ok.run.called
        assert not mock_send.called

    def test_postmarket_job_raise_does_not_propagate(self):
        import main
        boom = MagicMock()
        boom.run.side_effect = RuntimeError("boom")
        with patch("main.PostMarketJob", return_value=boom):
            result = main._run_postmarket_job(None, ":memory:", "exec-1")
        assert result is None

    def test_postmarket_job_success_returns_pnl(self, tmp_path):
        import main
        from research_db import init_db
        db_path = str(tmp_path / "research.db")
        init_db(db_path)
        ok = MagicMock()
        ok.run.return_value = 1234.0
        with patch("main.PostMarketJob", return_value=ok), \
             patch("notifier.notify_market_close") as mock_notify:
            result = main._run_postmarket_job(None, db_path, "exec-1")
        assert result == 1234.0
        assert mock_notify.called
