from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from portfolio import SimulatedPortfolio, Position
from morning_briefing import MarketData, MorningBriefing
from strategy_planner import StrategyPlan, StockPick

TODAY = date(2026, 5, 3)
NOW   = datetime(2026, 5, 3, 10, 0, 0)

# ── helpers ───────────────────────────────────────────────────────────────────

def _position(code="2330", name="台積電", qty=1, price=800.0) -> Position:
    return Position(
        code=code, name=name,
        quantity=qty, avg_cost=price,
        is_fractional=False, shares=0,
        entry_reason="RSI 低檔反彈，突破月線",
        entry_date=TODAY,
    )

def _portfolio(*positions: Position) -> SimulatedPortfolio:
    p = SimulatedPortfolio(initial_capital=500_000.0)
    for pos in positions:
        p.open_position(
            pos.code, pos.name, pos.quantity, pos.avg_cost,
            is_fractional=pos.is_fractional, shares=pos.shares,
            reason=pos.entry_reason, entry_date=TODAY,
        )
    return p

def _briefing() -> MorningBriefing:
    md = MarketData(
        date=TODAY, twse_index=38_927.0, twse_volume=None,
        foreign_net=-535.6, trust_net=10.8, margin_balance=None,
        sp500=7230.0, nasdaq=25114.0, sox=10595.0, vix=16.99,
        news_items=["台積電 CoWoS 訂單強勁"],
    )
    return MorningBriefing(
        date=TODAY,
        generated_at=datetime(2026, 5, 3, 8, 0, 0),
        market_data=md,
        ai_analysis="今日半導體族群偏多",
        market_outlook="bullish",
        key_risks=["美伊衝突"],
        key_opportunities=["半導體族群"],
        sector_focus=["半導體"],
    )


# ── imports under test ────────────────────────────────────────────────────────

from intraday_monitor import (
    Verdict,
    PositionCheck,
    IntradayReport,
    build_position_check_prompt,
    parse_check_response,
    check_position,
    run_intraday_monitor,
    format_monitor_message,
    save_intraday_report,
    load_intraday_report,
)


# ── Verdict enum ──────────────────────────────────────────────────────────────

class TestVerdict:
    def test_has_continue(self):
        assert Verdict.CONTINUE

    def test_has_caution(self):
        assert Verdict.CAUTION

    def test_has_exit(self):
        assert Verdict.EXIT

    def test_string_values(self):
        assert Verdict.CONTINUE.value in ("CONTINUE", "continue")
        assert Verdict.CAUTION.value  in ("CAUTION",  "caution")
        assert Verdict.EXIT.value     in ("EXIT",     "exit")


# ── PositionCheck dataclass ───────────────────────────────────────────────────

class TestPositionCheck:
    def test_fields(self):
        pc = PositionCheck(
            code="2330", name="台積電",
            verdict=Verdict.CONTINUE,
            reason="thesis 仍成立，外資持續買超",
            current_price=850.0,
            pnl_pct=6.25,
        )
        assert pc.code == "2330"
        assert pc.verdict == Verdict.CONTINUE
        assert pc.pnl_pct == pytest.approx(6.25)

    def test_optional_current_price(self):
        pc = PositionCheck(
            code="2330", name="台積電",
            verdict=Verdict.CAUTION,
            reason="需留意",
            current_price=None,
            pnl_pct=0.0,
        )
        assert pc.current_price is None


# ── IntradayReport dataclass ──────────────────────────────────────────────────

class TestIntradayReport:
    def _make_report(self):
        checks = [
            PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25),
            PositionCheck("2454", "聯發科", Verdict.CAUTION,  "觀察", 520.0, -3.8),
        ]
        return IntradayReport(date=TODAY, checked_at=NOW, checks=checks)

    def test_has_date(self):
        r = self._make_report()
        assert r.date == TODAY

    def test_has_checked_at(self):
        r = self._make_report()
        assert r.checked_at == NOW

    def test_has_checks(self):
        r = self._make_report()
        assert len(r.checks) == 2

    def test_exit_positions_property(self):
        r = self._make_report()
        # none are EXIT
        assert r.exit_positions == []

    def test_exit_positions_filters(self):
        checks = [
            PositionCheck("2330", "台積電", Verdict.EXIT,     "停損", 750.0, -6.25),
            PositionCheck("2454", "聯發科", Verdict.CONTINUE, "ok",   520.0, 2.0),
        ]
        r = IntradayReport(date=TODAY, checked_at=NOW, checks=checks)
        assert len(r.exit_positions) == 1
        assert r.exit_positions[0].code == "2330"

    def test_caution_positions_property(self):
        r = self._make_report()
        assert len(r.caution_positions) == 1
        assert r.caution_positions[0].code == "2454"


# ── build_position_check_prompt ───────────────────────────────────────────────

