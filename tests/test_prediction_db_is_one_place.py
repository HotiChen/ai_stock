"""預測、複盤、學習必須讀寫同一個資料庫。

2026-08-19 實際狀態
-------------------
    data/research.db          dt_prediction_log  85 筆，已檢討 0 筆
    data/daytrading_review.db dt_prediction_log   0 筆

四個交易日、85 筆預測，outcome 與 was_correct 全部是 NULL，daily_open /
daily_close / reviewed_at 也全是 NULL。從來沒有一筆預測被檢討過。

於是 post_market 的學習步驟每天印：

    累積預測：0 筆 / 0 個交易日
    評分 4-5：資料不足。評分 6-7：資料不足。評分 8-10：資料不足。

不是資料不夠，是它在看另一個檔案。

    main.py:1692  build_daytrading_report(api=api, db_path=DB_PATH)  ← research.db
    main.py:1841  run_daytrading_review()                            ← 沒傳，用預設

寫入端由呼叫端指定路徑（DB_PATH，env 可覆蓋），五個讀取端各自硬寫
"data/daytrading_review.db"。跟 telegram_bot 的 load_daily_trades() 和
期貨的 fetch_futures_premium() 是同一種毛病：少傳那個決定它去哪裡找的參數。

只要路徑還能各自定義，就還會再錯一次。所以由擁有這張表的 daytrading_db
單一定義，其餘模組一律引用。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent

#: 會讀 dt_prediction_log 的模組
_READERS = [
    "daytrading_review.py",
    "adaptive_scorer.py",
    "dt_counterfactual.py",
    "notion_reporter.py",
    "dt_paper_trade.py",
]


def test_daytrading_db_owns_the_path():
    import daytrading_db

    assert hasattr(daytrading_db, "DEFAULT_DB_PATH"), \
        "擁有 dt_prediction_log 的模組沒有公開這張表的位置"


def test_the_owner_follows_the_same_env_var_as_main():
    """main.py 用 DB_PATH，這裡也必須跟著，否則換環境就再分裂一次。"""
    import importlib, os
    import daytrading_db

    os.environ["DB_PATH"] = "data/somewhere_else.db"
    try:
        importlib.reload(daytrading_db)
        assert daytrading_db.DEFAULT_DB_PATH == "data/somewhere_else.db"
    finally:
        os.environ.pop("DB_PATH", None)
        importlib.reload(daytrading_db)


def test_default_is_where_the_predictions_actually_are():
    import daytrading_db

    assert daytrading_db.DEFAULT_DB_PATH == "data/research.db", \
        f"預設不是實際存放 85 筆預測的地方：{daytrading_db.DEFAULT_DB_PATH}"


# ── 讀取端不可自己另外寫死一個路徑 ────────────────────────────────────────────

@pytest.mark.parametrize("module", _READERS)
def test_readers_do_not_hardcode_their_own_prediction_db(module):
    src = (_REPO / module).read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))

    assert '"data/daytrading_review.db"' not in code, (
        f"{module} 自己寫死了預測資料庫路徑。寫入端跟著 DB_PATH 走，"
        "這裡寫死就會讀到空的檔案——85 筆預測從來沒被檢討過就是這樣來的。"
    )


@pytest.mark.parametrize("module", _READERS)
def test_readers_take_the_path_from_the_owner(module):
    src = (_REPO / module).read_text(encoding="utf-8")

    assert "DEFAULT_DB_PATH" in src, \
        f"{module} 沒有引用 daytrading_db.DEFAULT_DB_PATH"


# ── 呼叫端 ───────────────────────────────────────────────────────────────────

def test_main_reviews_the_database_it_writes_to():
    """main.py 存預測時傳 DB_PATH，複盤時也要指向同一個地方。"""
    src = (_REPO / "main.py").read_text(encoding="utf-8")
    m = re.search(r"run_daytrading_review\(([^)]*)\)", src)

    assert m, "main.py 沒有呼叫 run_daytrading_review"
    assert "DB_PATH" in m.group(1), (
        f"複盤沒有指定資料庫，會用預設值：run_daytrading_review({m.group(1)})"
    )
