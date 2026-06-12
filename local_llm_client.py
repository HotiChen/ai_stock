"""
local_llm_client.py — 本機 LLM 客戶端（Ollama / LM Studio OpenAI-compatible API）

設計原則（TIM 批准）：
  - 本機模型僅作「廉價前置過濾器」，**永遠不在決策路徑上**。
  - FAIL-OPEN：停用、連線失敗、逾時、回應格式錯誤 → 行為與未啟用完全一致（保留全部 headlines）。
  - 預設停用：只有設定 LOCAL_LLM_BASE_URL 環境變數後才啟動。

環境變數：
  LOCAL_LLM_BASE_URL  — 例如 http://localhost:11434/v1（Ollama）或
                        http://localhost:1234/v1（LM Studio）；
                        未設定 = 停用。
  LOCAL_LLM_MODEL     — 預設 "gemma3:12b"
  LOCAL_LLM_TIMEOUT   — 整數秒，預設 30
"""
from __future__ import annotations

import json
import os
import re
from typing import List

import requests

import logger

_log = logger.get_logger("local_llm_client")

# ── 環境變數讀取（模組層級，支援測試 reload 讓 monkeypatch 生效） ──────────────


def _base_url() -> str | None:
    return os.environ.get("LOCAL_LLM_BASE_URL") or None


def _model() -> str:
    return os.environ.get("LOCAL_LLM_MODEL", "gemma3:12b")


def _timeout() -> int:
    try:
        return int(os.environ.get("LOCAL_LLM_TIMEOUT", "30"))
    except (ValueError, TypeError):
        return 30


# ── 公開 API ──────────────────────────────────────────────────────────────────


def is_enabled() -> bool:
    """LOCAL_LLM_BASE_URL 已設定且非空白時回傳 True。"""
    return bool(_base_url())


def chat(prompt: str, system: str | None = None) -> str | None:
    """向本機 LLM 發送單輪 chat 請求（OpenAI-compatible /chat/completions）。

    回傳 assistant 的文字內容；任何失敗（停用／逾時／連線錯誤／
    non-2xx／格式錯誤）一律回傳 None，不 raise。
    """
    base = _base_url()
    if not base:
        return None

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": _model(),
        "messages": messages,
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=_timeout())
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.Timeout:
        _log.warning("local LLM 請求逾時（%ss）", _timeout())
        return None
    except requests.ConnectionError as exc:
        _log.warning("local LLM 連線失敗：%s", exc)
        return None
    except requests.HTTPError as exc:
        _log.warning("local LLM HTTP 錯誤：%s", exc)
        return None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        _log.warning("local LLM 回應格式無法解析：%s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        _log.warning("local LLM 未知錯誤：%s", exc)
        return None


# ── 領域工具 ──────────────────────────────────────────────────────────────────


def filter_stock_relevant_headlines(headlines: list[dict]) -> list[dict]:
    """過濾與台股無關的 RSS 標題（廉價前置過濾器）。

    行為規則：
    1. 停用或 headlines 為空 → 原樣回傳。
    2. 單次批次 prompt：列出所有標題編號，要求模型回傳保留的索引 JSON 陣列。
    3. 防禦性解析：從回覆中擷取第一個 [...] → json.loads → 驗證 int 且在範圍內。
       任何解析問題 → fail-open，回傳原始 list。
    4. 保護性檢查：若過濾結果為空但輸入不為空 → fail-open，回傳原始 list
       （全部砍掉比保留多餘 token 更危險）。
    5. 記錄保留／丟棄數量。
    """
    if not is_enabled() or not headlines:
        return headlines

    # 組裝批次 prompt（繁體中文）
    numbered = "\n".join(
        f"{i}. {h.get('title', '')}" for i, h in enumerate(headlines)
    )
    prompt = (
        "以下是台股相關新聞標題，請判斷哪些與「台股個股、總體經濟（影響台股）、"
        "產業動態」有直接關聯。\n\n"
        f"{numbered}\n\n"
        "請只回傳一個 JSON 整數陣列，內容為**保留**的標題編號（從 0 開始），"
        "例如：[0, 2, 5]\n"
        "若全部相關請回傳所有索引。不要輸出任何其他文字。"
    )

    reply = chat(prompt)
    if reply is None:
        _log.info("local LLM filter：chat() 失敗，fail-open 保留全部 %d 則", len(headlines))
        return headlines

    # 解析：擷取第一個 [...] 陣列
    kept_indices = _parse_index_array(reply, max_index=len(headlines) - 1)
    if kept_indices is None:
        _log.info("local LLM filter：回應無法解析，fail-open 保留全部 %d 則", len(headlines))
        return headlines

    if not kept_indices:
        _log.info(
            "local LLM filter：過濾後為空（原 %d 則），保護性 fail-open，保留全部",
            len(headlines),
        )
        return headlines

    result = [headlines[i] for i in kept_indices]
    dropped = len(headlines) - len(result)
    _log.info(
        "local LLM filter：%d 則保留，%d 則過濾（共 %d 則）",
        len(result), dropped, len(headlines),
    )
    return result


# ── 內部工具 ──────────────────────────────────────────────────────────────────


def _parse_index_array(text: str, max_index: int) -> list[int] | None:
    """從文字中擷取第一個 JSON 整數陣列並驗證範圍。

    回傳已驗證的整數 list；若無法解析或元素型態錯誤回傳 None。
    超出 max_index 的索引會靜默略過（不讓整體解析失敗）。
    """
    match = re.search(r"\[([^\]]*)\]", text)
    if not match:
        return None

    raw = "[" + match.group(1) + "]"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(parsed, list):
        return None

    result: list[int] = []
    for item in parsed:
        if not isinstance(item, int):
            return None  # 型態錯誤 → 整體解析失敗
        if 0 <= item <= max_index:
            result.append(item)
        # 超出範圍的索引靜默略過

    return result
