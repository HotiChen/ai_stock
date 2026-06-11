"""
preflight.py — 啟動前環境檢查模組

在 main.py 啟動時自動執行，確保必要環境變數、服務連線皆正常。
任何單項檢查失敗只記錄 log；critical=True 的項目失敗代表系統無法正常運作。

用法（獨立執行）：
    python3 preflight.py          # 執行全部檢查，失敗時 exit(1)
    python3 main.py               # main.py 在啟動時呼叫 run_preflight()
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List

try:
    import requests  # type: ignore  # 可被測試 patch
except ImportError:  # 若環境未安裝則在 check_telegram 內處理
    requests = None  # type: ignore

log = logging.getLogger(__name__)

# repo 根目錄（測試可 monkeypatch）
_REPO_ROOT = Path(__file__).resolve().parent


@dataclass
class CheckResult:
    """單項檢查結果。"""
    name: str
    ok: bool
    detail: str
    critical: bool


# ── 個別檢查函式（每個皆回傳 CheckResult，絕不 raise）────────────────────────────


def check_env() -> CheckResult:
    """檢查必要環境變數是否設定。"""
    try:
        missing_critical: list[str] = []
        missing_noncritical: list[str] = []
        notes: list[str] = []

        # Critical：AI 分析核心
        if not os.getenv("ANTHROPIC_API_KEY"):
            missing_critical.append("ANTHROPIC_API_KEY")

        # Critical：Telegram 推播（兩個都要有）
        if not os.getenv("TELEGRAM_BOT_TOKEN"):
            missing_critical.append("TELEGRAM_BOT_TOKEN")
        if not os.getenv("TELEGRAM_CHAT_ID"):
            missing_critical.append("TELEGRAM_CHAT_ID")

        # Non-critical：Shioaji（無則模擬模式）
        if not os.getenv("SHIOAJI_API_KEY"):
            missing_noncritical.append("SHIOAJI_API_KEY")
            notes.append("SHIOAJI_API_KEY 未設定，將以模擬模式運行")
        if not os.getenv("SHIOAJI_SECRET_KEY"):
            missing_noncritical.append("SHIOAJI_SECRET_KEY")
            notes.append("SHIOAJI_SECRET_KEY 未設定，將以模擬模式運行")

        # Non-critical：Gemini（youtube_analyzer 使用）
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not gemini_key:
            missing_noncritical.append("GEMINI_API_KEY / GOOGLE_API_KEY")
            notes.append("GEMINI_API_KEY 未設定，YouTube 分析功能停用")

        if missing_critical:
            detail = f"缺少必要環境變數：{', '.join(missing_critical)}"
            if notes:
                detail += f"；{'; '.join(notes)}"
            return CheckResult(name="環境變數", ok=False, detail=detail, critical=True)

        if missing_noncritical:
            detail = "; ".join(notes) if notes else f"部分選用環境變數未設定：{', '.join(missing_noncritical)}"
            return CheckResult(name="環境變數", ok=True, detail=detail, critical=False)

        return CheckResult(name="環境變數", ok=True, detail="所有必要環境變數已設定", critical=False)

    except Exception as e:
        return CheckResult(name="環境變數", ok=False, detail=f"檢查時例外：{e}", critical=True)


def check_telegram() -> CheckResult:
    """透過 Telegram getMe API 驗證 Bot Token 是否有效。"""
    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return CheckResult(
                name="Telegram",
                ok=False,
                detail="TELEGRAM_BOT_TOKEN 未設定",
                critical=True,
            )

        url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            bot_name = data.get("result", {}).get("username", "unknown")
            return CheckResult(
                name="Telegram",
                ok=True,
                detail=f"Bot 連線正常：@{bot_name}",
                critical=False,
            )
        else:
            return CheckResult(
                name="Telegram",
                ok=False,
                detail=f"getMe 回傳 HTTP {resp.status_code}，Token 可能無效",
                critical=True,
            )
    except Exception as e:
        return CheckResult(
            name="Telegram",
            ok=False,
            detail=f"連線失敗：{e}",
            critical=True,
        )


def check_shioaji() -> CheckResult:
    """檢查 Shioaji 連線（僅在 API key 設定時才嘗試連線）。"""
    try:
        api_key = os.getenv("SHIOAJI_API_KEY", "")
        secret_key = os.getenv("SHIOAJI_SECRET_KEY", "")

        if not api_key or not secret_key:
            return CheckResult(
                name="Shioaji",
                ok=True,
                detail="未設定，模擬模式（SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY 皆未設定）",
                critical=False,
            )

        simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"

        # 延遲匯入，捕捉所有例外
        try:
            from monitor_agent import ensure_connected  # type: ignore
            api = ensure_connected(api_key, secret_key, simulation=simulation)
        except Exception as e:
            return CheckResult(
                name="Shioaji",
                ok=False,
                detail=f"連線模組載入失敗：{e}",
                critical=False,
            )

        if api is not None:
            mode = "模擬" if simulation else "真實"
            return CheckResult(
                name="Shioaji",
                ok=True,
                detail=f"已連線（{mode}模式）",
                critical=False,
            )
        else:
            return CheckResult(
                name="Shioaji",
                ok=False,
                detail="連線失敗（ensure_connected 回傳 None），報價可能為 0.0，系統將繼續以降級模式運行",
                critical=False,
            )
    except Exception as e:
        return CheckResult(
            name="Shioaji",
            ok=False,
            detail=f"檢查時例外：{e}",
            critical=False,
        )


def check_db() -> CheckResult:
    """檢查資料庫目錄可寫入，並以 PRAGMA quick_check 驗證 SQLite 完整性。"""
    try:
        db_path = os.getenv("DB_PATH", "data/research.db")
        db_file = Path(db_path)
        parent = db_file.parent

        # 目錄可寫入（若不存在則嘗試建立）
        if not parent.exists():
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return CheckResult(
                    name="資料庫",
                    ok=False,
                    detail=f"無法建立資料目錄 {parent}：{e}",
                    critical=True,
                )

        if not os.access(str(parent), os.W_OK):
            return CheckResult(
                name="資料庫",
                ok=False,
                detail=f"資料目錄 {parent} 無寫入權限",
                critical=True,
            )

        # SQLite 連線與完整性
        conn = sqlite3.connect(str(db_file))
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
            integrity = result[0] if result else "unknown"
        finally:
            conn.close()

        if integrity == "ok":
            return CheckResult(
                name="資料庫",
                ok=True,
                detail=f"SQLite 正常（{db_path}）",
                critical=False,
            )
        else:
            return CheckResult(
                name="資料庫",
                ok=False,
                detail=f"PRAGMA quick_check 回傳：{integrity}",
                critical=True,
            )
    except Exception as e:
        return CheckResult(
            name="資料庫",
            ok=False,
            detail=f"檢查時例外：{e}",
            critical=True,
        )


def check_playbook() -> CheckResult:
    """確認 research_playbook.md 存在且包含兩個 ADAPTIVE 標記。"""
    try:
        playbook_path = _REPO_ROOT / "research_playbook.md"

        if not playbook_path.exists():
            return CheckResult(
                name="Playbook",
                ok=False,
                detail="research_playbook.md 不存在，PlaybookUpdateJob 的 AI 指引將缺失",
                critical=False,
            )

        text = playbook_path.read_text(encoding="utf-8")
        has_start = "ADAPTIVE-SECTION-START" in text
        has_end = "ADAPTIVE-SECTION-END" in text

        if has_start and has_end:
            return CheckResult(
                name="Playbook",
                ok=True,
                detail="research_playbook.md 存在且 ADAPTIVE 標記完整",
                critical=False,
            )
        else:
            missing = []
            if not has_start:
                missing.append("ADAPTIVE-SECTION-START")
            if not has_end:
                missing.append("ADAPTIVE-SECTION-END")
            return CheckResult(
                name="Playbook",
                ok=False,
                detail=f"ADAPTIVE 標記缺失：{', '.join(missing)}",
                critical=False,
            )
    except Exception as e:
        return CheckResult(
            name="Playbook",
            ok=False,
            detail=f"檢查時例外：{e}",
            critical=False,
        )


# ── 彙總函式 ───────────────────────────────────────────────────────────────────


def run_preflight(send_telegram: bool = True) -> List[CheckResult]:
    """執行所有啟動前檢查，記錄摘要 log，並選擇性送出 Telegram 通知。

    - 所有檢查皆在內部捕捉例外，此函式本身絕不 raise。
    - ``send_telegram=True`` 且 Telegram 連線正常時，送出繁體中文摘要訊息。
    - 回傳 CheckResult 清單供呼叫端使用（例如 has_critical_failure()）。
    """
    results: List[CheckResult] = []

    checks = [check_env, check_telegram, check_shioaji, check_db, check_playbook]
    for fn in checks:
        try:
            result = fn()
        except Exception as e:
            # 防禦性：即使 check fn 本身不應 raise，此處也兜底
            result = CheckResult(
                name=getattr(fn, "__name__", "unknown"),
                ok=False,
                detail=f"檢查函式本身拋出例外：{e}",
                critical=True,
            )
        results.append(result)

    # ── 記錄摘要 log ──────────────────────────────────────────────────────────
    log.info("── 啟動前環境檢查 ──────────────────────")
    for r in results:
        icon = "✅" if r.ok else ("❌" if r.critical else "⚠️")
        level = logging.INFO if r.ok else (logging.ERROR if r.critical else logging.WARNING)
        log.log(level, "%s [%s] %s", icon, r.name, r.detail)

    critical_count = sum(1 for r in results if not r.ok and r.critical)
    warn_count = sum(1 for r in results if not r.ok and not r.critical)
    if critical_count:
        log.error("啟動前檢查完成：%d 項嚴重問題，%d 項警告", critical_count, warn_count)
    elif warn_count:
        log.warning("啟動前檢查完成：0 項嚴重問題，%d 項警告", warn_count)
    else:
        log.info("啟動前檢查完成：所有項目正常")

    # ── 選擇性 Telegram 通知 ──────────────────────────────────────────────────
    if send_telegram:
        # 只在 Telegram 本身 ok 時才嘗試發送
        tg_result = next((r for r in results if r.name == "Telegram"), None)
        if tg_result and tg_result.ok:
            try:
                from telegram_bot import send_text  # type: ignore  # lazy import
                chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
                if chat_id:
                    lines = ["<b>🔍 啟動前環境檢查</b>"]
                    for r in results:
                        icon = "✅" if r.ok else ("❌" if r.critical else "⚠️")
                        lines.append(f"{icon} <b>{r.name}</b>：{r.detail}")
                    if critical_count:
                        lines.append(f"\n<b>⛔ {critical_count} 項嚴重問題，系統功能可能不完整。</b>")
                    elif warn_count:
                        lines.append(f"\n⚠️ {warn_count} 項警告（非嚴重，系統繼續運行）。")
                    else:
                        lines.append("\n✅ 所有檢查通過，系統正常啟動。")
                    send_text(chat_id, "\n".join(lines))
            except Exception as e:
                log.warning("preflight: Telegram 通知發送失敗（已忽略）：%s", e)

    return results


def has_critical_failure(results: List[CheckResult]) -> bool:
    """回傳 True 若結果清單中有任何 critical=True 且 ok=False 的項目。"""
    return any(r.critical and not r.ok for r in results)


# ── 獨立執行入口 ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(message)s",
    )

    results = run_preflight(send_telegram=True)

    if has_critical_failure(results):
        print("\n❌ 啟動前檢查發現嚴重問題，請修復後再啟動 main.py。")
        sys.exit(1)
    else:
        print("\n✅ 啟動前檢查通過。")
        sys.exit(0)
