"""
tests/test_simulation_persistence.py — 虛擬損益落庫與彙總

每一筆預測（long 與 skip 一視同仁）都要留下：進場價、出場價、出場原因、
損益金額、報酬率。沒有落庫的話，跨日累積、參數校準、以及「AI 過濾值不值得」
的比較全都做不了。
"""
import pytest

from daytrading_db import DaytradingDB, DTPrediction
from dt_simulate import SimResult

TODAY = "2026-06-30"


def _pred(code, name, action, dt_score=7, source="live"):
    return DTPrediction(
        date=TODAY, code=code, name=name, dt_score=dt_score, action=action,
        entry_low=None, entry_high=None, target_price=None, stop_loss=None,
        ai_summary="", source=source,
    )


def _sim(pnl, reason="take_profit", entry=100.0, exit_=109.0):
    return SimResult(entry=entry, exit=exit_, exit_reason=reason,
                     pnl=pnl, pnl_pct=round(pnl / 30_000, 4))


@pytest.fixture
def db(tmp_path):
    return DaytradingDB(str(tmp_path / "review.db"))


class TestSchema:
    def test_simulation_columns_exist(self, db):
        """新欄位要靠 _MIGRATIONS 補上——CREATE TABLE IF NOT EXISTS 對既有
        資料表是 no-op，光靠建表語句永遠補不上（LESSONS.md 錯誤 9）。"""
        db.save_predictions([_pred("2330", "台積電", "long")])
        row = db.get_predictions(TODAY)[0]
        for col in ("sim_entry", "sim_exit", "sim_exit_reason",
                    "sim_pnl", "sim_pnl_pct"):
            assert col in row.keys(), f"缺少欄位 {col}"

    def test_columns_start_null(self, db):
        db.save_predictions([_pred("2330", "台積電", "long")])
        assert db.get_predictions(TODAY)[0]["sim_pnl"] is None


class TestSaveSimulation:
    def test_writes_all_fields(self, db):
        db.save_predictions([_pred("2330", "台積電", "long")])
        db.save_simulation(TODAY, "2330", _sim(2570.0))
        row = db.get_predictions(TODAY)[0]
        assert row["sim_entry"] == 100.0
        assert row["sim_exit"] == 109.0
        assert row["sim_exit_reason"] == "take_profit"
        assert row["sim_pnl"] == 2570.0

    def test_skip_predictions_also_simulated(self, db):
        """★ AI 判觀望的也要算——沒有這些數字就無法回答
        「AI 說不要的那些，如果我買了會怎樣」。"""
        db.save_predictions([_pred("2454", "聯發科", "skip")])
        db.save_simulation(TODAY, "2454", _sim(-1041.0, reason="stop_loss"))
        assert db.get_predictions(TODAY)[0]["sim_pnl"] == -1041.0

    def test_unknown_code_is_noop_not_error(self, db):
        """複盤時股票可能已被刪除，不得因此中斷整批。"""
        db.save_simulation(TODAY, "9999", _sim(100.0))   # 不應拋出


class TestSummary:
    def _seed(self, db):
        db.save_predictions([
            _pred("2330", "台積電", "long"),
            _pred("2454", "聯發科", "long"),
            _pred("3008", "大立光", "skip"),
            _pred("6415", "矽力", "skip"),
        ])
        db.save_simulation(TODAY, "2330", _sim(2570.0))
        db.save_simulation(TODAY, "2454", _sim(-1041.0, reason="stop_loss"))
        db.save_simulation(TODAY, "3008", _sim(-800.0, reason="stop_loss"))
        db.save_simulation(TODAY, "6415", _sim(1500.0))

    def test_totals_split_by_action(self, db):
        """★ 做多與觀望要分開統計——混在一起就看不出過濾的價值。"""
        self._seed(db)
        s = db.simulation_summary(TODAY)
        assert s["long"]["total_pnl"] == pytest.approx(1529.0)
        assert s["skip"]["total_pnl"] == pytest.approx(700.0)

    def test_counts_and_win_rate(self, db):
        self._seed(db)
        s = db.simulation_summary(TODAY)
        assert s["long"]["count"] == 2
        assert s["long"]["wins"] == 1
        assert s["long"]["win_rate"] == pytest.approx(0.5)

    def test_filter_contribution_is_negative_of_skip_total(self, db):
        """AI 過濾的貢獻 = 「如果把觀望的也買了」會多賺（或少賠）多少的反面。

        觀望組合計 +700 → AI 幫你「錯過」700，貢獻為 -700。
        觀望組合計 -700 → AI 幫你避開 700 虧損，貢獻為 +700。
        """
        self._seed(db)
        s = db.simulation_summary(TODAY)
        assert s["filter_contribution"] == pytest.approx(-700.0)

    def test_ignores_unsimulated_rows(self, db):
        """沒有 sim_pnl 的列不得被當成 0 計入——那會稀釋統計。"""
        db.save_predictions([_pred("2330", "台積電", "long"),
                             _pred("2454", "聯發科", "long")])
        db.save_simulation(TODAY, "2330", _sim(1000.0))
        s = db.simulation_summary(TODAY)
        assert s["long"]["count"] == 1

    def test_empty_day_returns_zeros_not_none(self, db):
        s = db.simulation_summary(TODAY)
        assert s["long"]["count"] == 0
        assert s["long"]["total_pnl"] == 0.0
        assert s["long"]["win_rate"] is None

    def test_cumulative_all_time(self, db):
        """不指定日期時彙總全部。"""
        self._seed(db)
        s = db.simulation_summary()
        assert s["long"]["count"] == 2
        assert s["total_pnl"] == pytest.approx(2229.0)

    def test_days_filter_excludes_older_rows(self, db):
        """★ days 是相對「今天」的視窗——舊資料必須被排除，否則
        「近 30 日勝率」會混進半年前的資料。"""
        from datetime import date, timedelta
        recent = (date.today() - timedelta(days=2)).isoformat()
        old    = (date.today() - timedelta(days=90)).isoformat()
        for d, pnl in ((recent, 500.0), (old, 9999.0)):
            db.save_predictions([DTPrediction(
                date=d, code="2330", name="台積電", dt_score=7,
                action="long", entry_low=None, entry_high=None,
                target_price=None, stop_loss=None,
            )])
            db.save_simulation(d, "2330", _sim(pnl))
        s = db.simulation_summary(days=30)
        assert s["long"]["count"] == 1
        assert s["long"]["total_pnl"] == pytest.approx(500.0)

    def test_source_filter_separates_backfill(self, db):
        """★ 回填（規則版，無 LLM）與每日真實資料統計意義不同，混算沒有意義。"""
        db.save_predictions([
            _pred("2330", "台積電", "long", source="live"),
            _pred("2454", "聯發科", "long", source="backfill"),
        ])
        db.save_simulation(TODAY, "2330", _sim(1000.0))
        db.save_simulation(TODAY, "2454", _sim(9999.0))
        s = db.simulation_summary(TODAY, source="live")
        assert s["long"]["count"] == 1
        assert s["long"]["total_pnl"] == pytest.approx(1000.0)
