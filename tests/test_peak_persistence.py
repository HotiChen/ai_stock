from __future__ import annotations

"""
tests/test_peak_persistence.py — 波段移動停損最高點持久化（GAP 2）

問題：波段交易的 peak_price 只存在 in-memory _watchlist dict，main.py
重啟後 peak 歸零回 entry → 移動停損誤觸發。

修正：以 data/wave_peaks.json sidecar（atomic_write_json）持久化，key 為 code。
  - peak 升高時寫入 sidecar
  - set_watchlist / start 時讀回 sidecar，seed 每檔 peak_price =
    max(entry_price, persisted_peak)，重啟不丟高水位
  - 不在今日 watchlist 的舊 code 不 seed（忽略 stale 條目）

不得改動出場邏輯（門檻、何時賣）— 只持久化 peak。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _agent(tmp_path, peaks_path=None, **kwargs):
    """建立一個 MonitorAgent（提供 api 以跳過 ensure_connected）。"""
    from monitor_agent import MonitorAgent
    return MonitorAgent(
        api_key="k", secret_key="s", simulation=True,
        db_path=str(tmp_path / "test.db"), telegram_chat_id=None,
        api=MagicMock(),
        peaks_path=str(peaks_path or (tmp_path / "wave_peaks.json")),
        **kwargs,
    )


# ── peak 升高 → 寫入 sidecar ─────────────────────────────────────────────────

class TestPeakPersisted:
    def test_peak_rise_is_persisted_to_sidecar(self, tmp_path):
        """peak 升高 → sidecar 檔案寫入新高點。"""
        peaks_path = tmp_path / "wave_peaks.json"
        agent = _agent(tmp_path, peaks_path=peaks_path)
        agent.set_watchlist([
            {"code": "2330", "name": "台積電", "entry_price": 100.0,
             "target_price": 120.0, "stop_loss_price": 95.0, "quantity": 1000},
        ])

        # 模擬 tick：現價 104 高於 entry 100 → peak 應升到 104 並持久化
        agent._record_peak("2330", 104.0)

        data = json.loads(peaks_path.read_text(encoding="utf-8"))
        assert data["2330"] == pytest.approx(104.0)

    def test_peak_not_lowered_in_sidecar(self, tmp_path):
        """較低的 tick 不應降低 sidecar 中的 peak。"""
        peaks_path = tmp_path / "wave_peaks.json"
        agent = _agent(tmp_path, peaks_path=peaks_path)
        agent.set_watchlist([
            {"code": "2330", "entry_price": 100.0, "quantity": 1000},
        ])
        agent._record_peak("2330", 106.0)
        agent._record_peak("2330", 103.0)  # lower — must not overwrite

        data = json.loads(peaks_path.read_text(encoding="utf-8"))
        assert data["2330"] == pytest.approx(106.0)


# ── 重啟 → peak 還原（不歸零回 entry）────────────────────────────────────────

class TestPeakRestoredOnRestart:
    def test_restart_restores_peak_not_entry(self, tmp_path):
        """新 MonitorAgent 共用同一 sidecar → watchlist 的 peak 還原成持久值。"""
        peaks_path = tmp_path / "wave_peaks.json"

        # 第一個 agent：peak 升到 108
        agent1 = _agent(tmp_path, peaks_path=peaks_path)
        agent1.set_watchlist([
            {"code": "2330", "entry_price": 100.0, "quantity": 1000},
        ])
        agent1._record_peak("2330", 108.0)

        # 重啟：新 agent 讀同一 sidecar，set_watchlist 後 peak 應為 108 不是 100
        agent2 = _agent(tmp_path, peaks_path=peaks_path)
        agent2.set_watchlist([
            {"code": "2330", "entry_price": 100.0, "quantity": 1000},
        ])

        pick = agent2._watchlist["2330"]
        assert pick["peak_price"] == pytest.approx(108.0)

    def test_seed_uses_max_of_entry_and_persisted(self, tmp_path):
        """persisted peak 低於 entry（不該發生但要穩健）→ 取 max=entry。"""
        peaks_path = tmp_path / "wave_peaks.json"
        peaks_path.write_text(json.dumps({"2330": 90.0}), encoding="utf-8")

        agent = _agent(tmp_path, peaks_path=peaks_path)
        agent.set_watchlist([
            {"code": "2330", "entry_price": 100.0, "quantity": 1000},
        ])

        pick = agent._watchlist["2330"]
        assert pick["peak_price"] == pytest.approx(100.0)

    def test_no_persisted_peak_seeds_entry(self, tmp_path):
        """sidecar 無此 code → peak 起點為 entry_price。"""
        peaks_path = tmp_path / "wave_peaks.json"
        agent = _agent(tmp_path, peaks_path=peaks_path)
        agent.set_watchlist([
            {"code": "2330", "entry_price": 100.0, "quantity": 1000},
        ])
        pick = agent._watchlist["2330"]
        assert pick["peak_price"] == pytest.approx(100.0)


# ── stale 條目（不在今日 watchlist）不 seed ──────────────────────────────────

class TestStaleEntriesIgnored:
    def test_code_absent_from_watchlist_not_seeded(self, tmp_path):
        """sidecar 有舊 code，但不在今日 watchlist → 不出現在 watchlist。"""
        peaks_path = tmp_path / "wave_peaks.json"
        peaks_path.write_text(
            json.dumps({"1111": 200.0, "2330": 108.0}), encoding="utf-8")

        agent = _agent(tmp_path, peaks_path=peaks_path)
        agent.set_watchlist([
            {"code": "2330", "entry_price": 100.0, "quantity": 1000},
        ])

        assert "1111" not in agent._watchlist
        assert "2330" in agent._watchlist
        assert agent._watchlist["2330"]["peak_price"] == pytest.approx(108.0)

    def test_missing_sidecar_is_safe(self, tmp_path):
        """sidecar 不存在 → set_watchlist 正常，peak=entry。"""
        peaks_path = tmp_path / "does_not_exist.json"
        agent = _agent(tmp_path, peaks_path=peaks_path)
        agent.set_watchlist([
            {"code": "2330", "entry_price": 100.0, "quantity": 1000},
        ])
        assert agent._watchlist["2330"]["peak_price"] == pytest.approx(100.0)
