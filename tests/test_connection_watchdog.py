from __future__ import annotations

"""
tests/test_connection_watchdog.py — TDD 測試 connection_watchdog.py

涵蓋：
  - 狀態機轉移矩陣（UNKNOWN→CONNECTED / CONNECTED→DISCONNECTED / DISCONNECTED→CONNECTED）
  - 防抖（debounce）：單次失敗不觸發斷線，連續 N 次才觸發
  - 重新連線推送
  - 速率限制（同一狀態 10 分鐘內只推一次）
  - notify 失敗被靜默吞掉，report_*() 不會 raise
  - monitor_agent 的 hook 會呼叫 watchdog（mock 驗證）
"""

import time
import types
import sys
from unittest.mock import MagicMock, patch, call

import pytest


# ── fixture：每個 test 重置 watchdog module 狀態 ─────────────────────────────

@pytest.fixture(autouse=True)
def reset_watchdog():
    """每次測試前後都重新載入 watchdog，確保狀態機回到初始值。"""
    # 移除快取，讓每次 import 都拿到乾淨的 module
    for key in list(sys.modules.keys()):
        if key == "connection_watchdog":
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if key == "connection_watchdog":
            del sys.modules[key]


def _get_watchdog():
    """取得乾淨的 watchdog module（不帶 Telegram 副作用）。"""
    import connection_watchdog
    return connection_watchdog


# ── 狀態機轉移 ────────────────────────────────────────────────────────────────

class TestStateMachineTransitions:
    """連線狀態機轉移矩陣測試。"""

    def test_initial_state_is_unknown(self):
        """模組初始狀態為 UNKNOWN。"""
        wdog = _get_watchdog()
        assert wdog.get_state() == "UNKNOWN"

    def test_first_success_transitions_to_connected(self):
        """UNKNOWN → 首次成功 → CONNECTED。"""
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()
        assert wdog.get_state() == "CONNECTED"

    def test_first_success_sends_connected_push(self):
        """UNKNOWN → CONNECTED 轉移時推送「已連線」訊息。"""
        wdog = _get_watchdog()
        notified = []
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_success()
        assert any("已連線" in m for m in notified)

    def test_connected_then_single_failure_stays_connected(self):
        """CONNECTED 狀態下單次失敗不轉移到 DISCONNECTED（防抖）。"""
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()  # → CONNECTED
            wdog.report_failure("timeout")  # 1 次，未達門檻
        assert wdog.get_state() == "CONNECTED"

    def test_connected_then_single_failure_sends_no_push(self):
        """CONNECTED 狀態下單次失敗不推送任何訊息。"""
        wdog = _get_watchdog()
        notified = []
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_success()   # → CONNECTED，推「已連線」
        notified.clear()
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_failure("timeout")  # 1 次，無推送
        assert notified == []

    def test_connected_then_two_failures_transitions_to_disconnected(self):
        """CONNECTED 狀態下連續 2 次失敗 → DISCONNECTED。"""
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()        # → CONNECTED
            wdog.report_failure("err")   # 1 次
            wdog.report_failure("err")   # 2 次 → DISCONNECTED
        assert wdog.get_state() == "DISCONNECTED"

    def test_connected_then_two_failures_sends_disconnected_push(self):
        """CONNECTED → DISCONNECTED 推送「斷線」訊息。"""
        wdog = _get_watchdog()
        notified = []
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_success()
        notified.clear()
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_failure("timeout")
            wdog.report_failure("timeout")
        assert any("斷線" in m for m in notified)

    def test_disconnected_push_includes_reason(self):
        """斷線推送訊息要包含失敗原因。"""
        wdog = _get_watchdog()
        notified = []
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_success()
        notified.clear()
        reason = "connection refused"
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_failure(reason)
            wdog.report_failure(reason)
        assert any(reason in m for m in notified)

    def test_disconnected_then_success_transitions_to_connected(self):
        """DISCONNECTED → 成功 → CONNECTED（重新連線）。"""
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()        # → CONNECTED
            wdog.report_failure("err")
            wdog.report_failure("err")   # → DISCONNECTED
            wdog.report_success()        # → CONNECTED again
        assert wdog.get_state() == "CONNECTED"

    def test_disconnected_then_success_sends_reconnected_push(self):
        """DISCONNECTED → CONNECTED 推送「已重新連線」訊息。"""
        wdog = _get_watchdog()
        notified = []
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()
            wdog.report_failure("err")
            wdog.report_failure("err")   # → DISCONNECTED
        notified.clear()
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_success()
        assert any("重新連線" in m for m in notified)

    def test_success_resets_failure_counter(self):
        """成功後失敗計數器歸零：1 失 → 1 成 → 1 失 不觸發斷線。"""
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()       # → CONNECTED
            wdog.report_failure("err")  # counter=1
            wdog.report_success()       # reset counter=0
            wdog.report_failure("err")  # counter=1（未達門檻）
        assert wdog.get_state() == "CONNECTED"


