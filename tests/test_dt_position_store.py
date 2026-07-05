"""tests/test_dt_position_store.py — SQLite 單一真相來源 + 遷移 + 併發安全."""
from __future__ import annotations

import json

import pytest


TD = "2026-07-03"


def _pos(code="2330", name="台積電", status="watching", **kw):
    from daytrading_monitor import DaytradingPosition
    base = dict(
        code=code, name=name, entry_low=99.0, entry_high=101.0,
        target_price=110.0, stop_loss=95.0, dt_score=90, status=status,
    )
    base.update(kw)
    return DaytradingPosition(**base)


@pytest.fixture
def paths(tmp_path):
    return {"db_path": str(tmp_path / "pos.db"), "json_path": str(tmp_path / "pos.json")}


# ── bulk save / load ──────────────────────────────────────────────────────────

class TestBulkRoundtrip:
    def test_save_and_load(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330"), _pos("2454", "聯發科", dt_score=80)],
                          trade_date=TD, **paths)
        loaded = {p.code: p for p in st.load_positions(TD, **paths)}
        assert set(loaded) == {"2330", "2454"}
        assert loaded["2454"].dt_score == 80

    def test_save_writes_json_mirror(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330")], trade_date=TD, **paths)
        data = json.loads(open(paths["json_path"], encoding="utf-8").read())
        assert data[0]["code"] == "2330"

    def test_upsert_only_touches_passed_codes(self, paths):
        """save_positions 只 UPSERT 傳入的 code，不刪除其他列。"""
        import dt_position_store as st
        st.save_positions([_pos("2330"), _pos("2454")], trade_date=TD, **paths)
        # 只存 2330（改 status），2454 必須保留
        st.save_positions([_pos("2330", status="active", entry_price=100.0)],
                          trade_date=TD, **paths)
        loaded = {p.code: p for p in st.load_positions(TD, **paths)}
        assert set(loaded) == {"2330", "2454"}
        assert loaded["2330"].status == "active"
        assert loaded["2454"].status == "watching"

    def test_replace_today_clears_old(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330"), _pos("2454")], trade_date=TD, **paths)
        st.replace_today([_pos("2317", "鴻海")], trade_date=TD, **paths)
        loaded = {p.code: p for p in st.load_positions(TD, **paths)}
        assert set(loaded) == {"2317"}


# ── atomic single-row ops ─────────────────────────────────────────────────────

class TestAtomicOps:
    def test_mark_entered(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330")], trade_date=TD, **paths)
        st.mark_entered("2330", 101.5, 1000, "common", trade_date=TD, **paths)
        p = st.load_positions(TD, **paths)[0]
        assert p.status == "active"
        assert p.entry_price == 101.5
        assert p.peak_price == 101.5
        assert p.quantity == 1000

    def test_mark_skipped(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330")], trade_date=TD, **paths)
        st.mark_skipped("2330", trade_date=TD, **paths)
        assert st.load_positions(TD, **paths)[0].status == "skipped"

    def test_mark_closed(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330", status="active")], trade_date=TD, **paths)
        st.mark_closed("2330", trade_date=TD, **paths)
        assert st.load_positions(TD, **paths)[0].status == "closed"

    def test_update_peak_only_rises(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330", status="active", entry_price=100.0,
                                 peak_price=100.0)], trade_date=TD, **paths)
        assert st.update_peak("2330", 105.0, trade_date=TD, **paths) is True
        assert st.load_positions(TD, **paths)[0].peak_price == 105.0
        # 較低價不下降、回傳 False
        assert st.update_peak("2330", 103.0, trade_date=TD, **paths) is False
        assert st.load_positions(TD, **paths)[0].peak_price == 105.0

    def test_append_alert_dedup(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330")], trade_date=TD, **paths)
        st.append_alert("2330", "entry", trade_date=TD, **paths)
        st.append_alert("2330", "entry", trade_date=TD, **paths)  # 重複不再加
        st.append_alert("2330", "target", trade_date=TD, **paths)
        assert st.load_positions(TD, **paths)[0].alerts_sent == ["entry", "target"]

    def test_record_sell_attempt_increments(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2330", status="active")], trade_date=TD, **paths)
        assert st.record_sell_attempt("2330", "boom", trade_date=TD, **paths) == 1
        assert st.record_sell_attempt("2330", "boom2", trade_date=TD, **paths) == 2
        p = st.load_positions(TD, **paths)[0]
        assert p.sell_attempts == 2
        assert p.last_sell_error == "boom2"


# ── 跨 process 併發（原子操作不互相蓋掉）─────────────────────────────────────

class TestConcurrency:
    def test_interleaved_load_and_atomic_op_no_clobber(self, paths):
        """A 載入快照後，B mark_entered X，A 再 save 其他股票 → B 的變更不得遺失。"""
        import dt_position_store as st
        st.save_positions([_pos("X"), _pos("Y")], trade_date=TD, **paths)

        # process A 載入完整快照（X、Y 都是 watching）
        a_snapshot = {p.code: p for p in st.load_positions(TD, **paths)}

        # process B 對 X 原子進場
        st.mark_entered("X", 200.0, 1000, "common", trade_date=TD, **paths)

        # process A 只回存它真正改過的 Y（不含過期的 X 快照）
        y = a_snapshot["Y"]
        y.peak_price = 55.0
        st.save_positions([y], trade_date=TD, **paths)

        result = {p.code: p for p in st.load_positions(TD, **paths)}
        # B 的變更存活
        assert result["X"].status == "active"
        assert result["X"].entry_price == 200.0
        # A 的變更也存活
        assert result["Y"].peak_price == 55.0


# ── JSON 一次性遷移 ───────────────────────────────────────────────────────────

class TestMigration:
    def test_imports_legacy_json_when_db_empty(self, paths):
        import dt_position_store as st
        # 準備舊 JSON（DB 尚無資料）
        legacy = [{
            "code": "2330", "name": "台積電", "entry_low": 99.0, "entry_high": 101.0,
            "target_price": 110.0, "stop_loss": 95.0, "dt_score": 90,
            "status": "active", "alerts_sent": ["entry"], "ai_summary": "x",
            "entry_price": 100.0, "peak_price": 102.0, "quantity": 1000,
            "lot_type": "common",
        }]
        with open(paths["json_path"], "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        # 遷移防護：JSON mtime 日期必須等於 trade_date 才會匯入（避免把過期
        # 鏡像復活成當日持倉）；把檔案 mtime 對齊 TD。
        import os as _os
        from datetime import datetime as _dt
        _ts = _dt.fromisoformat(TD + "T09:00:00").timestamp()
        _os.utime(paths["json_path"], (_ts, _ts))
        st._migrated.clear()

        loaded = st.load_positions(TD, **paths)
        assert len(loaded) == 1
        assert loaded[0].status == "active"
        assert loaded[0].alerts_sent == ["entry"]

    def test_no_migration_when_db_has_rows(self, paths):
        import dt_position_store as st
        st.save_positions([_pos("2454", "聯發科")], trade_date=TD, **paths)
        # JSON 有不同資料，但 DB 已有當日資料 → 不匯入
        with open(paths["json_path"], "w", encoding="utf-8") as f:
            json.dump([{"code": "9999", "name": "X", "status": "watching"}], f)
        st._migrated.clear()
        codes = {p.code for p in st.load_positions(TD, **paths)}
        assert codes == {"2454"}
