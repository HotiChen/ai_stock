"""
tests/conftest.py — 測試基礎設施（全域 fixtures）
"""
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# 測試隔離：中和外部服務憑證，讓測試在「有 .env 的開發機」上也不會連到真實的
# Shioaji / Telegram / Anthropic（否則測試會又慢又可能花錢、佔用檔案描述元，
# 甚至真的訂閱到市場行情——這是 macOS 上 Errno 24 Too many open files 的元凶
# 之一）。CI / 遠端無 .env 時本段等同 no-op。
# 必須在任何專案模組 import 之前執行，且要壓過各模組自己的 load_dotenv()。
# ---------------------------------------------------------------------------
def _isolate_external_credentials() -> None:
    for _k in (
        "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_USER_ID",
        "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "LOCAL_LLM_BASE_URL", "BACKEND_INTERNAL_TOKEN",
    ):
        os.environ.pop(_k, None)
    os.environ.setdefault("SHIOAJI_SIMULATION", "true")
    os.environ.setdefault("PAPER_TRADING", "true")

    # 專案多數模組在 import 時呼叫 load_dotenv(override=True)，會用開發機的
    # .env 蓋回上面清掉的值；測試期間把 load_dotenv 換成 no-op，確保隔離。
    try:
        import dotenv
        dotenv.load_dotenv = lambda *a, **k: False  # type: ignore[assignment]
        dotenv.main.load_dotenv = lambda *a, **k: False  # type: ignore[attr-defined]
    except Exception:
        pass

_isolate_external_credentials()

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
