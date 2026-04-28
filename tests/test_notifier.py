from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

import notifier


@pytest.fixture(autouse=False)
def verbose(monkeypatch):
    """Force TELEGRAM_VERBOSE=True for tests that check verbose-only messages."""
    monkeypatch.setattr(notifier, "_VERBOSE", True)


# ── _send ─────────────────────────────────────────────────────────────────────

def test_send_silently_ignores_network_error():
    with patch("notifier.requests.post", side_effect=Exception("timeout")):
        notifier._send("hello")  # must not raise


def test_send_skips_when_no_token(monkeypatch):
    monkeypatch.setattr(notifier, "_TOKEN", "")
    monkeypatch.setattr(notifier, "_CHAT_ID", "123")
    with patch("notifier.requests.post") as mock_post:
        notifier._send("hello")
        mock_post.assert_not_called()


def test_send_skips_when_no_chat_id(monkeypatch):
    monkeypatch.setattr(notifier, "_TOKEN", "tok")
    monkeypatch.setattr(notifier, "_CHAT_ID", "")
    with patch("notifier.requests.post") as mock_post:
        notifier._send("hello")
        mock_post.assert_not_called()


def test_send_calls_post_with_correct_url(monkeypatch):
    monkeypatch.setattr(notifier, "_TOKEN", "mytoken")
    monkeypatch.setattr(notifier, "_CHAT_ID", "999")
    monkeypatch.setattr(notifier, "_API", "https://api.telegram.org/botmytoken")
    with patch("notifier.requests.post") as mock_post:
        notifier._send("test message")
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert "sendMessage" in url
        payload = mock_post.call_args[1]["json"]
        assert payload["text"] == "test message"
        assert payload["chat_id"] == "999"


# ── notify_system_start ───────────────────────────────────────────────────────

def test_system_start_simulation_label():
    with patch("notifier._send") as mock_send:
        notifier.notify_system_start(simulation=True)
        text = mock_send.call_args[0][0]
        assert "模擬模式" in text


def test_system_start_live_label():
    with patch("notifier._send") as mock_send:
        notifier.notify_system_start(simulation=False)
        text = mock_send.call_args[0][0]
        assert "實盤模式" in text


def test_system_start_contains_schedule():
    with patch("notifier._send") as mock_send:
        notifier.notify_system_start(simulation=True)
        text = mock_send.call_args[0][0]
        assert "08:30" in text
        assert "09:00" in text
        assert "13:35" in text


# ── notify_analysis_result ────────────────────────────────────────────────────

def _analysis_kwargs(**overrides):
    base = dict(
        code="2330", name="台積電", signal="buy", confidence=8,
        summary="技術面強勁", factors={"historical_trend": "多頭排列"},
        target_price=1000.0, stop_loss_price=900.0,
    )
    base.update(overrides)
    return base


def test_analysis_buy_shows_checkmark(verbose):
    with patch("notifier._send") as mock_send:
        notifier.notify_analysis_result(**_analysis_kwargs(signal="buy"))
        assert "✅" in mock_send.call_args[0][0]


def test_analysis_sell_shows_cross(verbose):
    with patch("notifier._send") as mock_send:
        notifier.notify_analysis_result(**_analysis_kwargs(signal="sell"))
        assert "❌" in mock_send.call_args[0][0]


def test_analysis_hold_shows_pause(verbose):
    with patch("notifier._send") as mock_send:
        notifier.notify_analysis_result(**_analysis_kwargs(signal="hold"))
        assert "⏸️" in mock_send.call_args[0][0]


def test_analysis_contains_code_and_name(verbose):
    with patch("notifier._send") as mock_send:
        notifier.notify_analysis_result(**_analysis_kwargs())
        text = mock_send.call_args[0][0]
        assert "2330" in text
        assert "台積電" in text


def test_analysis_contains_confidence(verbose):
    with patch("notifier._send") as mock_send:
        notifier.notify_analysis_result(**_analysis_kwargs(confidence=7))
        assert "7/10" in mock_send.call_args[0][0]


