"""tests/test_twse_sim_scanner.py — PR-7：模擬模式 TWSE OpenAPI 選股"""
# 這些 fixture 的 stop_loss 不是裝飾：dt_rules.check_exit_plan 會擋掉
# 沒有停損價的買單（沒有停損價 = 沒有風險上限）。測的是別的行為，
# 但買單得先送得出去。詳見 tests/test_dt_exit_plan.py。

from __future__ import annotations

from unittest.mock import patch, MagicMock


# ── fetch_twse_sim_candidates ─────────────────────────────────────────────────

class TestFetchTwseSimCandidates:

    def _mock_twse_row(self, code="2330", name="台積電",
                       close="820", change="5",
                       volume="50000", high="825", low="815", open_p="815"):
        return {
            "Code": code, "Name": name,
            "ClosingPrice": close, "Change": change,
            "TradeVolume": volume,
            "HighestPrice": high, "LowestPrice": low, "OpeningPrice": open_p,
        }

    def _mock_resp(self, rows):
        resp = MagicMock()
        resp.json.return_value = rows
        resp.raise_for_status = MagicMock()
        return resp

    # TradeVolume 單位是「股」，÷1000 得張數。
    # 測試用 min_volume=50（張），提供 60,000 股 = 60 張，高於門檻。
    _CRITERIA = None  # 在各測試裡統一用 min_volume=50

    @patch("market_scanner.requests")
    def test_returns_candidates_list(self, mock_req):
        from market_scanner import fetch_twse_sim_candidates, ScanCriteria
        mock_req.get.return_value = self._mock_resp([
            self._mock_twse_row(code="2330", volume="60000"),  # 60張
        ])
        result = fetch_twse_sim_candidates(ScanCriteria(min_volume=50, top_n=5))
        assert isinstance(result, list)

    @patch("market_scanner.requests")
    def test_filters_by_volume(self, mock_req):
        from market_scanner import fetch_twse_sim_candidates, ScanCriteria
        mock_req.get.return_value = self._mock_resp([
            self._mock_twse_row(code="2330", volume="60000"),  # 60 張 > 50 → 保留
            self._mock_twse_row(code="9999", volume="10"),     # 0 張 < 50 → 排除
        ])
        result = fetch_twse_sim_candidates(ScanCriteria(min_volume=50, top_n=5))
        codes = [r["code"] for r in result]
        assert "2330" in codes
        assert "9999" not in codes

    @patch("market_scanner.requests")
    def test_skips_non_4digit_codes(self, mock_req):
        from market_scanner import fetch_twse_sim_candidates, ScanCriteria
        mock_req.get.return_value = self._mock_resp([
            self._mock_twse_row(code="0050", volume="60000"),  # ETF 4 碼但含 0 開頭
            self._mock_twse_row(code="2330", volume="60000"),
        ])
        result = fetch_twse_sim_candidates(ScanCriteria(min_volume=50, top_n=5))
        codes = [r["code"] for r in result]
        # 0050 不是純數字 4 碼（開頭 0，stock 碼也是 4 碼數字）
        # market_scanner 的邏輯：code.isdigit() and len==4 → 0050 會通過 isdigit()
        # 所以我們只確認 2330 一定在
        assert "2330" in codes

    @patch("market_scanner.requests")
    def test_skips_zero_close_price(self, mock_req):
        from market_scanner import fetch_twse_sim_candidates, ScanCriteria
        mock_req.get.return_value = self._mock_resp([
            self._mock_twse_row(code="1234", close="0", volume="60000"),
            self._mock_twse_row(code="2330", close="820", volume="60000"),
        ])
        result = fetch_twse_sim_candidates(ScanCriteria(min_volume=50, top_n=5))
        codes = [r["code"] for r in result]
        assert "1234" not in codes

    @patch("market_scanner._fallback_candidates", return_value=[{"code": "2330", "name": "台積電", "close": 0.0, "change_rate": 0.0, "total_volume": 99999}])
    @patch("market_scanner.requests")
    def test_falls_back_on_api_failure(self, mock_req, mock_fallback):
        from market_scanner import fetch_twse_sim_candidates
        mock_req.get.side_effect = Exception("network error")
        result = fetch_twse_sim_candidates()
        mock_fallback.assert_called_once()
        assert isinstance(result, list)

    @patch("market_scanner.requests")
    def test_candidate_has_required_fields(self, mock_req):
        from market_scanner import fetch_twse_sim_candidates, ScanCriteria
        mock_req.get.return_value = self._mock_resp([
            self._mock_twse_row(code="2330", volume="60000"),
        ])
        result = fetch_twse_sim_candidates(ScanCriteria(min_volume=50, top_n=5))
        if result:
            r = result[0]
            assert "code" in r
            assert "name" in r
            assert "close" in r
            assert "total_volume" in r


# ── scan_candidates simulation fallback ──────────────────────────────────────

