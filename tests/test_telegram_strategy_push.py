from __future__ import annotations

import json
import tempfile
from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest

from strategy_planner import PlanSet, StrategyPlan, StockPick
from morning_briefing import MarketData, MorningBriefing
from datetime import datetime

# ── helpers ───────────────────────────────────────────────────────────────────

TODAY = date(2026, 5, 3)

def _make_pick(code="2330", name="台積電", action="open", qty=1,
               expected_pct=8.0, reason="RSI低檔反彈", confidence=8) -> StockPick:
    return StockPick(
        code=code, name=name, action=action,
        quantity=qty, hold_days=3,
        expected_return_pct=expected_pct,
        reason=reason, confidence=confidence,
        key_catalysts=["外資連買", "CoWoS訂單"],
        entry_logic="突破月線進場", exit_logic="目標 880 停損 830",
    )

def _make_plan(plan_type: str, expected_pct: float, picks=None) -> StrategyPlan:
    return StrategyPlan(
        plan_type=plan_type,
        description=f"{plan_type} 策略描述",
        picks=picks or [_make_pick()],
        capital_deployed_pct=0.8 if plan_type == "aggressive" else 0.5,
        expected_return_pct=expected_pct,
        risk_note="波動大" if plan_type == "aggressive" else "中等風險",
        thesis=f"{plan_type} 整體論述",
        macro_context="美股強勁，SOX 創高",
    )

def _make_planset() -> PlanSet:
    return PlanSet(
        aggressive=_make_plan("aggressive", 15.0),
        balanced=_make_plan("balanced", 8.0),
        conservative=_make_plan("conservative", 4.0, picks=[]),
        generated_at=TODAY,
    )

def _make_briefing() -> MorningBriefing:
    md = MarketData(
        date=TODAY, twse_index=38_927.0, twse_volume=None,
        foreign_net=-535.6, trust_net=10.8, margin_balance=None,
        sp500=7230.12, nasdaq=25114.44, sox=10595.34, vix=16.99,
        news_items=["台積電 CoWoS 訂單強勁", "美伊衝突升溫"],
    )
    return MorningBriefing(
        date=TODAY, generated_at=datetime(2026, 5, 3, 8, 0, 0),
        market_data=md,
        ai_analysis="今日台股外資賣超，偏謹慎操作",
        market_outlook="bearish",
        key_risks=["美伊衝突", "外資賣超"],
        key_opportunities=["半導體族群"],
        sector_focus=["半導體"],
    )


# ── imports under test ────────────────────────────────────────────────────────

from telegram_bot import (
    send_morning_push,
    format_briefing_message,
    format_plan_card,
    handle_select_plan,
)


# ── format_briefing_message ───────────────────────────────────────────────────

class TestFormatBriefingMessage:
    def test_returns_string(self):
        msg = format_briefing_message(_make_briefing())
        assert isinstance(msg, str)

    def test_includes_date(self):
        msg = format_briefing_message(_make_briefing())
        assert "2026-05-03" in msg or "05-03" in msg or "05/03" in msg

    def test_includes_twse_index(self):
        msg = format_briefing_message(_make_briefing())
        assert "38,927" in msg or "38927" in msg

    def test_includes_foreign_net(self):
        msg = format_briefing_message(_make_briefing())
        assert "535" in msg  # -535.6 億

    def test_includes_vix(self):
        msg = format_briefing_message(_make_briefing())
        assert "16.99" in msg or "17" in msg

    def test_includes_sox(self):
        msg = format_briefing_message(_make_briefing())
        assert "10,595" in msg or "10595" in msg or "SOX" in msg

    def test_includes_ai_analysis(self):
        msg = format_briefing_message(_make_briefing())
        assert "外資賣超" in msg

    def test_includes_market_outlook(self):
        msg = format_briefing_message(_make_briefing())
        assert "bearish" in msg.lower() or "空" in msg or "謹慎" in msg

    def test_includes_key_risks(self):
        msg = format_briefing_message(_make_briefing())
        assert "美伊衝突" in msg

    def test_includes_sector_focus(self):
        msg = format_briefing_message(_make_briefing())
        assert "半導體" in msg


# ── format_plan_card ──────────────────────────────────────────────────────────

class TestFormatPlanCard:
    def test_returns_string(self):
        plan = _make_plan("aggressive", 15.0)
        card = format_plan_card(plan)
        assert isinstance(card, str)

    def test_includes_plan_type(self):
        for pt in ("aggressive", "balanced", "conservative"):
            card = format_plan_card(_make_plan(pt, 8.0))
            assert pt in card.lower() or "衝刺" in card or "均衡" in card or "保守" in card

    def test_includes_expected_return(self):
        plan = _make_plan("aggressive", 15.0)
        card = format_plan_card(plan)
        assert "15" in card

    def test_includes_risk_note(self):
        plan = _make_plan("aggressive", 15.0)
        card = format_plan_card(plan)
        assert "波動大" in card

    def test_includes_pick_code(self):
        plan = _make_plan("balanced", 8.0)
        card = format_plan_card(plan)
        assert "2330" in card

    def test_includes_pick_reason(self):
        plan = _make_plan("balanced", 8.0)
        card = format_plan_card(plan)
        assert "RSI低檔反彈" in card

    def test_empty_picks_handled(self):
        plan = _make_plan("conservative", 4.0, picks=[])
        card = format_plan_card(plan)
        assert isinstance(card, str)
        assert "空手" in card or "無" in card or "保守" in card.lower() or "conservative" in card.lower()

    def test_includes_thesis(self):
        plan = _make_plan("aggressive", 15.0)
        card = format_plan_card(plan)
        assert "整體論述" in card or "aggressive" in card.lower()


# ── send_morning_push ─────────────────────────────────────────────────────────

