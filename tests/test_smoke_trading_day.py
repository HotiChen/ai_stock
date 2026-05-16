"""
tests/test_smoke_trading_day.py
═══════════════════════════════
交易日核心流程 smoke test — 四個時間槽順序執行

  08:30  PremarketJob  → 選股 → 風控 → 存 DB（daily_plans）
  09:00  MarketOpenJob → 讀 DB → 下單 → 存成交（daily_trades）
  13:25  ForceCloseJob → 讀持倉 → 平倉（lot_type 正確傳遞）
  13:35  PostMarketJob → 停監控 → 存日結（daily_summaries）

設計原則
────────
- 共用一個 tmp_path SQLite DB，所有 job 走真實 DB read/write
- 最小 mock：只 mock 外部 IO（Shioaji、Anthropic、Telegram、TWSE HTTP）
- 斷言前綴格式：[HH:MM JobName] 說明
  → 任何 AssertionError 直接指向是哪個時間槽的哪個資料契約壞掉

涵蓋的高風險場景
────────────────
1. 完整四槽串接 + sector / lot_type 資料契約驗證
2. 09:00 同股同向防重複下單
3. 13:25 零股（intraday_odd）平倉 → force_stop_loss 收到正確 lot_type
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest

from research_db import init_db, load_daily_plan, load_daily_trades, reject_all_picks_from_plan


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """每個 test 獨立的乾淨 SQLite DB。"""
    path = str(tmp_path / "smoke.db")
    init_db(path)
    return path


def _buy_analysis(confidence: int = 8, target: float = 900.0, stop: float = 800.0):
    """模擬 DeepAnalysis buy 訊號。"""
    m = MagicMock()
    m.signal        = "buy"
    m.confidence    = confidence
    m.target_price  = target
    m.stop_loss_price = stop
    return m


def _ok_result(code="2330", name="台積電", lot_type="common", order_id="ORD_SMOKE_001"):
    """模擬成功下單的 ExecutionResult。"""
    from executor import ExecutionResult
    return ExecutionResult(
        code=code, name=name, action="buy",
        quantity=1, price=850.0, amount=850_000.0,
        lot_type=lot_type, success=True,
        order_id=order_id, reason="",
    )


# ── Smoke Test 1：完整四槽串接 ────────────────────────────────────────────────

class TestTradingDayFourSlots:
    """
    四個時間槽端對端 smoke test。
    共用同一個 DB：PremarketJob 存的 picks 被 MarketOpenJob 讀取；
    MarketOpenJob 存的 trades 被 ForceCloseJob 讀取。
    """

    @patch("main.MonitorAgent")
    @patch("main.force_stop_loss")
    @patch("main.place_stock_order")
    @patch("main.send_confirmation")
    @patch("risk_guard.check_ex_dividend", return_value=False)
    @patch("main.run_deep_analysis")
    def test_full_trading_day_0830_to_1335(
        self,
        mock_deep,
        _mock_exdiv,
        mock_confirm,
        mock_place,
        mock_force,
        mock_monitor_cls,
        db,
    ):
        """
        08:30 → 09:00 → 13:25 → 13:35 完整交易日流程。
        斷言前綴指出失敗所在時間槽與資料契約欄位。
        """
        from main import (
            PremarketJob, MarketOpenJob, ForceCloseJob, PostMarketJob,
        )
        from research_db import load_daily_summaries

        # ═══════════════════════════════════════════════════════
        # 08:30  PremarketJob — 選股 + 風控 + 存 daily_plans
        # ═══════════════════════════════════════════════════════
        mock_deep.return_value = _buy_analysis(confidence=8)
        mock_confirm.return_value = 1          # Telegram message_id

        candidates = [
            {"code": "2330", "name": "台積電", "close": 850.0,
             "change_rate": 1.5, "analysis": "強勢", "sector": "半導體"},
        ]

        premarket = PremarketJob(
            candidates=candidates,
            capital=100_000.0,
            db_path=db,
            telegram_chat_id="12345",
            current_positions=[],
        )
        approved = premarket.run(market_summary="穩定", theme_info="AI")

        # ── 08:30 Assertions ──────────────────────────────────
        picks_in_db = load_daily_plan(date.today(), db)
        assert len(picks_in_db) > 0, \
            "[08:30 PremarketJob] 盤前選股未寫入 DB — daily_plans 為空"
        assert picks_in_db[0]["code"] == "2330", \
            "[08:30 PremarketJob] DB 中的股票代號與輸入不符"
        assert "sector" in picks_in_db[0], \
            "[08:30 PremarketJob] 資料契約缺 sector 欄位 — risk_guard 需要它"
        assert picks_in_db[0]["sector"] == "半導體", \
            "[08:30 PremarketJob] sector 值未從候選股正確傳遞"
        assert "budget" in picks_in_db[0], \
            "[08:30 PremarketJob] 資料契約缺 budget 欄位 — MarketOpenJob 需要它"

        # ═══════════════════════════════════════════════════════
        # 09:00  MarketOpenJob — 讀 DB → 下單 → 存 daily_trades
        # ═══════════════════════════════════════════════════════
        mock_place.return_value = _ok_result(lot_type="common")
        mock_monitor = MagicMock()
        mock_monitor_cls.return_value = mock_monitor

        # Shioaji api mock：snapshot 回傳股價
        api = MagicMock()
        snap = MagicMock()
        snap.close = 850.0
        api.snapshots.return_value = [snap]
        api.list_trades.return_value = []

        market_open = MarketOpenJob(
            api=api,
            approved_picks=approved,
            db_path=db,
            telegram_chat_id="12345",
            hard_limit=500_000,
            prior_orders=[],      # 今日無先前委託
        )
        monitor = market_open.run()

        # ── 09:00 Assertions ──────────────────────────────────
        trades = load_daily_trades(date.today(), db)
        assert len(trades) == 1, (
            f"[09:00 MarketOpenJob] daily_trades 應有 1 筆成交，實際 {len(trades)} 筆"
        )
        t = trades[0]
        assert t["code"] == "2330", \
            "[09:00 MarketOpenJob] 成交代號不符"
        assert t["action"] == "buy", \
            "[09:00 MarketOpenJob] action 應為 buy"
        assert t["lot_type"] == "common", \
            "[09:00 MarketOpenJob] 資料契約：lot_type 未從 ExecutionResult 正確存入 DB"
        assert t["sector"] == "半導體", \
            "[09:00 MarketOpenJob] 資料契約：sector 未從 pick 正確存入 DB"
        assert t["note"].startswith("id="), \
            "[09:00 MarketOpenJob] note 格式異常（應為 id=... 而非 lot=... 混合）"
        assert mock_monitor.start.called, \
            "[09:00 MarketOpenJob] MonitorAgent 未啟動"

        # ═══════════════════════════════════════════════════════
        # 13:25  ForceCloseJob — 讀持倉 → 平倉 → 寫 sell trade
        # ═══════════════════════════════════════════════════════
        mock_force.return_value = True

        force_close = ForceCloseJob(api=api, db_path=db)
        results = force_close.run()

        # ── 13:25 Assertions ──────────────────────────────────
        assert len(results) == 1, (
            f"[13:25 ForceCloseJob] 應平倉 1 筆持倉，實際處理 {len(results)} 筆"
        )
        assert results[0]["success"] is True, \
            "[13:25 ForceCloseJob] 平倉回傳 success=False，強制停損失敗"
        assert results[0]["code"] == "2330", \
            "[13:25 ForceCloseJob] 平倉代號不符"
        mock_force.assert_called_once()
        force_kwargs = mock_force.call_args[1]
        assert force_kwargs["code"] == "2330", \
            "[13:25 ForceCloseJob] force_stop_loss 收到的 code 不符"
        assert force_kwargs["lot_type"] == "common", \
            "[13:25 ForceCloseJob] 資料契約：lot_type 應從 daily_trades 傳遞給 force_stop_loss"

        # Simulation 模式下平倉成交有保證 → 寫入 action='sell' + computed PnL；
        # 不應有 force_close_requested（那是真實模式的「待確認」語義）。
        all_trades = load_daily_trades(date.today(), db)
        confirmed_sells = [t for t in all_trades if t["action"] == "sell"]
        assert len(confirmed_sells) == 1, \
            "[13:25 ForceCloseJob] simulation 模式下平倉應寫入 1 筆 action='sell' 已確認記錄"
        assert confirmed_sells[0]["code"] == "2330", \
            "[13:25 ForceCloseJob] sell 記錄的 code 不符"
        assert confirmed_sells[0]["pnl"] is not None, \
            "[13:25 ForceCloseJob] simulation 模式 sell 記錄應有 pnl 數值"
        pending_closes = [t for t in all_trades if t["action"] == "force_close_requested"]
        assert len(pending_closes) == 0, \
            "[13:25 ForceCloseJob] simulation 模式不應有 force_close_requested 記錄"

        # ═══════════════════════════════════════════════════════
        # 13:35  PostMarketJob — 停監控 + 存日結（PnL 來自 DB）
        # ═══════════════════════════════════════════════════════
        post_market = PostMarketJob(
            monitor=mock_monitor,
            db_path=db,
            execution_id="EX_SMOKE_001",
        )
        # 不傳 total_pnl — PostMarketJob 必須自行從 daily_trades 計算
        total_pnl = post_market.run(trades_summary="買入台積電 1 張")

        # ── 13:35 Assertions ──────────────────────────────────
        mock_monitor.stop.assert_called_once(), \
            "[13:35 PostMarketJob] MonitorAgent.stop() 未被呼叫"
        summaries = load_daily_summaries("EX_SMOKE_001", db)
        assert len(summaries) == 1, \
            "[13:35 PostMarketJob] 日結未寫入 DB — daily_summaries 為空"
        # force_close_requested 不計入 realized pnl → PnL=0.0（保守處理）
        assert summaries[0].total_pnl == pytest.approx(total_pnl), \
            "[13:35 PostMarketJob] DB 儲存的 PnL 與 run() 回傳值不一致"
        # buy=850.0, snapshot close=850.0 → pnl=(850-850)×1×1000=0
        assert total_pnl == pytest.approx(0.0), \
            "[13:35 PostMarketJob] buy price == close price → force_close pnl=0.0"


# ── Smoke Test 2：09:00 防重複下單 ────────────────────────────────────────────

class TestMarketOpenDuplicateOrderSmoke:
    """
    同股同向同交易日 → 第二次嘗試下單應被 executor.is_duplicate_order 攔截。

    注意：不 mock place_stock_order，讓真實執行邏輯（含 is_duplicate_order）運行。
    只 mock Shioaji API 物件，讓第一次成功落單，第二次被防重複攔截。
    """

    @patch("main.MonitorAgent")
    def test_0900_same_stock_same_action_blocked_on_second_run(
        self, mock_monitor_cls, db
    ):
        """
        [09:00 MarketOpenJob] 盤中若再次觸發 MarketOpenJob（如重啟），
        prior_orders 帶有今日已下單記錄，executor.is_duplicate_order 必須攔截。

        策略：不 mock place_stock_order → is_duplicate_order 真實跑。
              mock Shioaji api → 不需要真實連線。
        """
        from main import MarketOpenJob

        mock_monitor_cls.return_value = MagicMock()

        # Shioaji API mock：回傳假合約 + 假委託
        api = MagicMock()
        snap = MagicMock(); snap.close = 850.0
        api.snapshots.return_value = [snap]
        contract = MagicMock()
        api.Contracts.Stocks.get.return_value = contract
        trade_resp = MagicMock()
        trade_resp.order.id = "ORD_FIRST"
        api.place_order.return_value = trade_resp

        picks = [{"code": "2330", "name": "台積電", "budget": 5_000.0,
                  "sector": "半導體", "signal": "buy", "confidence": 8}]

        # 09:00 以 DB 為單一真相來源：把計劃先存入 DB
        from research_db import save_daily_plan
        save_daily_plan(date.today(), picks, db)

        # 第一次執行：prior_orders 空 → 應該下單
        with patch("notifier.notify_order_success"):  # 不需要通知
            MarketOpenJob(
                api=api, approved_picks=picks, db_path=db,
                telegram_chat_id=None, hard_limit=500_000, prior_orders=[],
            ).run()

        first_place_count = api.place_order.call_count
        assert first_place_count == 1, (
            f"[09:00 MarketOpenJob] 第一次執行應下單 1 次，實際 {first_place_count} 次"
        )

        # 第二次執行：prior_orders 包含今日已下的 2330 買單
        prior = [{"code": "2330", "action": "buy",
                  "date": date.today().isoformat(),
                  "quantity": 1, "price": 850.0}]
        api.place_order.reset_mock()

        with patch("notifier.notify_order_success"):
            MarketOpenJob(
                api=api, approved_picks=picks, db_path=db,
                telegram_chat_id=None, hard_limit=500_000, prior_orders=prior,
            ).run()

        second_place_count = api.place_order.call_count
        assert second_place_count == 0, (
            "[09:00 MarketOpenJob / is_duplicate_order] 防重複下單失效："
            "同股同向今日已下單（prior_orders 含 date），"
            f"第二次執行仍呼叫了 Shioaji place_order {second_place_count} 次"
        )


# ── Smoke Test 3：13:25 零股平倉 lot_type 正確傳遞 ───────────────────────────

class TestForceCloseIntradayOddSmoke:
    """
    零股（intraday_odd）持倉 → ForceCloseJob 必須傳 lot_type='intraday_odd'
    給 force_stop_loss，否則 Shioaji 會用整股通道拒單。
    """

    @patch("main.force_stop_loss")
    def test_1325_intraday_odd_position_uses_correct_lot_type(
        self, mock_force, db
    ):
        """
        [13:25 ForceCloseJob] 零股持倉平倉 — lot_type 必須為 intraday_odd，
        不得 fallback 成 common（否則 Shioaji 拒單，停損失效）。
        """
        from main import ForceCloseJob
        from research_db import save_daily_trade

        # 先寫入一筆零股買單
        save_daily_trade({
            "trade_date": date.today(),
            "code": "2454", "name": "聯發科",
            "action": "buy",
            "quantity": 100,           # 100 股（非整張）
            "price": 750.0,
            "amount": 75_000.0,
            "pnl": None,
            "lot_type": "intraday_odd",
            "sector": "IC設計",
            "note": "id=ORD_ODD_001",
        }, db)

        mock_force.return_value = True
        ForceCloseJob(api=MagicMock(), db_path=db).run()

        mock_force.assert_called_once()
        force_kwargs = mock_force.call_args[1]

        assert force_kwargs["code"] == "2454", \
            "[13:25 ForceCloseJob] 平倉代號不符"
        assert force_kwargs["lot_type"] == "intraday_odd", (
            "[13:25 ForceCloseJob] lot_type 資料契約斷裂："
            f"預期 intraday_odd，實際 {force_kwargs.get('lot_type', '(缺失)')}"
            " — ForceCloseJob 未從 daily_trades.lot_type 正確讀取"
        )
        assert force_kwargs["quantity"] == 100, \
            "[13:25 ForceCloseJob] 零股數量不符"

    @patch("main.force_stop_loss")
    def test_1325_mixed_positions_correct_lot_types(self, mock_force, db):  # noqa: E303
        """
        [13:25 ForceCloseJob] 整股 + 零股混合持倉，各自收到正確 lot_type。
        """
        from main import ForceCloseJob
        from research_db import save_daily_trade

        for code, lot_type, qty in [
            ("2330", "common",       1000),
            ("2454", "intraday_odd",  100),
        ]:
            save_daily_trade({
                "trade_date": date.today(),
                "code": code, "name": code,
                "action": "buy",
                "quantity": qty, "price": 100.0, "amount": qty * 100.0,
                "pnl": None,
                "lot_type": lot_type,
                "sector": "半導體",
                "note": f"id=ORD_{code}",
            }, db)

        mock_force.return_value = True
        ForceCloseJob(api=MagicMock(), db_path=db).run()

        assert mock_force.call_count == 2, \
            "[13:25 ForceCloseJob] 混合持倉應平倉 2 筆"

        calls_by_code = {c[1]["code"]: c[1]["lot_type"]
                         for c in mock_force.call_args_list}
        assert calls_by_code["2330"] == "common", \
            "[13:25 ForceCloseJob] 整股 2330 的 lot_type 不應被改動"
        assert calls_by_code["2454"] == "intraday_odd", \
            "[13:25 ForceCloseJob] 零股 2454 的 lot_type 不應 fallback 成 common"


# ── Smoke Test 4：reject_all 後 09:00 不下單 ────────────────────────────────

class TestMarketOpenRejectAllSmoke:
    """
    08:30 有 approved picks → DB 被使用者 reject_all 清空 → 09:00 不下任何單

    驗證 DB 是 09:00 的唯一真相來源，memory fallback 不得影響下單決策。
    """

    @patch("main.MonitorAgent")
    @patch("main.place_stock_order")
    @patch("main.send_confirmation")
    @patch("risk_guard.check_ex_dividend", return_value=False)
    @patch("main.run_deep_analysis")
    def test_0900_zero_orders_after_reject_all(
        self,
        mock_deep,
        _mock_exdiv,
        mock_confirm,
        mock_place,
        mock_monitor_cls,
        db,
    ):
        """
        [08:30] PremarketJob 選到 2330 並寫入 DB
        [Telegram] 使用者按 reject_all → DB 清空為 []
        [09:00] MarketOpenJob 讀 DB → [] → 不呼叫 place_stock_order

        要點：MarketOpenJob 的 approved_picks（memory）仍帶有舊 picks，
              但必須以 DB 狀態（[]）為準，不得 fallback。
        """
        from main import PremarketJob, MarketOpenJob

        mock_deep.return_value = _buy_analysis(confidence=8)
        mock_confirm.return_value = 1
        mock_monitor_cls.return_value = MagicMock()
        mock_place.return_value = MagicMock(success=True)

        candidates = [
            {"code": "2330", "name": "台積電", "close": 850.0,
             "change_rate": 1.5, "analysis": "強勢", "sector": "半導體"},
        ]

        # ── 08:30 PremarketJob ────────────────────────────────────
        premarket = PremarketJob(
            candidates=candidates,
            capital=100_000.0,
            db_path=db,
            telegram_chat_id="12345",
            current_positions=[],
        )
        approved = premarket.run(market_summary="穩定", theme_info="AI")

        picks_before = load_daily_plan(date.today(), db)
        assert len(picks_before) > 0, \
            "[08:30] 盤前選股應寫入 DB，此測試前提不成立"

        # ── Telegram：使用者按 reject_all ─────────────────────────
        reject_all_picks_from_plan(date.today(), db)

        picks_after = load_daily_plan(date.today(), db)
        assert picks_after == [], \
            "[reject_all] DB 應已清空為 []"

        # ── 09:00 MarketOpenJob ───────────────────────────────────
        api = MagicMock()
        snap = MagicMock()
        snap.close = 850.0
        api.snapshots.return_value = [snap]

        market_open = MarketOpenJob(
            api=api,
            approved_picks=approved,   # stale memory picks — must be ignored
            db_path=db,
            telegram_chat_id=None,
            hard_limit=500_000,
            prior_orders=[],
        )
        market_open.run()

        mock_place.assert_not_called(), (
            "[09:00 MarketOpenJob] reject_all 後 DB 為 []，"
            "不應因 memory fallback 而呼叫 place_stock_order"
        )