def test_analysis_shows_target_and_stop_loss(verbose):
    with patch("notifier._send") as mock_send:
        notifier.notify_analysis_result(**_analysis_kwargs(target_price=1020.0, stop_loss_price=880.0))
        text = mock_send.call_args[0][0]
        assert "1020" in text
        assert "880" in text


def test_analysis_no_target_no_crash(verbose):
    with patch("notifier._send"):
        notifier.notify_analysis_result(**_analysis_kwargs(target_price=None, stop_loss_price=None))


# ── notify_risk_approved / rejected ──────────────────────────────────────────

def test_risk_approved_contains_code(verbose):
    with patch("notifier._send") as mock_send:
        notifier.notify_risk_approved("2330", "台積電", 5000.0)
        assert "2330" in mock_send.call_args[0][0]


def test_risk_rejected_translates_reason(verbose):
    with patch("notifier._send") as mock_send:
        notifier.notify_risk_rejected("2330", "台積電", "ex_dividend")
        assert "除息" in mock_send.call_args[0][0]

    with patch("notifier._send") as mock_send:
        notifier.notify_risk_rejected("2330", "台積電", "blacklisted")
        assert "黑名單" in mock_send.call_args[0][0]

    with patch("notifier._send") as mock_send:
        notifier.notify_risk_rejected("2330", "台積電", "limit_up")
        assert "漲停" in mock_send.call_args[0][0]


def test_risk_summary_empty_lists_no_crash():
    with patch("notifier._send"):
        notifier.notify_risk_summary([], [])


def test_risk_summary_shows_counts():
    approved = [{"code": "2330", "name": "台積電", "budget": 5000}]
    rejected = [{"code": "2317", "name": "鴻海", "reason": "blacklisted"}]
    with patch("notifier._send") as mock_send:
        notifier.notify_risk_summary(approved, rejected)
        text = mock_send.call_args[0][0]
        assert "1" in text


# ── notify_order_success / skipped ───────────────────────────────────────────

def test_order_success_contains_order_id():
    with patch("notifier._send") as mock_send:
        notifier.notify_order_success("2330", "台積電", "buy", 1, 975.0, 975000.0, "common", "A123")
        assert "A123" in mock_send.call_args[0][0]


def test_order_success_shows_amount():
    with patch("notifier._send") as mock_send:
        notifier.notify_order_success("2330", "台積電", "buy", 1, 975.0, 975000.0, "common", "A123")
        assert "975,000" in mock_send.call_args[0][0]


def test_order_skipped_contains_reason():
    with patch("notifier._send") as mock_send:
        notifier.notify_order_skipped("2330", "台積電", "預算不足")
        assert "預算不足" in mock_send.call_args[0][0]


# ── notify_price_alert ────────────────────────────────────────────────────────

def test_price_alert_target_hit_message():
    with patch("notifier._send") as mock_send:
        notifier.notify_price_alert("2330", "台積電", "target_hit", 1010.0, target_price=1000.0)
        text = mock_send.call_args[0][0]
        assert "目標價" in text
        assert "1010" in text


def test_price_alert_stop_loss_message():
    with patch("notifier._send") as mock_send:
        notifier.notify_price_alert("2330", "台積電", "stop_loss", 890.0, stop_loss_price=900.0)
        text = mock_send.call_args[0][0]
        assert "停損" in text
        assert "890" in text


# ── notify_market_close ───────────────────────────────────────────────────────

def test_market_close_positive_pnl():
    with patch("notifier._send") as mock_send:
        notifier.notify_market_close(total_pnl=1500.0, trade_count=3)
        text = mock_send.call_args[0][0]
        assert "📈" in text
        assert "+1,500" in text


def test_market_close_negative_pnl():
    with patch("notifier._send") as mock_send:
        notifier.notify_market_close(total_pnl=-800.0, trade_count=2)
        text = mock_send.call_args[0][0]
        assert "📉" in text


def test_market_close_zero_trades_no_crash():
    with patch("notifier._send"):
        notifier.notify_market_close(total_pnl=0.0, trade_count=0)