# ── 防抖閾值：WATCHDOG_FAILURE_THRESHOLD env var ──────────────────────────────

class TestDebounceThreshold:
    """WATCHDOG_FAILURE_THRESHOLD 環境變數控制連續失敗門檻。"""

    def test_threshold_1_single_failure_triggers_disconnected(self, monkeypatch):
        """WATCHDOG_FAILURE_THRESHOLD=1：單次失敗即觸發斷線。"""
        monkeypatch.setenv("WATCHDOG_FAILURE_THRESHOLD", "1")
        # 重新載入以套用 env
        for key in list(sys.modules.keys()):
            if key == "connection_watchdog":
                del sys.modules[key]
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()
            wdog.report_failure("err")  # threshold=1 → DISCONNECTED
        assert wdog.get_state() == "DISCONNECTED"

    def test_threshold_3_requires_three_failures(self, monkeypatch):
        """WATCHDOG_FAILURE_THRESHOLD=3：需要 3 次連續失敗才觸發斷線。"""
        monkeypatch.setenv("WATCHDOG_FAILURE_THRESHOLD", "3")
        for key in list(sys.modules.keys()):
            if key == "connection_watchdog":
                del sys.modules[key]
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()
            wdog.report_failure("err")   # 1
            wdog.report_failure("err")   # 2
        assert wdog.get_state() == "CONNECTED"  # 未達 3

        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_failure("err")   # 3 → DISCONNECTED
        assert wdog.get_state() == "DISCONNECTED"


# ── 速率限制 ──────────────────────────────────────────────────────────────────

class TestRateLimit:
    """同一狀態 10 分鐘內只推一次，避免重複推送。"""

    def test_no_duplicate_connected_push_within_10_minutes(self):
        """CONNECTED 後再次 report_success() 不重複推送（速率限制）。"""
        wdog = _get_watchdog()
        notified = []
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_success()  # 推「已連線」
            wdog.report_success()  # 同狀態，不再推
        # 只有 1 次推送
        connected_pushes = [m for m in notified if "已連線" in m and "重新" not in m]
        assert len(connected_pushes) == 1

    def test_push_allowed_after_10_minutes(self):
        """10 分鐘後允許同一狀態再次推送（mock time）。"""
        wdog = _get_watchdog()
        notified = []
        # 第一次進入 CONNECTED
        now = time.time()
        with patch("time.time", return_value=now):
            with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
                wdog.report_success()
        # 模擬 10 分鐘後（600s）又重新觸發相同的狀態推送路徑
        # 讓 watchdog 先進入 DISCONNECTED，再回 CONNECTED（重新連線推送）
        with patch("time.time", return_value=now):
            with patch.object(wdog, "_notify", return_value=None):
                wdog.report_failure("e")
                wdog.report_failure("e")  # → DISCONNECTED
        notified.clear()
        # 10 分鐘後重新連線
        with patch("time.time", return_value=now + 601):
            with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
                wdog.report_success()  # → CONNECTED（重新連線）
        assert any("重新連線" in m for m in notified)

    def test_duplicate_disconnected_push_suppressed(self):
        """DISCONNECTED 後再次達到斷線門檻不重複推送。"""
        wdog = _get_watchdog()
        notified = []
        # 進入 DISCONNECTED
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_failure("e")
            wdog.report_failure("e")   # → DISCONNECTED，推 1 次
        disc_pushes_1 = len([m for m in notified if "斷線" in m])
        # 持續失敗，不再推
        notified.clear()
        with patch.object(wdog, "_notify", side_effect=lambda msg: notified.append(msg)):
            wdog.report_failure("e")
            wdog.report_failure("e")
        disc_pushes_2 = len([m for m in notified if "斷線" in m])
        assert disc_pushes_1 == 1
        assert disc_pushes_2 == 0


