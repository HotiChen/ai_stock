"""Tests for candidate_builder.build_candidates pipeline (H11)"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from candidate_builder import build_candidates, _fetch_yfinance_prices


# ── _fetch_yfinance_prices ─────────────────────────────────────────────────────

class TestFetchYfinancePrices:
    """yfinance fallback 函數的行為（yf 是函數內 lazy import，需 sys.modules patch）。"""

    def _make_yf_mock(self, prices: dict[str, float]):
        """建立回傳指定價格的 yfinance mock。"""
        mock_yf = MagicMock()
        mock_col = MagicMock()
        mock_col.dropna.return_value.iloc.__getitem__ = MagicMock(return_value=100.0)

        mock_close = MagicMock()
        mock_close.__contains__ = MagicMock(return_value=True)
        mock_close.__getitem__ = MagicMock(return_value=mock_col)
        mock_close.columns = list(prices.keys())

        mock_data = MagicMock()
        mock_data.get = MagicMock(return_value=mock_close)
        mock_yf.download.return_value = mock_data
        return mock_yf

    def test_suffix_pure_digit_gets_tw(self):
        """4 位純數字代號應加 .TW 後綴。"""
        mock_yf = self._make_yf_mock({"2330.TW": 850.0})
        with patch.dict(sys.modules, {"yfinance": mock_yf}):
            _fetch_yfinance_prices(["2330"])
        call_args = mock_yf.download.call_args
        symbols = call_args[0][0] if call_args[0] else call_args[1].get("tickers", [])
        assert "2330.TW" in str(symbols)

    def test_suffix_alphanumeric_gets_two(self):
        """含字母的代號（上櫃 ETF 如 00878B）應加 .TWO。"""
        mock_yf = self._make_yf_mock({"00878B.TWO": 20.0})
        with patch.dict(sys.modules, {"yfinance": mock_yf}):
            _fetch_yfinance_prices(["00878B"])
        call_args = mock_yf.download.call_args
        assert "00878B.TWO" in str(call_args)

    def test_import_error_returns_empty(self):
        """yfinance 未安裝時回傳空 dict，不拋例外。"""
        original = sys.modules.pop("yfinance", None)
        # 使 import yfinance 失敗
        sys.modules["yfinance"] = None  # type: ignore
        try:
            result = _fetch_yfinance_prices(["2330"])
            assert result == {}
        finally:
            if original is not None:
                sys.modules["yfinance"] = original
            else:
                sys.modules.pop("yfinance", None)

    def test_download_exception_returns_empty(self):
        """下載失敗時回傳空 dict。"""
        mock_yf = MagicMock()
        mock_yf.download.side_effect = Exception("network error")
        with patch.dict(sys.modules, {"yfinance": mock_yf}):
            result = _fetch_yfinance_prices(["2330"])
        assert result == {}

    def test_returns_dict_type(self):
        """回傳型別為 dict。"""
        mock_yf = self._make_yf_mock({})
        with patch.dict(sys.modules, {"yfinance": mock_yf}):
            result = _fetch_yfinance_prices(["2330"])
        assert isinstance(result, dict)


# ── build_candidates ──────────────────────────────────────────────────────────

_FAKE_THEME_GROUPS = {
    "半導體": ["2330", "2454", "3711"],
    "電動車": ["2308", "1590", "6415"],
}

_FAKE_INST_DATA = {
    "2330": {
        "foreign_net": 5000.0,
        "investment_trust_net": 200.0,
        "dealer_net": -100.0,
        "total_net": 5100.0,
    },
    "2454": {
        "foreign_net": -500.0,
        "investment_trust_net": 50.0,
        "dealer_net": 0.0,
        "total_net": -450.0,
    },
}

# chip_data / themes 是在 build_candidates() 函數內 lazy import
# 需 patch sys.modules，確保 mock 在呼叫時生效
_MOCK_CHIP_MODULE = MagicMock()
_MOCK_CHIP_MODULE.fetch_institutional_investors.return_value = _FAKE_INST_DATA
_MOCK_CHIP_MODULE.fetch_margin_trading.return_value = {}
_MOCK_CHIP_MODULE.get_continuous_buy_days.return_value = {
    "foreign_continuous_buy": 0,
    "investment_trust_continuous_buy": 0,
}

_MOCK_THEMES_MODULE = MagicMock()
_MOCK_THEMES_MODULE.THEME_GROUPS = _FAKE_THEME_GROUPS


class TestBuildCandidates:
    """build_candidates() 整合測試（mocked 外部依賴）。"""

    @pytest.fixture(autouse=True)
    def _patch_externals(self, tmp_path):
        """統一 patch 所有外部相依（chip_data / themes / yfinance）。"""
        # yfinance: 每支都回傳 100.0
        mock_yf = MagicMock()
        mock_col = MagicMock()
        mock_col.dropna.return_value.iloc.__getitem__ = MagicMock(return_value=100.0)
        mock_close = MagicMock()
        mock_close.__contains__ = MagicMock(return_value=True)
        mock_close.__getitem__ = MagicMock(return_value=mock_col)
        mock_close.columns = ["dummy"]
        mock_data = MagicMock()
        mock_data.get = MagicMock(return_value=mock_close)
        mock_yf.download.return_value = mock_data

        # chip_data / themes mock
        chip_mock = MagicMock()
        chip_mock.fetch_institutional_investors.return_value = dict(_FAKE_INST_DATA)
        chip_mock.fetch_margin_trading.return_value = {}
        chip_mock.get_continuous_buy_days.return_value = {
            "foreign_continuous_buy": 0,
            "investment_trust_continuous_buy": 0,
        }

        themes_mock = MagicMock()
        themes_mock.THEME_GROUPS = _FAKE_THEME_GROUPS

        # ai_log: 空檔案
        ai_log = tmp_path / "ai_log.jsonl"
        ai_log.touch()

        with patch.dict(sys.modules, {
            "chip_data": chip_mock,
            "themes": themes_mock,
            "yfinance": mock_yf,
        }), patch("candidate_builder._AI_LOG_PATH", ai_log):
            self._chip_mock = chip_mock
            self._themes_mock = themes_mock
            self._ai_log = ai_log
            yield

    def test_returns_list_of_dicts(self):
        result = build_candidates(api=None)
        assert isinstance(result, list)
        assert len(result) > 0
        for c in result:
            assert isinstance(c, dict)

    def test_required_fields_present(self):
        result = build_candidates(api=None)
        for c in result:
            for field in ("code", "name", "close", "change_rate", "analysis", "chip_score"):
                assert field in c, f"缺少欄位 {field} in {c}"

    def test_max_candidates_is_30(self):
        result = build_candidates(api=None)
        assert len(result) <= 30

    def test_sorted_by_chip_score_descending(self):
        result = build_candidates(api=None)
        scores = [c["chip_score"] for c in result]
        assert scores == sorted(scores, reverse=True), "應按 chip_score 降序排列"

    def test_chip_score_positive_for_strong_stock(self):
        """2330 外資大買超 → chip_score > 0。"""
        result = build_candidates(api=None)
        stock_2330 = next((c for c in result if c["code"] == "2330"), None)
        assert stock_2330 is not None
        assert stock_2330["chip_score"] > 0

    def test_ai_log_picks_included(self, tmp_path):
        """ai_log.jsonl 的紀錄應出現在候選股中。"""
        ai_log = tmp_path / "ai_log2.jsonl"
        ai_log.write_text(
            json.dumps({"decision": {"stock_code": "9999", "reason": "測試"}}) + "\n",
            encoding="utf-8",
        )
        with patch("candidate_builder._AI_LOG_PATH", ai_log):
            result = build_candidates(api=None)
        codes = [c["code"] for c in result]
        assert "9999" in codes

    def test_ai_log_no_duplicates(self, tmp_path):
        """同一股票在 ai_log 重複出現，候選股中只保留一筆。"""
        ai_log = tmp_path / "ai_log3.jsonl"
        lines = [
            json.dumps({"decision": {"stock_code": "2330", "reason": "第1次"}}) + "\n",
            json.dumps({"decision": {"stock_code": "2330", "reason": "第2次"}}) + "\n",
        ]
        ai_log.write_text("".join(lines), encoding="utf-8")
        with patch("candidate_builder._AI_LOG_PATH", ai_log):
            result = build_candidates(api=None)
        codes = [c["code"] for c in result]
        assert codes.count("2330") == 1

    def test_analysis_contains_chip_summary_when_data_available(self):
        """有籌碼資料的股票，analysis 應包含【籌碼】標記。"""
        result = build_candidates(api=None)
        stock_2330 = next((c for c in result if c["code"] == "2330"), None)
        if stock_2330:
            assert "【籌碼】" in stock_2330["analysis"]


# ── H11: app.py 不再有完整重複的 _build_candidates ───────────────────────────

class TestAppPyNoDuplicate:
    def test_app_py_uses_candidate_builder(self):
        """app.py 應 import candidate_builder，而不是自己實作完整 pipeline。"""
        src = Path("app.py").read_text(encoding="utf-8")
        assert "candidate_builder" in src, \
            "app.py 未 import candidate_builder（_build_candidates 重複邏輯未移除）"

    def test_app_py_no_duplicated_chip_score_logic(self):
        """app.py 不應再自己含完整 chip_score 計算邏輯（已移到 candidate_builder）。"""
        src = Path("app.py").read_text(encoding="utf-8")
        # candidate_builder.py 有一處 chip_score = 0.0，app.py 應只剩 0 或 1 處
        count = src.count("chip_score = 0")
        assert count == 0, \
            f"app.py 仍有 {count} 處獨立 chip_score 計算，請確認重複邏輯已移除"
