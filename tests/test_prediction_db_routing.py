"""
tests/test_prediction_db_routing.py — 預測必須寫進「當沖複盤資料庫」

正式環境徵狀：8:30 報告 log 顯示 "daytrading_db: saved 10 predictions"，
但查 data/daytrading_review.db 的 dt_prediction_log 永遠 0 筆。

成因：daytrading_report 把自己的 db_path（research/learning 資料庫）傳給
DaytradingDB()——那是「當沖複盤資料庫」的類別，預設 data/daytrading_review.db。
於是預測全部寫進 research.db / learning.db，而三個讀取端
（daytrading_review、dt_paper_trade、adaptive_scorer）都讀 daytrading_review.db
→ 複盤沒資料、每日 #1 Pick 永遠 no_pick、adaptive_scorer 學不到東西。
"""
import sqlite3
from unittest.mock import patch

from daytrading_analyzer import DayTradingAnalysis
from daytrading_config import DaytradingConfig


def _pick(code="2330", name="台積電"):
    return {"code": code, "name": name, "confidence": 7}


def _assessment(score=8):
    return {
        "score": score, "verdict": "✅ 適合當沖", "data_ok": True,
        "reasons_good": ["量比充足"], "reasons_bad": [],
    }


def _ai(code, name, **kw):
    return DayTradingAnalysis(
        code=code, name=name, action="long", confidence=8,
        entry_low=99.0, entry_high=101.0, target_price=105.0,
        stop_loss=97.0, timing="拉回", summary="做多",
    )


def _patches(cfg):
    market = {"index_change_pct": 0.0, "futures_premium_pct": 0.0}
    indicators = {
        "current_price": 100.0, "VWAP": 99.0, "ATR": 3.0,
        "volume_ratio": 2.0, "bullish_alignment": True,
        "bearish_alignment": False, "RSI": 55.0,
    }
    return [
        patch("daytrading_report._get_stock_universe", return_value=[_pick()]),
        patch("daytrading_report._fetch_historical_win_rate", return_value=None),
        patch("daytrading_report._fetch_market", return_value=market),
        patch("daytrading_report._fetch_chip_data", return_value={}),
        patch("daytrading_report._get_indicators", return_value=indicators),
        patch("stock_query._assess_day_trading", return_value=_assessment()),
        patch("daytrading_config.load_daytrading_config", return_value=cfg),
        patch("daytrading_monitor.replace_today"),
        patch("daytrading_report.run_daytrading_analysis", side_effect=_ai),
    ]


def _count_predictions(db_file) -> int:
    """回傳該 DB 的 dt_prediction_log 筆數；表不存在視為 0。"""
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute("SELECT COUNT(*) FROM dt_prediction_log").fetchone()[0]
    except sqlite3.OperationalError:
        return 0          # no such table
    finally:
        conn.close()


class TestPredictionsGoToReviewDb:
    def test_predictions_land_in_review_db_not_research_db(self, tmp_path):
        """預測要寫進複盤庫，不能寫進傳入的 research/learning db_path。"""
        research = str(tmp_path / "research.db")
        review = str(tmp_path / "daytrading_review.db")
        cfg = DaytradingConfig(analysis_count=8, display_count=20)

        ps = _patches(cfg)
        from daytrading_report import build_daytrading_report
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8]:
            build_daytrading_report(
                api=None, db_path=research, review_db_path=review,
            )

        assert _count_predictions(review) == 1      # 複盤庫收到了
        assert _count_predictions(research) == 0    # research 庫不該有預測

    def test_review_db_defaults_to_daytrading_review_db(self, tmp_path):
        """未指定 review_db_path 時，必須用 DaytradingDB 自己的預設路徑
        （data/daytrading_review.db），而不是傳入的 db_path。"""
        research = str(tmp_path / "research.db")
        cfg = DaytradingConfig(analysis_count=8, display_count=20)

        ps = _patches(cfg)
        from daytrading_report import build_daytrading_report
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7], ps[8], \
                patch("daytrading_db.DaytradingDB") as MockDB:
            build_daytrading_report(api=None, db_path=research)

        assert MockDB.called, "應該有建立 DaytradingDB"
        for call in MockDB.call_args_list:
            args, kwargs = call
            passed = args[0] if args else kwargs.get("path")
            # 絕不可把 research/learning db_path 當成複盤庫
            assert passed != research, (
                f"DaytradingDB 不該用 research db_path 建立（收到 {passed}）"
            )