# ── Notify 失敗靜默 ───────────────────────────────────────────────────────────

class TestNotifyFailureSwallowed:
    """notify 失敗時 report_*() 絕不 raise。"""

    def test_report_success_does_not_raise_on_notify_error(self):
        """send_text 拋出例外時，report_success() 不向外傳播。"""
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", side_effect=RuntimeError("network down")):
            wdog.report_success()  # 不應 raise

    def test_report_failure_does_not_raise_on_notify_error(self):
        """send_text 拋出例外時，report_failure() 不向外傳播。"""
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            wdog.report_success()  # → CONNECTED
        with patch.object(wdog, "_notify", side_effect=RuntimeError("network down")):
            wdog.report_failure("err")
            wdog.report_failure("err")  # 不應 raise

    def test_report_success_returns_none_always(self):
        """report_success() 永遠回傳 None（不回傳狀態）。"""
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            result = wdog.report_success()
        assert result is None

    def test_report_failure_returns_none_always(self):
        """report_failure() 永遠回傳 None（不回傳狀態）。"""
        wdog = _get_watchdog()
        with patch.object(wdog, "_notify", return_value=None):
            result = wdog.report_failure("err")
        assert result is None


# ── monitor_agent hook 整合 ───────────────────────────────────────────────────

class TestMonitorAgentHooks:
    """monitor_agent.py 在正確位置呼叫 watchdog（通過 mock 驗證）。"""

    @patch("monitor_agent.sj.Shioaji")
    def test_ensure_connected_success_calls_report_success(self, mock_shioaji):
        """ensure_connected() 成功時呼叫 connection_watchdog.report_success()。"""
        mock_api = MagicMock()
        mock_shioaji.return_value = mock_api

        with patch("connection_watchdog.report_success") as mock_rpt:
            from monitor_agent import ensure_connected
            result = ensure_connected("key", "secret", simulation=True)

        mock_rpt.assert_called()

    @patch("monitor_agent.sj.Shioaji")
    def test_ensure_connected_failure_calls_report_failure(self, mock_shioaji):
        """ensure_connected() 失敗時呼叫 connection_watchdog.report_failure()。"""
        mock_api = MagicMock()
        mock_api.login.side_effect = Exception("auth failed")
        mock_shioaji.return_value = mock_api

        with patch("connection_watchdog.report_failure") as mock_rpt:
            from monitor_agent import ensure_connected
            result = ensure_connected("bad", "bad", simulation=True)

        mock_rpt.assert_called()

    def test_get_snapshot_success_calls_report_success(self):
        """get_snapshot() 成功取得快照時呼叫 connection_watchdog.report_success()。"""
        api = MagicMock()
        snap = MagicMock()
        snap.close = 850.0
        snap.total_volume = 1000
        snap.change_price = 5.0
        api.snapshots.return_value = [snap]
        api.Contracts.Stocks.get.return_value = MagicMock()

        with patch("connection_watchdog.report_success") as mock_rpt:
            from monitor_agent import get_snapshot
            get_snapshot(api, "2330")

        mock_rpt.assert_called()

    def test_get_snapshot_failure_calls_report_failure(self):
        """get_snapshot() 失敗時呼叫 connection_watchdog.report_failure()。"""
        api = MagicMock()
        api.Contracts.Stocks.get.side_effect = Exception("socket error")

        with patch("connection_watchdog.report_failure") as mock_rpt:
            from monitor_agent import get_snapshot
            get_snapshot(api, "2330")

        mock_rpt.assert_called()

    def test_watchdog_exception_does_not_affect_get_snapshot(self):
        """watchdog 自身拋出例外不影響 get_snapshot() 的正常回傳值。"""
        api = MagicMock()
        snap = MagicMock()
        snap.close = 900.0
        snap.total_volume = 500
        snap.change_price = 10.0
        api.snapshots.return_value = [snap]
        api.Contracts.Stocks.get.return_value = MagicMock()

        with patch("connection_watchdog.report_success", side_effect=Exception("watchdog broken")):
            from monitor_agent import get_snapshot
            result = get_snapshot(api, "2330")

        assert result is not None
        assert result["close"] == pytest.approx(900.0)
