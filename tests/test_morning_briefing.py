from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from morning_briefing import (
    MarketData,
    MorningBriefing,
    build_briefing_prompt,
    generate_morning_briefing,
    save_briefing,
    load_briefing,
    append_intraday_update,
    fill_post_market,
    _parse_briefing_response,
)

TODAY = date(2026, 5, 3)

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_market_data(**kw) -> MarketData:
    defaults = dict(
        date=TODAY,
        twse_index=20_500.0,
        twse_volume=2_800.0,       # 億元
        foreign_net=35.0,          # 外資淨買（億）
        trust_net=5.2,             # 投信淨買（億）
        margin_balance=1_850.0,    # 融資餘額（億）
        sp500=5_650.0,
        nasdaq=18_200.0,
        sox=4_800.0,
        vix=18.5,
        news_items=[
            "鉅亨：台積電 CoWoS 訂單能見度至 2026",
            "Yahoo：聯準會暗示年內不降息",
            "PTT：外資連三買台積電",
        ],
    )
    defaults.update(kw)
    return MarketData(**defaults)


def _make_briefing(**kw) -> MorningBriefing:
    defaults = dict(
        date=TODAY,
        generated_at=datetime(2026, 5, 3, 8, 0, 0),
        market_data=_make_market_data(),
        ai_analysis="台股偏多，關注半導體族群",
        market_outlook="bullish",
        key_risks=["美股高估值", "地緣政治"],
        key_opportunities=["AI 伺服器供應鏈", "CoWoS 相關"],
        sector_focus=["半導體", "AI 伺服器"],
        intraday_updates=[],
        post_market=None,
    )
    defaults.update(kw)
    return MorningBriefing(**defaults)


SAMPLE_GEMINI_JSON = json.dumps({
    "ai_analysis": "台股今日偏多，外資連買三日，半導體族群強勢",
    "market_outlook": "bullish",
    "key_risks": ["美股高估值", "地緣政治風險"],
    "key_opportunities": ["AI CoWoS 供應鏈", "外資持續回補"],
    "sector_focus": ["半導體", "AI 伺服器"],
})


# ── MarketData ─────────────────────────────────────────────────────────────────

class TestMarketData:
    def test_instantiation(self):
        md = _make_market_data()
        assert md.date == TODAY

    def test_twse_fields(self):
        md = _make_market_data()
        assert md.twse_index == pytest.approx(20_500.0)
        assert md.twse_volume == pytest.approx(2_800.0)
        assert md.foreign_net == pytest.approx(35.0)
        assert md.trust_net == pytest.approx(5.2)
        assert md.margin_balance == pytest.approx(1_850.0)

    def test_us_market_fields(self):
        md = _make_market_data()
        assert md.sp500 == pytest.approx(5_650.0)
        assert md.nasdaq == pytest.approx(18_200.0)
        assert md.sox == pytest.approx(4_800.0)
        assert md.vix == pytest.approx(18.5)

    def test_news_items(self):
        md = _make_market_data()
        assert len(md.news_items) == 3
        assert "台積電" in md.news_items[0]

    def test_optional_fields_can_be_none(self):
        md = MarketData(
            date=TODAY,
            twse_index=None, twse_volume=None,
            foreign_net=None, trust_net=None, margin_balance=None,
            sp500=None, nasdaq=None, sox=None, vix=None,
            news_items=[],
        )
        assert md.twse_index is None
        assert md.news_items == []


# ── MorningBriefing ────────────────────────────────────────────────────────────

class TestMorningBriefing:
    def test_instantiation(self):
        b = _make_briefing()
        assert b.date == TODAY

    def test_fields(self):
        b = _make_briefing()
        assert b.market_outlook == "bullish"
        assert "半導體" in b.key_opportunities[0] or "半導體" in b.sector_focus[0]

    def test_intraday_updates_default_empty(self):
        b = _make_briefing()
        assert b.intraday_updates == []

    def test_post_market_default_none(self):
        b = _make_briefing()
        assert b.post_market is None


# ── build_briefing_prompt ──────────────────────────────────────────────────────

