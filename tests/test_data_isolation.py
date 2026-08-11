"""測試不得寫入正式資料目錄。

為什麼需要這個
--------------
2026-08-10 兩次證實測試會污染正式資料：

  1. data/research.db 的 daily_trades 全部 11 筆都是測試 fixture
     （台積電 賣 1000 股 @101 → 損益 +100 萬），沒有一筆真實交易。
     分兩批：7/06 一批、8/10 跑 pytest 又一批。
  2. 跑完整套件後，data/daytrading_review.db 被自動套用了 schema 遷移。

根因：十一個模組的預設路徑都是相對路徑（`data/xxx.db`），測試若沒有顯式
覆寫，開檔時就會以 repo 根目錄為基準解析，直接打到正式資料。

解法是讓每個測試在自己的臨時工作目錄執行——相對路徑自然落在 tmp 裡。
這比逐一 monkeypatch 十一個 `_DEFAULT_PATH` 可靠：新增模組時不會漏。
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: 這個檔案位於 <repo>/tests/，所以 repo 根目錄是它的祖父層。
#: 用 resolve() 以免 /Volumes 的 symlink 讓比較失準。
REPO_ROOT = Path(__file__).resolve().parent.parent


def test_cwd_is_not_the_repo_root():
    """測試執行時的工作目錄必須被導離 repo。"""
    assert Path.cwd().resolve() != REPO_ROOT, (
        "測試在 repo 根目錄執行，任何 'data/xxx.db' 的相對路徑都會寫進正式資料"
    )


def test_relative_data_path_resolves_outside_repo():
    """'data/...' 這種相對路徑必須落在 repo 之外。"""
    resolved = Path("data/whatever.db").resolve()
    assert REPO_ROOT not in resolved.parents, (
        f"相對路徑仍解析到 repo 內：{resolved}"
    )


def test_opening_default_db_does_not_touch_production():
    """用預設路徑開資料庫，不可以在 repo 的 data/ 底下生出檔案。

    這是最貼近真實污染情境的一條：測試通常不是故意去寫正式 DB，
    而是呼叫了某個「預設參數就是正式路徑」的建構子。
    """
    from daytrading_db import DaytradingDB

    production = REPO_ROOT / "data" / "daytrading_review.db"
    before = production.stat().st_mtime_ns if production.exists() else None

    DaytradingDB()  # 不傳 path，走 _DEFAULT_PATH

    after = production.stat().st_mtime_ns if production.exists() else None
    assert before == after, "以預設路徑開啟資料庫時動到了正式檔案"

    # 而且應該在臨時目錄裡真的建出檔案（確認它確實有寫東西，只是寫對地方）
    assert Path("data/daytrading_review.db").exists()


def test_writing_a_trade_stays_in_tmp(tmp_path):
    """寫入操作同樣要被關在臨時目錄內。"""
    from daytrading_db import DaytradingDB

    db = DaytradingDB()
    created = Path("data/daytrading_review.db").resolve()

    assert REPO_ROOT not in created.parents
    assert db.path == "data/daytrading_review.db", "預設路徑本身不該被改寫"
