"""
tests/conftest.py — 測試基礎設施（全域 fixtures）
"""
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# 在任何 import 之前，把 google.genai 注入為 stub，
# 避免 ci 環境缺少套件或 cryptography ABI 不相容造成 collection error。
# ---------------------------------------------------------------------------
def _stub_google_genai() -> None:
    # 若環境已裝真的 google.genai，直接使用，不要 stub（避免蓋掉真的 google
    # namespace package，導致 google.protobuf 等其他子套件失效）。
    try:
        import google.genai  # noqa: F401
        return
    except ImportError:
        pass

    # 取得（或建立）真正的 google module，讓其他真實安裝的子套件
    # （例如 protobuf 提供的 google.protobuf，被 yfinance 等套件引用）
    # 不因為我們注入 google.genai 而壞掉。
    try:
        import google as google_mod  # 真的 google namespace package（若已安裝 protobuf 等）
    except ImportError:
        google_mod = types.ModuleType("google")
        google_mod.__path__ = []  # 使其行為類似 namespace package
        sys.modules.setdefault("google", google_mod)

    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")

    # 最小化 stub：讓 ai_client.py 的 top-level import 不 raise
    genai_mod.Client = MagicMock
    types_mod.Tool = MagicMock
    types_mod.GoogleSearch = MagicMock
    types_mod.GenerateContentConfig = MagicMock
    genai_mod.types = types_mod

    google_mod.genai = genai_mod
    sys.modules.setdefault("google.genai", genai_mod)
    sys.modules.setdefault("google.genai.types", types_mod)

_stub_google_genai()

# feedparser stub（test 環境不裝真實 feedparser）
sys.modules.setdefault("feedparser", MagicMock())


@pytest.fixture(autouse=True)
def _isolate_planset(monkeypatch):
    """預設讓 load_pending_planset 回傳 None，
    讓測試不依賴本機 data/pending_planset.json 檔案。
    明確 patch 它的測試（test_telegram_plan_execution.py）會覆蓋此設定。
    """
    import telegram_bot
    monkeypatch.setattr(telegram_bot, "load_pending_planset", lambda *a, **kw: None)