class TestBuildBriefingPrompt:
    def test_returns_string(self):
        md = _make_market_data()
        prompt = build_briefing_prompt(md)
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_includes_twse_index(self):
        md = _make_market_data()
        prompt = build_briefing_prompt(md)
        assert "20,500" in prompt or "20500" in prompt

    def test_includes_foreign_net(self):
        md = _make_market_data()
        prompt = build_briefing_prompt(md)
        assert "35" in prompt

    def test_includes_vix(self):
        md = _make_market_data()
        prompt = build_briefing_prompt(md)
        assert "18.5" in prompt or "18" in prompt

    def test_includes_sox(self):
        md = _make_market_data()
        prompt = build_briefing_prompt(md)
        assert "4,800" in prompt or "4800" in prompt or "SOX" in prompt

    def test_includes_news(self):
        md = _make_market_data()
        prompt = build_briefing_prompt(md)
        assert "台積電" in prompt
        assert "CoWoS" in prompt

    def test_asks_for_json_output(self):
        md = _make_market_data()
        prompt = build_briefing_prompt(md)
        assert "JSON" in prompt or "json" in prompt

    def test_asks_for_market_outlook(self):
        md = _make_market_data()
        prompt = build_briefing_prompt(md)
        assert "market_outlook" in prompt or "看法" in prompt or "多空" in prompt

    def test_none_fields_handled_gracefully(self):
        """部分欄位為 None 時不應 crash"""
        md = _make_market_data(twse_index=None, sox=None, vix=None)
        prompt = build_briefing_prompt(md)
        assert isinstance(prompt, str)


# ── _parse_briefing_response ──────────────────────────────────────────────────

class TestParseBriefingResponse:
    def test_parses_valid_json(self):
        result = _parse_briefing_response(SAMPLE_GEMINI_JSON, TODAY)
        assert isinstance(result, MorningBriefing)

    def test_parses_ai_analysis(self):
        result = _parse_briefing_response(SAMPLE_GEMINI_JSON, TODAY)
        assert "外資" in result.ai_analysis

    def test_parses_market_outlook(self):
        result = _parse_briefing_response(SAMPLE_GEMINI_JSON, TODAY)
        assert result.market_outlook == "bullish"

    def test_parses_key_risks(self):
        result = _parse_briefing_response(SAMPLE_GEMINI_JSON, TODAY)
        assert len(result.key_risks) >= 1

    def test_parses_key_opportunities(self):
        result = _parse_briefing_response(SAMPLE_GEMINI_JSON, TODAY)
        assert len(result.key_opportunities) >= 1

    def test_parses_sector_focus(self):
        result = _parse_briefing_response(SAMPLE_GEMINI_JSON, TODAY)
        assert "半導體" in result.sector_focus

    def test_date_set_correctly(self):
        result = _parse_briefing_response(SAMPLE_GEMINI_JSON, TODAY)
        assert result.date == TODAY

    def test_intraday_updates_empty_on_parse(self):
        result = _parse_briefing_response(SAMPLE_GEMINI_JSON, TODAY)
        assert result.intraday_updates == []

    def test_post_market_none_on_parse(self):
        result = _parse_briefing_response(SAMPLE_GEMINI_JSON, TODAY)
        assert result.post_market is None

    def test_handles_json_wrapped_in_markdown(self):
        wrapped = f"```json\n{SAMPLE_GEMINI_JSON}\n```"
        result = _parse_briefing_response(wrapped, TODAY)
        assert isinstance(result, MorningBriefing)

    def test_returns_none_on_invalid_json(self):
        result = _parse_briefing_response("not json at all", TODAY)
        assert result is None


# ── generate_morning_briefing ──────────────────────────────────────────────────

class TestGenerateMorningBriefing:
    @patch("morning_briefing.collect_market_data")
    @patch("morning_briefing.call_gemini")
    def test_returns_morning_briefing(self, mock_gemini, mock_collect):
        mock_collect.return_value = _make_market_data()
        mock_gemini.return_value = SAMPLE_GEMINI_JSON
        result = generate_morning_briefing(TODAY)
        assert isinstance(result, MorningBriefing)

    @patch("morning_briefing.collect_market_data")
    @patch("morning_briefing.call_gemini")
    def test_calls_gemini_with_prompt(self, mock_gemini, mock_collect):
        mock_collect.return_value = _make_market_data()
        mock_gemini.return_value = SAMPLE_GEMINI_JSON
        generate_morning_briefing(TODAY)
        assert mock_gemini.called
        prompt = mock_gemini.call_args[0][0]
        assert isinstance(prompt, str)
        assert len(prompt) > 50

    @patch("morning_briefing.collect_market_data")
    @patch("morning_briefing.call_gemini")
    def test_date_matches(self, mock_gemini, mock_collect):
        mock_collect.return_value = _make_market_data()
        mock_gemini.return_value = SAMPLE_GEMINI_JSON
        result = generate_morning_briefing(TODAY)
        assert result.date == TODAY

    @patch("morning_briefing.collect_market_data")
    @patch("morning_briefing.call_gemini")
    def test_market_data_stored(self, mock_gemini, mock_collect):
        mock_collect.return_value = _make_market_data()
        mock_gemini.return_value = SAMPLE_GEMINI_JSON
        result = generate_morning_briefing(TODAY)
        assert result.market_data.twse_index == pytest.approx(20_500.0)

    @patch("morning_briefing.collect_market_data")
    @patch("morning_briefing.call_gemini")
    def test_returns_none_on_gemini_failure(self, mock_gemini, mock_collect):
        mock_collect.return_value = _make_market_data()
        mock_gemini.return_value = ""
        result = generate_morning_briefing(TODAY)
        assert result is None

    @patch("morning_briefing.collect_market_data")
    @patch("morning_briefing.call_gemini")
    def test_returns_none_on_collect_failure(self, mock_gemini, mock_collect):
        mock_collect.side_effect = Exception("network error")
        result = generate_morning_briefing(TODAY)
        assert result is None