class TestBuildPositionCheckPrompt:
    def test_returns_string(self):
        pos = _position()
        prompt = build_position_check_prompt(pos, _briefing(), check_time=NOW)
        assert isinstance(prompt, str)

    def test_includes_stock_code(self):
        pos = _position("2330", "台積電")
        prompt = build_position_check_prompt(pos, _briefing(), check_time=NOW)
        assert "2330" in prompt

    def test_includes_entry_reason(self):
        pos = _position()
        prompt = build_position_check_prompt(pos, _briefing(), check_time=NOW)
        assert "RSI 低檔反彈" in prompt

    def test_includes_pnl_context(self):
        # avg_cost = 800, need current price embedded or at least entry cost
        pos = _position(price=800.0)
        prompt = build_position_check_prompt(pos, _briefing(), check_time=NOW)
        assert "800" in prompt

    def test_includes_check_time(self):
        prompt = build_position_check_prompt(_position(), _briefing(), check_time=NOW)
        assert "10:00" in prompt or "2026" in prompt

    def test_includes_market_outlook(self):
        prompt = build_position_check_prompt(_position(), _briefing(), check_time=NOW)
        assert "bullish" in prompt or "多" in prompt

    def test_includes_verdict_instructions(self):
        prompt = build_position_check_prompt(_position(), _briefing(), check_time=NOW)
        upper = prompt.upper()
        assert "CONTINUE" in upper and "CAUTION" in upper and "EXIT" in upper

    def test_requests_json_output(self):
        prompt = build_position_check_prompt(_position(), _briefing(), check_time=NOW)
        assert "json" in prompt.lower() or "JSON" in prompt


# ── parse_check_response ──────────────────────────────────────────────────────

class TestParseCheckResponse:
    def _valid_json(self, verdict="CONTINUE", reason="ok", price=850.0):
        import json
        return json.dumps({
            "verdict": verdict,
            "reason": reason,
            "current_price": price,
        })

    def test_parses_continue(self):
        result = parse_check_response(self._valid_json("CONTINUE"), "2330", "台積電", avg_cost=800.0)
        assert result.verdict == Verdict.CONTINUE

    def test_parses_caution(self):
        result = parse_check_response(self._valid_json("CAUTION"), "2330", "台積電", avg_cost=800.0)
        assert result.verdict == Verdict.CAUTION

    def test_parses_exit(self):
        result = parse_check_response(self._valid_json("EXIT"), "2330", "台積電", avg_cost=800.0)
        assert result.verdict == Verdict.EXIT

    def test_parses_reason(self):
        result = parse_check_response(self._valid_json(reason="thesis 仍成立"), "2330", "台積電", avg_cost=800.0)
        assert "thesis 仍成立" in result.reason

    def test_parses_current_price(self):
        result = parse_check_response(self._valid_json(price=870.0), "2330", "台積電", avg_cost=800.0)
        assert result.current_price == pytest.approx(870.0)

    def test_calculates_pnl_pct(self):
        # avg_cost=800, current=880 → +10%
        result = parse_check_response(self._valid_json(price=880.0), "2330", "台積電", avg_cost=800.0)
        assert result.pnl_pct == pytest.approx(10.0)

    def test_handles_markdown_wrapped_json(self):
        import json
        raw = "```json\n" + json.dumps({"verdict": "CONTINUE", "reason": "ok", "current_price": 850.0}) + "\n```"
        result = parse_check_response(raw, "2330", "台積電", avg_cost=800.0)
        assert result.verdict == Verdict.CONTINUE

    def test_invalid_json_returns_caution(self):
        result = parse_check_response("not json at all", "2330", "台積電", avg_cost=800.0)
        assert result.verdict == Verdict.CAUTION

    def test_unknown_verdict_returns_caution(self):
        import json
        raw = json.dumps({"verdict": "HOLD", "reason": "unknown", "current_price": 850.0})
        result = parse_check_response(raw, "2330", "台積電", avg_cost=800.0)
        assert result.verdict == Verdict.CAUTION

    def test_lowercase_verdict_accepted(self):
        import json
        raw = json.dumps({"verdict": "exit", "reason": "stop", "current_price": 750.0})
        result = parse_check_response(raw, "2330", "台積電", avg_cost=800.0)
        assert result.verdict == Verdict.EXIT


# ── check_position ────────────────────────────────────────────────────────────

class TestCheckPosition:
    @patch("intraday_monitor.call_gemini_with_search")
    def test_calls_gemini_with_search(self, mock_gemini):
        import json
        mock_gemini.return_value = json.dumps({
            "verdict": "CONTINUE", "reason": "ok", "current_price": 850.0
        })
        pos = _position()
        result = check_position(pos, _briefing(), check_time=NOW)
        assert mock_gemini.called

    @patch("intraday_monitor.call_gemini_with_search")
    def test_returns_position_check(self, mock_gemini):
        import json
        mock_gemini.return_value = json.dumps({
            "verdict": "CONTINUE", "reason": "ok", "current_price": 850.0
        })
        result = check_position(_position(), _briefing(), check_time=NOW)
        assert isinstance(result, PositionCheck)

    @patch("intraday_monitor.call_gemini_with_search", return_value="")
    def test_gemini_failure_returns_caution(self, mock_gemini):
        result = check_position(_position(), _briefing(), check_time=NOW)
        assert result.verdict == Verdict.CAUTION


