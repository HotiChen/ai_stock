"""
tests/conftest.py — 測試基礎設施（全域 fixtures）
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_planset(monkeypatch):
    """預設讓 load_pending_planset 回傳 None，
    讓測試不依賴本機 data/pending_planset.json 檔案。
    明確 patch 它的測試（test_telegram_plan_execution.py）會覆蓋此設定。
    """
    import telegram_bot
    monkeypatch.setattr(telegram_bot, "load_pending_planset", lambda *a, **kw: None)