# ── save / load ───────────────────────────────────────────────────────────────

class TestSaveLoadBriefing:
    def test_save_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            path = save_briefing(b, tmp)
            assert Path(path).exists()

    def test_save_filename_contains_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            path = save_briefing(b, tmp)
            assert "2026-05-03" in path

    def test_save_is_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            path = save_briefing(b, tmp)
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            assert "date" in data

    def test_load_returns_briefing(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            save_briefing(b, tmp)
            loaded = load_briefing(TODAY, tmp)
            assert isinstance(loaded, MorningBriefing)

    def test_load_preserves_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            save_briefing(b, tmp)
            loaded = load_briefing(TODAY, tmp)
            assert loaded.date == TODAY

    def test_load_preserves_market_outlook(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            save_briefing(b, tmp)
            loaded = load_briefing(TODAY, tmp)
            assert loaded.market_outlook == "bullish"

    def test_load_preserves_news_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            save_briefing(b, tmp)
            loaded = load_briefing(TODAY, tmp)
            assert len(loaded.market_data.news_items) == 3

    def test_load_returns_none_if_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = load_briefing(TODAY, tmp)
            assert result is None

    def test_save_overwrites_existing(self):
        """同一天再存一次，覆蓋舊的"""
        with tempfile.TemporaryDirectory() as tmp:
            b1 = _make_briefing(market_outlook="bullish")
            save_briefing(b1, tmp)
            b2 = _make_briefing(market_outlook="bearish")
            save_briefing(b2, tmp)
            loaded = load_briefing(TODAY, tmp)
            assert loaded.market_outlook == "bearish"


# ── append_intraday_update ────────────────────────────────────────────────────

class TestAppendIntradayUpdate:
    def test_appends_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            save_briefing(b, tmp)
            append_intraday_update(TODAY, "10:00 台積電突破 860", tmp)
            loaded = load_briefing(TODAY, tmp)
            assert len(loaded.intraday_updates) == 1
            assert "台積電" in loaded.intraday_updates[0]

    def test_appends_multiple_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            save_briefing(b, tmp)
            append_intraday_update(TODAY, "10:00 盤中上漲", tmp)
            append_intraday_update(TODAY, "12:00 法人持續買超", tmp)
            loaded = load_briefing(TODAY, tmp)
            assert len(loaded.intraday_updates) == 2

    def test_update_order_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            save_briefing(b, tmp)
            append_intraday_update(TODAY, "10:00 第一筆", tmp)
            append_intraday_update(TODAY, "12:00 第二筆", tmp)
            loaded = load_briefing(TODAY, tmp)
            assert "第一筆" in loaded.intraday_updates[0]
            assert "第二筆" in loaded.intraday_updates[1]

    def test_raises_if_briefing_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(FileNotFoundError):
                append_intraday_update(TODAY, "10:00 update", tmp)


# ── fill_post_market ──────────────────────────────────────────────────────────

class TestFillPostMarket:
    def test_fills_post_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing()
            save_briefing(b, tmp)
            fill_post_market(TODAY, "今日外資買超符合預期，明日偏多", tmp)
            loaded = load_briefing(TODAY, tmp)
            assert loaded.post_market == "今日外資買超符合預期，明日偏多"

    def test_overwrites_existing_post_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _make_briefing(post_market="舊的")
            save_briefing(b, tmp)
            fill_post_market(TODAY, "新的分析", tmp)
            loaded = load_briefing(TODAY, tmp)
            assert loaded.post_market == "新的分析"

    def test_raises_if_briefing_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(FileNotFoundError):
                fill_post_market(TODAY, "analysis", tmp)