class TestSendMorningPush:
    @patch("telegram_bot._post")
    def test_sends_multiple_messages(self, mock_post):
        mock_post.return_value = {"ok": True}
        send_morning_push(_make_briefing(), _make_planset(), starting_capital=200_000.0)
        # 至少：1 briefing + 3 plan cards + 1 selection message
        assert mock_post.call_count >= 5

    @patch("telegram_bot._post")
    def test_last_message_has_inline_keyboard(self, mock_post):
        mock_post.return_value = {"ok": True}
        send_morning_push(_make_briefing(), _make_planset(), starting_capital=200_000.0)
        # 最後一個 sendMessage call 應有 inline_keyboard
        last_payload = mock_post.call_args_list[-1][0][1]  # (method, payload)
        assert "reply_markup" in last_payload
        kb = last_payload["reply_markup"]
        assert "inline_keyboard" in kb

    @patch("telegram_bot._post")
    def test_inline_keyboard_has_3_buttons(self, mock_post):
        mock_post.return_value = {"ok": True}
        send_morning_push(_make_briefing(), _make_planset(), starting_capital=200_000.0)
        last_payload = mock_post.call_args_list[-1][0][1]
        buttons = last_payload["reply_markup"]["inline_keyboard"]
        # 所有 button 攤平
        all_btns = [b for row in buttons for b in row]
        assert len(all_btns) == 3

    @patch("telegram_bot._post")
    def test_button_callback_data_correct(self, mock_post):
        mock_post.return_value = {"ok": True}
        send_morning_push(_make_briefing(), _make_planset(), starting_capital=200_000.0)
        last_payload = mock_post.call_args_list[-1][0][1]
        buttons = last_payload["reply_markup"]["inline_keyboard"]
        all_btns = [b for row in buttons for b in row]
        cb_data = {b["callback_data"] for b in all_btns}
        assert "select_plan:aggressive" in cb_data
        assert "select_plan:balanced" in cb_data
        assert "select_plan:conservative" in cb_data

    @patch("telegram_bot._post")
    def test_button_text_includes_expected_return(self, mock_post):
        mock_post.return_value = {"ok": True}
        send_morning_push(_make_briefing(), _make_planset(), starting_capital=200_000.0)
        last_payload = mock_post.call_args_list[-1][0][1]
        buttons = last_payload["reply_markup"]["inline_keyboard"]
        all_texts = " ".join(b["text"] for row in buttons for b in row)
        assert "15" in all_texts   # aggressive 15%
        assert "8" in all_texts    # balanced 8%
        assert "4" in all_texts    # conservative 4%

    @patch("telegram_bot._post")
    def test_uses_chat_id_env(self, mock_post):
        mock_post.return_value = {"ok": True}
        send_morning_push(_make_briefing(), _make_planset(), starting_capital=200_000.0)
        # 所有 sendMessage 都要帶 chat_id
        for c in mock_post.call_args_list:
            method, payload = c[0]
            if method == "sendMessage":
                assert "chat_id" in payload


# ── handle_select_plan ────────────────────────────────────────────────────────

class TestHandleSelectPlan:
    def _make_callback(self, plan_type: str) -> dict:
        return {
            "id": "cq123",
            "data": f"select_plan:{plan_type}",
            "message": {"chat": {"id": "999"}},
        }

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    def test_sends_confirmation(self, mock_save, mock_send):
        handle_select_plan(self._make_callback("balanced"))
        assert mock_send.called

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    def test_confirmation_mentions_plan_type(self, mock_save, mock_send):
        handle_select_plan(self._make_callback("aggressive"))
        text = mock_send.call_args[0][1]
        assert "aggressive" in text.lower() or "衝刺" in text

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    def test_saves_daily_tracker_record(self, mock_save, mock_send):
        handle_select_plan(self._make_callback("conservative"))
        assert mock_save.called

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    def test_record_has_correct_plan_type(self, mock_save, mock_send):
        handle_select_plan(self._make_callback("balanced"))
        record = mock_save.call_args[0][0]  # first positional arg
        assert record.chosen_plan_type == "balanced"

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    def test_record_date_is_today(self, mock_save, mock_send):
        handle_select_plan(self._make_callback("aggressive"))
        record = mock_save.call_args[0][0]
        assert record.date == date.today()

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    def test_invalid_plan_type_sends_error(self, mock_save, mock_send):
        cb = self._make_callback("unknown_type")
        handle_select_plan(cb)
        text = mock_send.call_args[0][1]
        assert "錯誤" in text or "invalid" in text.lower() or "❌" in text

    @patch("telegram_bot.send_text")
    @patch("telegram_bot.save_day_record")
    def test_invalid_does_not_save(self, mock_save, mock_send):
        cb = self._make_callback("unknown_type")
        handle_select_plan(cb)
        assert not mock_save.called


# ── handle_callback integration ───────────────────────────────────────────────

class TestCallbackIntegration:
    """確認 handle_callback 能正確路由 select_plan:* 事件。"""

    @patch("telegram_bot.handle_select_plan")
    def test_routes_select_plan(self, mock_handler):
        from telegram_bot import handle_callback
        with patch("telegram_bot._post"):
            handle_callback({
                "id": "cq1",
                "data": "select_plan:balanced",
                "message": {"chat": {"id": "999"}},
            })
        assert mock_handler.called

    @patch("telegram_bot.handle_select_plan")
    def test_passes_full_callback_query(self, mock_handler):
        from telegram_bot import handle_callback
        cq = {
            "id": "cq1",
            "data": "select_plan:aggressive",
            "message": {"chat": {"id": "999"}},
        }
        with patch("telegram_bot._post"):
            handle_callback(cq)
        assert mock_handler.call_args[0][0] == cq
