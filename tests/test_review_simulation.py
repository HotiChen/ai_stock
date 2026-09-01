"""
tests/test_review_simulation.py — 複盤時計算並呈現虛擬損益

需求：每一筆預測都套用同一套買賣計畫（買固定金額、漲 X% 停利、跌 Y% 停損），
收盤後看到底賺多少賠多少，每天累積。

參數一律取自 DaytradingConfig（budget_per_stock / take_profit_pct /
stop_loss_pct / force_close_time），與真實交易用同一份設定——設定不一致的話，
模擬出來的績效無法用來調整真實參數。
"""
from unittest.mock import patch

import pytest

from daytrading_config import DaytradingConfig
from daytrading_db import DaytradingDB, DTPrediction

TODAY = "2026-06-30"


def _pred(code, name, action, dt_score=7):
    return DTPrediction(
        date=TODAY, code=code, name=name, dt_score=dt_score, action=action,
        entry_low=100.0 if action == "long" else None,
        entry_high=101.0 if action == "long" else None,
        target_price=110.0 if action == "long" else None,
        stop_loss=95.0 if action == "long" else None,
        ai_summary="測試",
    )


def _bars(open_, high, low, close):
    return [{"time": "09:00", "open": open_, "high": high,
             "low": low, "close": close}]


def _cfg(**kw):
    base = dict(budget_per_stock=30_000.0, take_profit_pct=9.0,
                stop_loss_pct=3.0, force_close_time="13:15")
    base.update(kw)
    return DaytradingConfig(**base)


def _run(db_path, bars_by_code, cfg=None):
    from daytrading_review import run_daytrading_review

    with patch("daytrading_review._fetch_intraday_bars",
               side_effect=lambda code, day=None, api=None: bars_by_code.get(code)), \
         patch("daytrading_config.load_daytrading_config", return_value=cfg or _cfg()):
        return run_daytrading_review(db_path=db_path, today=TODAY)


@pytest.fixture
def db(tmp_path):
    return DaytradingDB(str(tmp_path / "review.db"))


class TestSimulationRunsDuringReview:
    def test_long_prediction_gets_simulated(self, db):
        db.save_predictions([_pred("2330", "台積電", "long")])
        _run(db.path, {"2330": _bars(100.0, 112.0, 99.0, 110.0)})
        row = db.get_predictions(TODAY)[0]
        assert row["sim_entry"] == 100.0
        assert row["sim_exit_reason"] == "take_profit"
        assert row["sim_pnl"] > 0

    def test_skip_prediction_also_simulated(self, db):
        """★ AI 判觀望的也要算——否則無從回答「AI 說不要的，如果買了會怎樣」。"""
        db.save_predictions([_pred("2454", "聯發科", "skip")])
        _run(db.path, {"2454": _bars(200.0, 240.0, 199.0, 235.0)})
        assert db.get_predictions(TODAY)[0]["sim_pnl"] > 0

    def test_uses_config_percentages_not_prediction_targets(self, db):
        """★ 出場價來自設定的百分比，不是預測自己的 target_price。

        預測的 target 是 110（+10%），但設定停利是 +9% → 應在 109 出場。
        用預測自己的目標價的話，skip 沒有目標價就算不出來，兩組無法比較。
        """
        db.save_predictions([_pred("2330", "台積電", "long")])
        _run(db.path, {"2330": _bars(100.0, 115.0, 99.0, 114.0)})
        assert db.get_predictions(TODAY)[0]["sim_exit"] == pytest.approx(109.0)

    def test_custom_config_respected(self, db):
        db.save_predictions([_pred("2330", "台積電", "long")])
        _run(db.path, {"2330": _bars(100.0, 115.0, 99.0, 114.0)},
             cfg=_cfg(take_profit_pct=5.0))
        assert db.get_predictions(TODAY)[0]["sim_exit"] == pytest.approx(105.0)

    def test_no_bars_leaves_simulation_null(self, db):
        """抓不到分鐘 K 時不得產生假紀錄。"""
        db.save_predictions([_pred("2330", "台積電", "long")])
        _run(db.path, {})
        rows = db.get_predictions(TODAY)
        assert rows[0]["sim_pnl"] is None


class TestReportSection:
    def _seed_and_run(self, db, skip_pnl_positive: bool):
        db.save_predictions([
            _pred("2330", "台積電", "long"),
            _pred("2454", "聯發科", "skip"),
        ])
        skip_bars = (_bars(200.0, 240.0, 199.0, 235.0) if skip_pnl_positive
                     else _bars(200.0, 201.0, 180.0, 185.0))
        return _run(db.path, {
            "2330": _bars(100.0, 112.0, 99.0, 110.0),
            "2454": skip_bars,
        })

    def test_section_present(self, db):
        msg = self._seed_and_run(db, skip_pnl_positive=False)
        assert "虛擬損益" in msg

    def test_shows_capital_per_trade(self, db):
        """要標明每筆多少錢，否則損益數字沒有意義。"""
        msg = self._seed_and_run(db, skip_pnl_positive=False)
        assert "30,000" in msg

    def test_long_and_skip_totals_separated(self, db):
        msg = self._seed_and_run(db, skip_pnl_positive=False)
        assert "做多" in msg and "觀望" in msg

    def test_filter_credited_when_skips_would_have_lost(self, db):
        """★ 觀望組虧損 → AI 幫你避開了。"""
        msg = self._seed_and_run(db, skip_pnl_positive=False)
        assert "避開" in msg

    def test_filter_blamed_when_skips_would_have_won(self, db):
        """★ 觀望組獲利 → AI 讓你錯過了。這個方向同樣要講，
        報喜不報憂的複盤沒有價值。"""
        msg = self._seed_and_run(db, skip_pnl_positive=True)
        assert "錯過" in msg

    def test_no_section_when_nothing_simulated(self, db):
        db.save_predictions([_pred("2330", "台積電", "long")])
        msg = _run(db.path, {})
        assert "虛擬損益" not in msg

    def test_no_cumulative_line_with_too_few_samples(self, db):
        """★ 一兩筆就報「累計勝率」是誤導——樣本不足時不該顯示。"""
        msg = self._seed_and_run(db, skip_pnl_positive=False)
        assert "累計" not in msg

    def test_cumulative_line_appears_once_enough_samples(self, db):
        """近 30 日累積到 3 筆以上才顯示累計績效。"""
        from datetime import date, timedelta

        from dt_simulate import SimResult
        for i in range(3):
            d = (date.today() - timedelta(days=i + 1)).isoformat()
            db.save_predictions([DTPrediction(
                date=d, code="2330", name="台積電", dt_score=7, action="long",
                entry_low=None, entry_high=None, target_price=None, stop_loss=None,
            )])
            db.save_simulation(d, "2330", SimResult(
                entry=100.0, exit=109.0, exit_reason="take_profit",
                pnl=2570.0, pnl_pct=0.0857))

        msg = self._seed_and_run(db, skip_pnl_positive=False)
        assert "累計" in msg
        assert "過濾貢獻" in msg


class TestReportClarity:
    def test_explains_exit_rule_differs_from_prediction_targets(self, db):
        """★ 上方「達標／停損」用預測自己的目標價，下方虛擬損益用固定百分比。

        兩區塊的數字本來就會不同（同一天可能「達標 0」卻「勝率 67%」），
        沒有說明會被當成 bug。
        """
        db.save_predictions([_pred("2330", "台積電", "long")])
        msg = _run(db.path, {"2330": _bars(100.0, 112.0, 99.0, 110.0)})
        assert "與各筆預測自訂的目標價無關" in msg