# ── run_intraday_monitor ──────────────────────────────────────────────────────

class TestRunIntradayMonitor:
    @patch("intraday_monitor.check_position")
    def test_checks_each_position(self, mock_check):
        mock_check.return_value = PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25)
        port = _portfolio(_position("2330", "台積電"), _position("2454", "聯發科", price=500.0))
        report = run_intraday_monitor(port, _briefing(), check_time=NOW)
        assert mock_check.call_count == 2

    @patch("intraday_monitor.check_position")
    def test_returns_intraday_report(self, mock_check):
        mock_check.return_value = PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25)
        port = _portfolio(_position())
        report = run_intraday_monitor(port, _briefing(), check_time=NOW)
        assert isinstance(report, IntradayReport)

    @patch("intraday_monitor.check_position")
    def test_empty_portfolio_returns_empty_report(self, mock_check):
        port = SimulatedPortfolio(initial_capital=500_000.0)
        report = run_intraday_monitor(port, _briefing(), check_time=NOW)
        assert report.checks == []
        assert not mock_check.called

    @patch("intraday_monitor.check_position")
    def test_report_date_is_check_date(self, mock_check):
        mock_check.return_value = PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25)
        port = _portfolio(_position())
        report = run_intraday_monitor(port, _briefing(), check_time=NOW)
        assert report.date == TODAY
        assert report.checked_at == NOW


# ── format_monitor_message ────────────────────────────────────────────────────

class TestFormatMonitorMessage:
    def _report(self, checks):
        return IntradayReport(date=TODAY, checked_at=NOW, checks=checks)

    def test_returns_string(self):
        msg = format_monitor_message(self._report([
            PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25)
        ]))
        assert isinstance(msg, str)

    def test_includes_stock_code(self):
        msg = format_monitor_message(self._report([
            PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25)
        ]))
        assert "2330" in msg

    def test_includes_verdict_emoji_continue(self):
        msg = format_monitor_message(self._report([
            PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25)
        ]))
        assert "✅" in msg or "🟢" in msg

    def test_includes_verdict_emoji_caution(self):
        msg = format_monitor_message(self._report([
            PositionCheck("2330", "台積電", Verdict.CAUTION, "注意", 800.0, 0.0)
        ]))
        assert "⚠️" in msg or "🟡" in msg

    def test_includes_verdict_emoji_exit(self):
        msg = format_monitor_message(self._report([
            PositionCheck("2330", "台積電", Verdict.EXIT, "停損", 750.0, -6.25)
        ]))
        assert "🔴" in msg or "❌" in msg

    def test_includes_pnl(self):
        msg = format_monitor_message(self._report([
            PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25)
        ]))
        assert "6.25" in msg or "6.3" in msg or "+6" in msg

    def test_includes_check_time(self):
        msg = format_monitor_message(self._report([
            PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25)
        ]))
        assert "10:00" in msg or "盤中" in msg

    def test_empty_portfolio_message(self):
        msg = format_monitor_message(self._report([]))
        assert isinstance(msg, str)
        assert "空手" in msg or "無持倉" in msg or "0" in msg

    def test_multiple_positions(self):
        msg = format_monitor_message(self._report([
            PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok",  850.0,  6.25),
            PositionCheck("2454", "聯發科", Verdict.EXIT,     "停損", 480.0, -3.8),
        ]))
        assert "2330" in msg and "2454" in msg


# ── save / load intraday report ───────────────────────────────────────────────

class TestSaveLoadIntradayReport:
    def _report(self):
        return IntradayReport(
            date=TODAY, checked_at=NOW,
            checks=[
                PositionCheck("2330", "台積電", Verdict.CONTINUE, "ok", 850.0, 6.25),
                PositionCheck("2454", "聯發科", Verdict.EXIT, "停損", 480.0, -3.8),
            ],
        )

    def test_save_and_load_roundtrip(self, tmp_path):
        r = self._report()
        save_intraday_report(r, path=str(tmp_path / "report.json"))
        loaded = load_intraday_report(str(tmp_path / "report.json"))
        assert loaded is not None
        assert len(loaded.checks) == 2

    def test_load_preserves_verdict(self, tmp_path):
        r = self._report()
        save_intraday_report(r, path=str(tmp_path / "report.json"))
        loaded = load_intraday_report(str(tmp_path / "report.json"))
        verdicts = {c.code: c.verdict for c in loaded.checks}
        assert verdicts["2330"] == Verdict.CONTINUE
        assert verdicts["2454"] == Verdict.EXIT

    def test_load_missing_returns_none(self, tmp_path):
        result = load_intraday_report(str(tmp_path / "not_exist.json"))
        assert result is None
