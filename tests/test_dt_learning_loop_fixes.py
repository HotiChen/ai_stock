"""當沖學習迴路的三個修正：schema 遷移、None 判定、資料品質守衛。

背景（2026-08-10 實測）
-----------------------
`adaptive_scorer.py` 每天由 post_market.sh 執行，但每天都靜默失敗：

    [WARNING] _query_history failed: no such column: was_correct
    ✅ 學習版本 v1 已更新
       累積預測：0 筆 / 0 個交易日

根因是 `CREATE TABLE IF NOT EXISTS` 不會替既有資料表補後來新增的欄位，
`was_correct` 從沒被遷移進 data/daytrading_review.db。

修好之後又會立刻踩到第二個坑：`buckets.append(r["was_correct"] or 0)`
會把 `None`（neutral，沒碰到 target 也沒碰到 stop）算成一次**失敗**，
系統性低估勝率。

第三個問題是資料品質：既有 29 筆全部是 neutral，因為它們產生於券商登入
失敗期間——股價退化、當日振幅平均只有 0.03%，而目標固定在 ±3%，永遠碰
不到。這種「預測根本無法被驗證」的記錄若混進勝率統計，會讓分母灌水。
"""

from __future__ import annotations

import sqlite3

import pytest


# ── P1：schema 遷移 ──────────────────────────────────────────────────────────

_OLD_SCHEMA = """
CREATE TABLE dt_prediction_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT    NOT NULL,
    code          TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    dt_score      INTEGER NOT NULL DEFAULT 0,
    action        TEXT    NOT NULL DEFAULT 'skip',
    target_price  REAL,
    stop_loss     REAL,
    ai_summary    TEXT    NOT NULL DEFAULT '',
    daily_open    REAL,
    daily_high    REAL,
    daily_low     REAL,
    daily_close   REAL,
    outcome       TEXT,
    created_at    TEXT,
    UNIQUE(date, code)
)
"""


@pytest.fixture
def legacy_db(tmp_path):
    """重現正式環境那顆缺 was_correct 的舊資料庫，並塞一筆既有資料。"""
    path = tmp_path / "daytrading_review.db"
    conn = sqlite3.connect(path)
    conn.execute(_OLD_SCHEMA)
    conn.execute(
        "INSERT INTO dt_prediction_log (date, code, name, dt_score, action, outcome)"
        " VALUES ('2026-05-29', '2330', '台積電', 8, 'long', 'neutral')"
    )
    conn.commit()
    conn.close()
    return str(path)


def _columns(path: str) -> list[str]:
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(dt_prediction_log)")]
    conn.close()
    return cols


def test_opening_legacy_db_adds_missing_column(legacy_db):
    """開啟舊 DB 就該自動補上 was_correct，不需要手動 ALTER。"""
    assert "was_correct" not in _columns(legacy_db), "前置條件：舊 DB 本來就沒有這欄"

    from daytrading_db import DaytradingDB

    DaytradingDB(legacy_db)

    assert "was_correct" in _columns(legacy_db), (
        "CREATE TABLE IF NOT EXISTS 不會補欄位，需要顯式 migration"
    )