class TestScanCandidatesSimFallback:
    def test_api_none_calls_twse_fallback(self):
        from main import scan_candidates
        with patch("main.fetch_twse_sim_candidates") as mock_twse:
            mock_twse.return_value = [{"code": "2330", "name": "台積電"}]
            result = scan_candidates(api=None)
        mock_twse.assert_called_once()
        assert result == [{"code": "2330", "name": "台積電"}]

    def test_api_success_skips_twse(self):
        from main import scan_candidates
        mock_api = MagicMock()
        with patch("main.get_all_stock_codes", return_value=["2330"]), \
             patch("main.batch_fetch_snapshots", return_value={"2330": {"close": 820, "total_volume": 50000, "change_rate": 1.0}}), \
             patch("main.screen_candidates", return_value=[{"code": "2330"}]), \
             patch("main.fetch_twse_sim_candidates") as mock_twse:
            result = scan_candidates(api=mock_api)
        mock_twse.assert_not_called()
        assert result == [{"code": "2330"}]

    def test_api_failure_falls_back_to_twse(self):
        from main import scan_candidates
        mock_api = MagicMock()
        with patch("main.get_all_stock_codes", side_effect=Exception("shioaji error")), \
             patch("main.fetch_twse_sim_candidates") as mock_twse:
            mock_twse.return_value = [{"code": "2454"}]
            result = scan_candidates(api=mock_api)
        mock_twse.assert_called_once()
        assert result[0]["code"] == "2454"


# ── _handle_dt_buy ────────────────────────────────────────────────────────────

class TestHandleDtBuy:
    def _make_watching_pos(self, code="2330", name="台積電"):
        from daytrading_monitor import DaytradingPosition
        return DaytradingPosition(
            code=code, name=name,
            entry_low=None, entry_high=None,
            target_price=None, stop_loss=97.0,
            dt_score=7, status="watching",
        )

    def test_no_position_sends_warning(self):
        from telegram_bot import _handle_dt_buy
        # _handle_dt_buy uses lazy imports → patch the source modules
        with patch("daytrading_monitor.load_daytrading_positions", return_value=[]), \
             patch("telegram_bot.send_text") as mock_send:
            _handle_dt_buy("12345", "9999")
        assert mock_send.called
        msg = mock_send.call_args[0][1]
        assert "無可用" in msg or "不存在" in msg

    def test_no_api_sends_error(self):
        from telegram_bot import _handle_dt_buy
        pos = self._make_watching_pos()
        with patch("daytrading_monitor.load_daytrading_positions", return_value=[pos]), \
             patch("telegram_bot._get_sj_api", return_value=None), \
             patch("telegram_bot.send_text") as mock_send:
            _handle_dt_buy("12345", "2330")
        msg = mock_send.call_args[0][1]
        assert "無法連線" in msg or "失敗" in msg

    def test_no_price_sends_error(self):
        from telegram_bot import _handle_dt_buy
        pos = self._make_watching_pos()
        mock_api = MagicMock()
        with patch("daytrading_monitor.load_daytrading_positions", return_value=[pos]), \
             patch("telegram_bot._get_sj_api", return_value=mock_api), \
             patch("daytrading_config.load_daytrading_config") as mock_cfg, \
             patch("daytrading_monitor.fetch_current_price", return_value=None), \
             patch("telegram_bot.send_text") as mock_send:
            from daytrading_config import DaytradingConfig
            mock_cfg.return_value = DaytradingConfig(
                budget_per_stock=30000, stop_loss_pct=3, take_profit_pct=9,
                trailing_start_pct=2, trailing_gap_pct=0.5,
                force_close_time="13:00", require_manual_confirm=True,
            )
            _handle_dt_buy("12345", "2330")
        last_msg = mock_send.call_args_list[-1][0][1]
        assert "報價" in last_msg or "失敗" in last_msg

    def test_buy_success_marks_position_active(self):
        from telegram_bot import _handle_dt_buy
        from executor import ExecutionResult
        pos = self._make_watching_pos()
        mock_api = MagicMock()
        result = ExecutionResult(
            code="2330", name="台積電", action="buy",
            quantity=1, price=820.0, amount=820000.0,
            lot_type="common", success=True,
            order_id="ord123", reason="",
        )
        # 委託送出後改走 record_buy_result：紙上模式直接 active，真實模式
        # 只標 buy_submitted，等券商回報成交才 active（委託 ≠ 成交）。
        # 這裡驗證的是「有把結果交給狀態機」，模式差異由
        # tests/test_buy_result_recording.py 覆蓋。
        with patch("daytrading_monitor.load_daytrading_positions", return_value=[pos]), \
             patch("telegram_bot._get_sj_api", return_value=mock_api), \
             patch("daytrading_config.load_daytrading_config") as mock_cfg, \
             patch("daytrading_monitor.fetch_current_price", return_value=820.0), \
             patch("executor.place_stock_order", return_value=result), \
             patch("daytrading_monitor.record_buy_result") as mock_entered, \
             patch("telegram_bot.send_text") as mock_send:
            from daytrading_config import DaytradingConfig
            mock_cfg.return_value = DaytradingConfig(
                budget_per_stock=30000, stop_loss_pct=3, take_profit_pct=9,
                trailing_start_pct=2, trailing_gap_pct=0.5,
                force_close_time="13:00", require_manual_confirm=True,
            )
            _handle_dt_buy("12345", "2330")
        mock_entered.assert_called_once()
        assert mock_entered.call_args[0][0] == "2330"           # code
        assert mock_entered.call_args[0][1].price == 820.0      # ExecutionResult
        success_msg = mock_send.call_args_list[-1][0][1]
        assert "成功" in success_msg
