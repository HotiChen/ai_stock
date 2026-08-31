"""
tests/test_review_all_predictions.py — 每日複盤要驗證「全部」預測

問題：get_unreviewed 只取 action='long'。但超過 analysis_count 的候選根本
沒經過 AI（自動 skip），加上 AI 近期幾乎每檔都判 skip —— 結果是一筆都不會
被複盤，準確率永遠算不出來、dt_counterfactual 也因為 skip 列沒有 OHLC 而
無法比較「AI 過濾到底有沒有加分」。

改為：
  * long：照舊算 outcome / was_correct（準確率）
  * skip：一併補 OHLC，was_correct 維持 None（不污染 long 勝率），
          並在摘要呈現「AI 說不要、結果當日大漲」的機會成本
"""
from unittest.mock import patch

import pytest

from daytrading_db import DaytradingDB, DTPrediction

TODAY = "2026-09-02"


def _pred(code, name, action, dt_score=8, target=None, stop=None):
    return DTPrediction(
        date=TODAY, code=code, name=name, dt_score=dt_score, action=action,
        entry_low=100.0 if action == "long" else None,
        entry_high=101.0 if action == "long" else None,
        target_price=target, stop_loss=stop, ai_summary="測試",
    )


def _bars(open_, high, low, close):
    """單根日 K（_determine_outcome 可接受的最小輸入）。"""
    return [{"open": open_, "high": high, "low": low, "close": close}]


@pytest.fixture
def db(tmp_path):
    return DaytradingDB(str(tmp_path / "review.db"))


class TestGetUnreviewedIncludeSkipped:
    def test_default_only_long_backward_compatible(self, db):
        db.save_predictions([
            _pred("2330", "台積電", "long", target=110.0, stop=95.0),
            _pred("2454", "聯發科", "skip"),
        ])
        rows = db.get_unreviewed(TODAY)
        assert [r["code"] for r in rows] == ["2330"]

    def test_include_skipped_returns_both(self, db):
        db.save_predictions([
            _pred("2330", "台積電", "long", target=110.0, stop=95.0),
            _pred("2454", "聯發科", "skip"),
        ])
        rows = db.get_unreviewed(TODAY, include_skipped=True)
        assert sorted(r["code"] for r in rows) == ["2330", "2454"]


class TestReviewCoversSkipped:
    def _run(self, db_path, bars_by_code):
        from daytrading_review import run_daytrading_review

        def _fake_bars(code):
            return bars_by_code.get(code)

        with patch("daytrading_review._fetch_intraday_bars", side_effect=_fake_bars):
            return run_daytrading_review(db_path=db_path, today=TODAY)

    def test_skipped_rows_get_ohlc_backfilled(self, db, tmp_path):
        """AI 判 skip 的也要補 OHLC，否則 dt_counterfactual 沒資料可比。"""
        db.save_predictions([
            _pred("2330", "台積電", "long", target=110.0, stop=95.0),
            _pred("2454", "聯發科", "skip"),
        ])
        self._run(db.path, {
            "2330": _bars(100.0, 112.0, 99.0, 110.0),   # 達標
            "2454": _bars(200.0, 220.0, 199.0, 218.0),  # 被跳過但大漲 10%
        })

        rows = {r["code"]: r for r in db.get_predictions(TODAY)}
        assert rows["2454"]["daily_open"] == 200.0     # skip 也補了 OHLC
        assert rows["2454"]["daily_high"] == 220.0
        assert rows["2330"]["outcome"] == "hit_target"

    def test_skipped_row_was_correct_stays_null(self, db):
        """skip 沒有目標價可判對錯，was_correct 必須維持 None，
        以免污染 long 的勝率統計。"""
        db.save_predictions([_pred("2454", "聯發科", "skip")])
        self._run(db.path, {"2454": _bars(200.0, 220.0, 199.0, 218.0)})

        row = db.get_predictions(TODAY)[0]
        assert row["was_correct"] is None

    def test_long_win_rate_unaffected_by_skipped(self, db):
        """複盤 skip 之後，近 30 日 long 勝率仍只計 long。"""
        db.save_predictions([
            _pred("2330", "台積電", "long", target=110.0, stop=95.0),
            _pred("2454", "聯發科", "skip"),
            _pred("3008", "大立光", "skip"),
        ])
        self._run(db.path, {
            "2330": _bars(100.0, 112.0, 99.0, 110.0),
            "2454": _bars(200.0, 220.0, 199.0, 218.0),
            "3008": _bars(300.0, 305.0, 299.0, 301.0),
        })
        stats = db.win_rate_summary(days=30)
        assert stats["total"] == 1        # 只有那筆 long
        assert stats["wins"] == 1

    def test_summary_reports_missed_opportunities(self, db):
        """摘要要點出「AI 說不要、結果當日大漲」的機會成本。"""
        db.save_predictions([
            _pred("2330", "台積電", "long", target=110.0, stop=95.0),
            _pred("2454", "聯發科", "skip"),   # +10% 高點 → 算錯過
            _pred("3008", "大立光", "skip"),   # +1.7% → 不算
        ])
        msg = self._run(db.path, {
            "2330": _bars(100.0, 112.0, 99.0, 110.0),
            "2454": _bars(200.0, 220.0, 199.0, 218.0),
            "3008": _bars(300.0, 305.0, 299.0, 301.0),
        })
        assert "觀望" in msg
        assert "2454" in msg              # 錯過的要點名
        assert "3008" not in msg          # 漲幅不足的不列

    def test_no_predictions_message(self, db):
        msg = self._run(db.path, {})
        assert "無待複盤" in msg