def test_migration_preserves_existing_rows(legacy_db):
    """遷移不可以毀掉既有資料——那 29 筆雖然是髒的，也不該被砍。"""
    from daytrading_db import DaytradingDB

    DaytradingDB(legacy_db)

    conn = sqlite3.connect(legacy_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM dt_prediction_log").fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0]["code"] == "2330"
    assert rows[0]["outcome"] == "neutral"
    assert rows[0]["was_correct"] is None, "既有列的新欄位應為 NULL，不可臆測填值"


def test_migration_is_idempotent(legacy_db):
    """重複開啟不可以拋 duplicate column name。"""
    from daytrading_db import DaytradingDB

    DaytradingDB(legacy_db)
    DaytradingDB(legacy_db)  # 不可拋例外
    assert "was_correct" in _columns(legacy_db)


# ── P5：None 不等於失敗 ──────────────────────────────────────────────────────

def test_none_is_excluded_from_winrate_not_counted_as_loss():
    """was_correct=None（neutral）必須排除在樣本外，不可算成失敗。

    三筆同區間：1 勝、1 敗、1 個 None。
    正確勝率是 1/2 = 0.5；若把 None 當失敗會變成 1/3 ≈ 0.333。
    """
    from adaptive_scorer import _bucket_winrates

    rows = [
        {"dt_score": 8, "was_correct": 1},
        {"dt_score": 8, "was_correct": 0},
        {"dt_score": 8, "was_correct": None},
    ]
    result = _bucket_winrates(rows)

    bucket = next(iter(result.values()))
    assert bucket == pytest.approx(0.5), (
        f"None 被當成失敗了：得到 {bucket}，應為 0.5"
    )


def test_bucket_with_only_none_is_omitted_entirely():
    """整個區間都是 None 時，該區間不該出現在結果裡（而不是回報 0% 勝率）。

    這正是正式環境的現況：29 筆全 neutral。回報 0% 會讓人誤以為策略很爛，
    實際上是「還沒有任何一筆分出勝負」。
    """
    from adaptive_scorer import _bucket_winrates

    rows = [
        {"dt_score": 8, "was_correct": None},
        {"dt_score": 8, "was_correct": None},
    ]
    assert _bucket_winrates(rows) == {}


# ── 資料品質守衛 ─────────────────────────────────────────────────────────────

def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def test_flat_day_is_marked_untestable_not_neutral():
    """當日振幅極小時，預測根本無法被驗證，不可與真正的 neutral 混為一談。

    重現正式資料：開盤 2260、全天高低差 5 元（0.22%），目標卻在 +3%。
    """
    from daytrading_review import _determine_outcome

    bars = [_bar(2260.0, 2260.0, 2255.0, 2258.0)]
    outcome, was_correct = _determine_outcome(2327.8, 2192.2, bars)

    assert outcome == "untestable"
    assert was_correct is None


def test_normal_day_still_resolves_hit_target():
    """守衛不可以把正常的一天誤判掉。"""
    from daytrading_review import _determine_outcome

    bars = [_bar(100.0, 104.0, 99.0, 103.0)]
    outcome, was_correct = _determine_outcome(103.0, 95.0, bars)

    assert outcome == "hit_target"
    assert was_correct == 1


def test_normal_day_with_no_touch_stays_neutral():
    """有足夠振幅但兩邊都沒碰到 → 仍然是 neutral（真的沒觸發）。"""
    from daytrading_review import _determine_outcome

    bars = [_bar(100.0, 101.5, 98.5, 100.5)]
    outcome, was_correct = _determine_outcome(110.0, 90.0, bars)

    assert outcome == "neutral"
    assert was_correct is None


def test_untestable_rows_are_excluded_from_counterfactual():
    """反事實分析必須排除 untestable，否則假的 0% 報酬會稀釋統計。

    現況 `_calc_ret` 的 else 分支會退回 daily_close，把一筆「無法驗證」的
    記錄算成 +0.1% 的真實報酬。29 筆這種資料足以讓整份分析失去意義。
    """
    from dt_counterfactual import _calc_ret

    row = {
        "outcome": "untestable",
        "daily_open": 100.0, "daily_close": 100.1,
        "target_price": 103.0, "stop_loss": 97.0,
    }
    assert _calc_ret(row) is None, "untestable 應視為不可用，不可退回 daily_close"


def test_neutral_still_uses_daily_close_in_counterfactual():
    """反向確認：真正的 neutral 仍照原本邏輯用 daily_close 計算報酬。"""
    from dt_counterfactual import _calc_ret

    row = {
        "outcome": "neutral",
        "daily_open": 100.0, "daily_close": 101.0,
        "target_price": 110.0, "stop_loss": 90.0,
    }
    assert _calc_ret(row) == pytest.approx(0.01)
