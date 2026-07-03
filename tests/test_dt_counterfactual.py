"""tests/test_dt_counterfactual.py — TDD tests for dt_counterfactual.py

LLM 過濾有效性反事實分析：比較「AI 實際策略」（action=long）、
「無 LLM 過濾」（每日 dt_score 前 N 名）、「反向檢查」（action=skip）
三組報酬表現，驗證統計數字與結論字串。資料透過 daytrading_db.DaytradingDB
建立的 SQLite schema 塞入已知記錄，不依賴網路。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest


def _pred(mod, d, code, name, dt_score, action, entry_low=None, entry_high=None,
          target_price=None, stop_loss=None):
    return mod.DTPrediction(
        date=d, code=code, name=name, dt_score=dt_score, action=action,
        entry_low=entry_low, entry_high=entry_high,
        target_price=target_price, stop_loss=stop_loss,
    )


def _review(mod, d, code, daily_open, daily_close, outcome,
            target_price=None, stop_loss=None, was_correct=None):
    daily_high = max(x for x in (daily_open, daily_close, target_price) if x is not None)
    daily_low = min(x for x in (daily_open, daily_close, stop_loss) if x is not None)
    return mod.DTReview(
        date=d, code=code,
        daily_open=daily_open, daily_high=daily_high,
        daily_low=daily_low, daily_close=daily_close,
        outcome=outcome, was_correct=was_correct,
    )


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "daytrading_review.db")


def _seed_known_scenario(db_path):
    """建立一組人工可算的兩天資料：

    Day1（today）
      A  2330  dt_score=9  long  entry=100  hit_target target=110  → ret=+0.10
      B  2603  dt_score=8  skip  entry=50   neutral    close=50    → ret= 0.00
      C  1101  dt_score=3  skip  entry=20   hit_stop   stop=18     → ret=-0.10

    Day2（today-1）
      D  2317  dt_score=7  long  entry=200  hit_stop   stop=190    → ret=-0.05
      E  2454  dt_score=9  skip  entry=30   hit_target target=33   → ret=+0.10

    long 檔數：day1=1, day2=1 → filter_n = round(mean([1,1])) = 1

    策略 B（每日前 1 名，不看 action）：day1→A(dt_score 9)，day2→E(dt_score 9)
      rets_b = [+0.10, +0.10]

    策略 A（action=long）：rets_a = [+0.10（A）, -0.05（D）]
    策略 C（action=skip）：rets_c = [0.00（B）, -0.10（C）, +0.10（E）]
    """
    import daytrading_db as dbmod

    db = dbmod.DaytradingDB(db_path)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    db.save_predictions([
        _pred(dbmod, today, "2330", "A", 9, "long", target_price=110, stop_loss=95),
        _pred(dbmod, today, "2603", "B", 8, "skip", target_price=55, stop_loss=48),
        _pred(dbmod, today, "1101", "C", 3, "skip", target_price=22, stop_loss=18),
        _pred(dbmod, yesterday, "2317", "D", 7, "long", target_price=210, stop_loss=190),
        _pred(dbmod, yesterday, "2454", "E", 9, "skip", target_price=33, stop_loss=27),
    ])

    db.save_review(_review(dbmod, today, "2330", 100.0, 108.0, "hit_target", target_price=110))
    db.save_review(_review(dbmod, today, "2603", 50.0, 50.0, "neutral"))
    db.save_review(_review(dbmod, today, "1101", 20.0, 19.0, "hit_stop", stop_loss=18))
    db.save_review(_review(dbmod, yesterday, "2317", 200.0, 195.0, "hit_stop", stop_loss=190))
    db.save_review(_review(dbmod, yesterday, "2454", 30.0, 32.0, "hit_target", target_price=33))

    return db_path


# ── analyze：三組策略統計 ──────────────────────────────────────────────────

class TestAnalyzeKnownScenario:
    def test_strategy_a_ai_actual(self, db_path):
        from dt_counterfactual import analyze
        db_path = _seed_known_scenario(db_path)
        report = analyze(db_path, days=30)

        a = report.strategy_a
        assert a.n == 2
        assert a.win_rate == pytest.approx(0.5)
        assert a.avg_ret == pytest.approx((0.10 + -0.05) / 2)
        assert a.total_ret == pytest.approx(0.10 + -0.05)

    def test_strategy_b_no_llm_filter(self, db_path):
        from dt_counterfactual import analyze
        db_path = _seed_known_scenario(db_path)
        report = analyze(db_path, days=30)

        assert report.filter_n == 1
        b = report.strategy_b
        assert b.n == 2
        assert b.win_rate == pytest.approx(1.0)
        assert b.avg_ret == pytest.approx(0.10)
        assert b.total_ret == pytest.approx(0.20)

    def test_strategy_c_reverse_check(self, db_path):
        from dt_counterfactual import analyze
        db_path = _seed_known_scenario(db_path)
        report = analyze(db_path, days=30)

        c = report.strategy_c
        assert c.n == 3
        assert c.win_rate == pytest.approx(1 / 3)
        assert c.avg_ret == pytest.approx(0.0, abs=1e-9)
        assert c.total_ret == pytest.approx(0.0, abs=1e-9)

    def test_n_trading_days(self, db_path):
        from dt_counterfactual import analyze
        db_path = _seed_known_scenario(db_path)
        report = analyze(db_path, days=30)
        assert report.n_trading_days == 2

    def test_conclusion_reports_llm_filter_decreased_return(self, db_path):
        """A 的平均報酬 (0.025) 低於 B (0.10)，代表本例中 LLM 過濾反而拖累報酬。"""
        from dt_counterfactual import analyze
        db_path = _seed_known_scenario(db_path)
        report = analyze(db_path, days=30)

        expected_delta = report.strategy_a.avg_ret - report.strategy_b.avg_ret
        assert report.avg_ret_delta == pytest.approx(expected_delta)
        assert expected_delta < 0
        assert "降低" in report.conclusion


class TestAnalyzeDateWindow:
    def test_records_outside_days_window_are_excluded(self, db_path):
        import daytrading_db as dbmod
        from dt_counterfactual import analyze

        db = dbmod.DaytradingDB(db_path)
        old_date = (date.today() - timedelta(days=200)).isoformat()
        db.save_predictions([_pred(dbmod, old_date, "9999", "Old", 9, "long", target_price=110)])
        db.save_review(_review(dbmod, old_date, "9999", 100.0, 108.0, "hit_target", target_price=110))

        report = analyze(db_path, days=90)
        assert report.n_trading_days == 0
        assert report.strategy_a.n == 0


# ── 邊界情況：不可 crash ──────────────────────────────────────────────────

class TestAnalyzeEdgeCases:
    def test_empty_database_does_not_crash(self, db_path):
        import daytrading_db as dbmod
        from dt_counterfactual import analyze

        dbmod.DaytradingDB(db_path)  # 只建 schema，不塞資料
        report = analyze(db_path, days=90)

        assert report.n_trading_days == 0
        assert report.strategy_a.n == 0
        assert report.strategy_a.win_rate is None
        assert report.strategy_b.n == 0
        assert report.strategy_c.n == 0
        assert report.avg_ret_delta == 0.0
        assert "資料不足" in report.conclusion

    def test_all_skip_does_not_crash(self, db_path):
        """全部都是 action='skip'（LLM 一檔都沒選）：策略 A 應為空，
        其餘計算仍需正常完成，不得 crash。"""
        import daytrading_db as dbmod
        from dt_counterfactual import analyze

        db = dbmod.DaytradingDB(db_path)
        today = date.today().isoformat()
        db.save_predictions([
            _pred(dbmod, today, "2330", "A", 9, "skip", target_price=110, stop_loss=95),
            _pred(dbmod, today, "2603", "B", 5, "skip", target_price=55, stop_loss=48),
        ])
        db.save_review(_review(dbmod, today, "2330", 100.0, 108.0, "hit_target", target_price=110))
        db.save_review(_review(dbmod, today, "2603", 50.0, 47.0, "hit_stop", stop_loss=48))

        report = analyze(db_path, days=90)

        assert report.strategy_a.n == 0
        assert report.strategy_a.win_rate is None
        assert report.strategy_c.n == 2
        assert "資料不足" in report.conclusion
        # format_report 也不可 crash
        from dt_counterfactual import format_report
        text = format_report(report)
        assert isinstance(text, str) and text


# ── format_report：Telegram HTML 格式 ─────────────────────────────────────

class TestFormatReport:
    def test_contains_key_sections(self, db_path):
        from dt_counterfactual import analyze, format_report
        db_path = _seed_known_scenario(db_path)
        report = analyze(db_path, days=30)
        text = format_report(report)

        assert "<b>" in text
        assert "AI 實際策略" in text
        assert "無 LLM 過濾" in text
        assert "反向檢查" in text
        assert report.conclusion in text
