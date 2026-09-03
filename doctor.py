"""
doctor.py — 系統自檢

    python3 doctor.py              終端輸出，全部通過回 0
    python3 doctor.py --telegram   同時推播摘要（08:20 晨間就緒檢查用）
    python3 doctor.py --quiet      只印失敗與警告

為什麼需要
----------
這三天的每一個故障都可以被一次自檢抓到，但我們是靠人工一行一行 grep 找出來的：

    2026-08-21  data/HALT 被誤觸 → 12 天靜默停擺，log 乾淨、process 活著
    2026-09-02  launchd 讀不到外接 SSD（macOS 隱私權限）
    2026-09-02  pgrep 抓到正在關閉的 process，誤判「已在執行」
    2026-09-03  kbars 單次查詢上限 30 天（原有 bug，數月未被發現）
    2026-09-03  Kbars 是 pydantic 物件、沒有 .get → 指標永遠算不出來
    2026-09-03  .env 行內註解變成密碼雜湊 → 登入永遠失敗且無提示

其中最有價值的是 check_market_data：**真的抓一支股票、驗證欄位齊全且數值
合理**。上面最後三項都會被它當場攔下。2861 條測試沒抓到那兩個 kbars 的
bug，因為所有測試的假物件都是 dict——mock 測不出來的問題，只有真的打一次
API 才會現形。

設計原則
--------
每個檢查獨立、回傳 CheckResult、絕不拋出。自檢本身因為一項爆炸而全滅，
就完全失去意義了。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

# doctor 是獨立入口，必須自己載入 .env——否則 check_env 會把「設定好但沒載入」
# 誤報成「缺少必填」。2026-09-04 首次在正式機執行就發生了，而健檢工具誤報
# 比不檢查更糟：它會讓人不再相信它說的話。
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:      # 沒裝 python-dotenv 時退化為只讀環境變數
    pass

log = logging.getLogger(__name__)

OK = "ok"
WARN = "warn"
FAIL = "fail"

_ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌"}


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    detail: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# 各項檢查
# ══════════════════════════════════════════════════════════════════════════════

def check_workdir() -> CheckResult:
    """工作目錄可讀寫、關鍵檔案在線。

    專案放在外接 SSD 上，launchd 曾因 macOS 隱私權限而完全讀不到
    （"Operation not permitted"），腳本連執行都執行不到。
    """
    import pathlib
    here = pathlib.Path(__file__).resolve().parent
    missing = [f for f in ("main.py", "executor.py", "config.py")
               if not (here / f).exists()]
    if missing:
        return CheckResult("工作目錄", FAIL, "關鍵檔案不存在",
                           f"缺少 {', '.join(missing)}（路徑 {here}）")
    try:
        probe = here / "data" / ".doctor_write_probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok")
        probe.unlink()
    except Exception as e:
        return CheckResult("工作目錄", FAIL, "data/ 不可寫", f"{here}: {e}")
    return CheckResult("工作目錄", OK, str(here))


#: start_all.sh / launchd 都是用裸 `python3` 啟動 main.py 的，
#: 而 ./doctor 有 venv 時會優先用 venv——兩者可能是不同的直譯器。
_TRADING_LAUNCHER = "python3"


def _shioaji_version_of(executable: str, timeout: float = 20.0):
    """回傳該直譯器裡的 shioaji 版本；沒裝回 None，問不到回 ""。"""
    import subprocess
    code = ("import shioaji,sys;"
            "sys.stdout.write(getattr(shioaji,'__version__','?'))")
    try:
        p = subprocess.run([executable, "-c", code], capture_output=True,
                           text=True, timeout=timeout)
    except Exception:
        return ""
    if p.returncode != 0:
        return None
    return (p.stdout or "").strip() or "?"


def check_interpreter() -> CheckResult:
    """自檢用的 Python 必須和真正交易的那個是同一個。

    ./doctor 有 venv 就用 venv，start_all.sh 卻一律用裸 `python3`。
    兩邊套件版本不同時，doctor 全綠但 main.py 用著舊版 shioaji 被券商
    503 拒絕——健檢說的話就完全不算數了。這比任何一項單獨的檢查都優先。
    """
    import shutil
    import sys

    mine = os.path.realpath(sys.executable)
    theirs_path = shutil.which(_TRADING_LAUNCHER)
    if not theirs_path:
        return CheckResult("直譯器一致性", WARN, "PATH 上找不到 python3",
                           "start_all.sh 用裸 python3 啟動 main.py，"
                           "cron/launchd 的 PATH 可能與互動 shell 不同。")
    theirs = os.path.realpath(theirs_path)

    my_ver = _shioaji_version_of(sys.executable)
    if theirs == mine:
        return CheckResult("直譯器一致性", OK,
                           f"{sys.executable}（shioaji {my_ver or '未安裝'}）")

    their_ver = _shioaji_version_of(theirs_path)
    if my_ver == their_ver:
        return CheckResult("直譯器一致性", OK,
                           f"兩個直譯器的 shioaji 同為 {my_ver or '未安裝'}",
                           f"自檢：{sys.executable}｜交易：{theirs_path}")
    return CheckResult(
        "直譯器一致性", FAIL,
        "自檢與交易用的 Python 不是同一個，套件版本也不同",
        f"自檢 {sys.executable} → shioaji {my_ver or '未安裝'}；"
        f"交易 {theirs_path} → shioaji {their_ver or '未安裝'}。"
        "本次自檢結果不能代表 main.py 的實際狀況——"
        "請對交易用的那個直譯器升級（或讓 start_all.sh 改用 venv）。",
    )


def check_halt() -> CheckResult:
    """HALT 旗標。存在時必須大聲——這是 12 天靜默停擺的元凶。"""
    try:
        from halt import _HALT_FILE, is_halted
        if is_halted():
            try:
                content = _HALT_FILE.read_text().strip()
            except Exception:
                content = "(讀取失敗)"
            return CheckResult(
                "緊急暫停旗標", FAIL, "系統處於緊急暫停狀態，不會執行買進",
                f"{_HALT_FILE}｜內容：{content}｜傳「恢復系統」或刪除該檔案解除",
            )
        return CheckResult("緊急暫停旗標", OK, "未暫停")
    except Exception as e:
        return CheckResult("緊急暫停旗標", WARN, "無法判斷", str(e))


#: 缺少會讓核心流程無法運作的設定
_REQUIRED_ENV = ("SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY")
#: 缺少只影響通知的設定
_OPTIONAL_ENV = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
#: 值以 # 開頭代表 .env 的行內註解被當成設定值
_COMMENT_PRONE = ("USER_PASSWORD_HASH", "USER_PASSWORD", "SHIOAJI_API_KEY",
                  "SHIOAJI_SECRET_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def check_env() -> CheckResult:
    """.env 必填參數與格式。

    特別檢查「行內註解被當成設定值」：python-dotenv 對**空值**不會剝掉行內
    註解，`KEY=   # 說明` 會讓 KEY 等於那段說明文字。實際發生過——
    USER_PASSWORD_HASH 變成一段中文，登入永遠失敗且沒有任何提示。
    """
    problems: list[str] = []

    for k in _COMMENT_PRONE:
        v = os.getenv(k)
        if v and v.strip().startswith("#"):
            problems.append(f"{k} 的值是行內註解（{v.strip()[:30]}…）——"
                            f"請把註解移到獨立的一行")

    missing = [k for k in _REQUIRED_ENV if not (os.getenv(k) or "").strip()]
    if missing:
        problems.append(f"缺少必填：{', '.join(missing)}")

    soft = [k for k in _OPTIONAL_ENV if not (os.getenv(k) or "").strip()]

    if problems:
        return CheckResult(".env 設定", FAIL, problems[0], "；".join(problems))
    if soft:
        return CheckResult(".env 設定", WARN, f"缺少通知設定：{', '.join(soft)}",
                           "Telegram 推播將無法送出")
    return CheckResult(".env 設定", OK, "必填參數齊全")


#: 永豐金伺服器拒絕過舊 SDK 時的回應特徵
_SDK_OUTDATED_HINTS = ("503", "update the version of shioaji")


def _looks_outdated(err: str) -> bool:
    low = err.lower()
    return all(h.lower() in low for h in _SDK_OUTDATED_HINTS)


def check_shioaji(api=None) -> CheckResult:
    """券商連線。

    特別區分「SDK 版本過舊」：永豐金會回 503 並要求升級。那不是憑證問題，
    但若籠統顯示「請確認 SHIOAJI_API_KEY」，會把人導向完全錯誤的方向。
    """
    if api is None:
        try:
            import shioaji_session
            api = shioaji_session.get_api()
        except Exception as e:
            if _looks_outdated(str(e)):
                return CheckResult(
                    "Shioaji 連線", FAIL, "SDK 版本過舊，券商拒絕連線",
                    "執行 `pip install -U shioaji` 升級。"
                    "升級後 login 不再接受 fetch_contract，本專案已相容。",
                )
            return CheckResult("Shioaji 連線", FAIL, "連線失敗", str(e))
    if api is None:
        # ensure_connected 吞掉例外只回 None，原因要從 session 撈——
        # 否則 503（SDK 過舊）會被顯示成「請確認憑證」，方向完全錯。
        err = ""
        try:
            import shioaji_session
            err = shioaji_session.last_error() or ""
        except Exception:
            pass
        if _looks_outdated(err):
            return CheckResult(
                "Shioaji 連線", FAIL, "SDK 版本過舊，券商拒絕連線",
                "執行 `pip install -U shioaji` 升級。"
                "升級後 login 不再接受 fetch_contract，本專案已相容。",
            )
        return CheckResult("Shioaji 連線", FAIL, "無法連線",
                           (err + "｜" if err else "")
                           + "請確認 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY")
    sim = os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"
    paper = os.getenv("PAPER_TRADING", "true").lower() != "false"
    return CheckResult("Shioaji 連線", OK,
                       f"已連線（{'模擬' if sim else '真實'}報價／"
                       f"{'紙上' if paper else '真實'}下單）")


#: 指標中這些欄位為 None 會讓 _assess_day_trading 拋 TypeError
_CRITICAL_INDICATORS = ("current_price", "RSI", "volume_ratio", "ATR", "MA20")


def check_market_data(api=None, code: str = "2330") -> CheckResult:
    """★ 真的抓一支股票，驗證欄位齊全且數值合理。

    這是整套自檢裡最有價值的一項。2026-09-03 早上的兩個 bug——kbars 單次
    上限 30 天、Kbars 是 pydantic 物件沒有 .get——都會被它當場攔下，而
    2861 條單元測試全部通過卻抓不到，因為測試的假物件都是 dict。
    """
    if api is None:
        try:
            import shioaji_session
            api = shioaji_session.get_api()
        except Exception as e:
            return CheckResult("行情資料", FAIL, "無法取得連線", str(e))
    if api is None:
        return CheckResult("行情資料", FAIL, "無 Shioaji 連線，無法驗證行情")

    from datetime import date, timedelta

    import shioaji_history as sh
    from technical_indicators import INDICATOR_HISTORY_DAYS

    end = date.today()
    try:
        df = sh.fetch_daily(api, code, end - timedelta(days=INDICATOR_HISTORY_DAYS), end)
    except Exception as e:
        return CheckResult("行情資料", FAIL, f"{code} 歷史抓取拋出例外", str(e))

    if df is None or len(df) == 0:
        return CheckResult("行情資料", FAIL, f"{code} 取不到任何日線",
                           "常見原因：kbars 單次查詢超過 30 天上限、"
                           "歷史資料額度用盡、或回傳結構不如預期")
    if len(df) < 80:
        return CheckResult("行情資料", FAIL,
                           f"{code} 日線僅 {len(df)} 根，不足 80 根",
                           "calculate_indicators 需要至少 80 根，指標會算不出來")

    try:
        from technical_indicators import calculate_indicators
        ind = calculate_indicators(df)
    except Exception as e:
        return CheckResult("行情資料", FAIL, f"{code} 指標計算失敗", str(e))

    nones = [k for k in _CRITICAL_INDICATORS if ind.get(k) is None]
    if nones:
        return CheckResult("行情資料", FAIL, f"{code} 指標有缺值",
                           f"以下欄位為 None：{', '.join(nones)}"
                           f"（會讓 _assess_day_trading 拋 TypeError）")

    price = ind.get("current_price") or 0
    if price <= 0:
        return CheckResult("行情資料", FAIL, f"{code} 現價為 {price}",
                           "0 不是價格，是「沒有報價」——報價 session 可能未就緒")

    return CheckResult(
        "行情資料", OK, f"{code} 日線 {len(df)} 根，指標正常",
        f"現價 {price}　RSI {ind.get('RSI')}　量比 {ind.get('volume_ratio')}",
    )


def check_quota(api=None) -> CheckResult:
    """Shioaji 歷史資料每日額度。

    額度用盡的徵狀是「Token is expired」加上無止盡的「Not ready」，完全看
    不出真正原因——2026-09-02 為此找了整個早上。
    """
    if api is None:
        try:
            import shioaji_session
            api = shioaji_session.get_api(connect=False)
        except Exception:
            api = None
    try:
        import shioaji_history as sh
        u = sh.usage_report(api)
    except Exception as e:
        return CheckResult("歷史資料額度", WARN, "查詢失敗", str(e))
    if not u:
        return CheckResult("歷史資料額度", WARN, "無法取得用量",
                           "SDK 可能不支援 usage()，或尚未連線")
    pct = u.get("remaining_pct")
    msg = f"已用 {u['used_mb']:,.0f} / {u['limit_mb']:,.0f} MB"
    if pct is not None:
        msg += f"（剩 {pct:.0f}%）"
        if pct < 20:
            return CheckResult("歷史資料額度", WARN, msg,
                               "剩餘不足 20%，今日候選可能偏少")
    return CheckResult("歷史資料額度", OK, msg)


#: 缺了會讓對應功能靜默失效的欄位（CREATE TABLE IF NOT EXISTS 補不上）
_REQUIRED_COLUMNS = {
    "dt_prediction_log": ("was_correct", "source", "sim_pnl", "reviewed_at"),
}


def check_db_schema(review_db: str | None = None) -> CheckResult:
    """資料庫 schema 完整性。

    CREATE TABLE IF NOT EXISTS 對既有資料表是 no-op，光靠建表語句永遠補不上
    新欄位——正式環境的 daytrading_review.db 就因此缺了 was_correct，讓
    adaptive_scorer 每天靜默失敗（no such column）長達數月。
    """
    import sqlite3
    path = review_db or "data/daytrading_review.db"
    if not os.path.exists(path):
        return CheckResult("資料庫 schema", WARN, "複盤資料庫尚未建立", path)

    missing: list[str] = []
    try:
        con = sqlite3.connect(path)
        for table, cols in _REQUIRED_COLUMNS.items():
            exists = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            if not exists:
                missing.append(f"{table}（整張表不存在）")
                continue
            have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            for c in cols:
                if c not in have:
                    missing.append(f"{table}.{c}")
        con.close()
    except Exception as e:
        return CheckResult("資料庫 schema", FAIL, "檢查失敗", str(e))

    if missing:
        return CheckResult("資料庫 schema", FAIL, "缺少欄位",
                           "、".join(missing) + "（開啟一次 DaytradingDB 即會自動補上）")
    return CheckResult("資料庫 schema", OK, "欄位齊全")


def check_processes() -> CheckResult:
    """main.py 是否真的在執行。

    2026-09-02 的教訓：start_all.sh 誤判「已在執行」而跳過啟動，照樣印出
    「全部啟動完成」，但 main.py 根本沒起來，當天 8:30 完全沒跑。
    """
    import subprocess
    try:
        out = subprocess.run(
            ["pgrep", "-f", r"[Pp]ython[0-9.]* main\.py"],
            capture_output=True, text=True, timeout=10,
        )
        pids = [p for p in out.stdout.split() if p.strip()]
    except Exception as e:
        return CheckResult("main.py 程序", WARN, "無法查詢", str(e))
    if not pids:
        return CheckResult("main.py 程序", FAIL, "未在執行",
                           "8:30 選股、盤中監控、13:35 複盤都不會發生")
    return CheckResult("main.py 程序", OK, f"執行中（PID {', '.join(pids)}）")


def check_ai_keys() -> CheckResult:
    """AI 金鑰是否設定（不實際呼叫，避免產生費用）。"""
    have = [k for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
            if (os.getenv(k) or "").strip()]
    if not have:
        return CheckResult("AI 金鑰", WARN, "未設定任何 AI 金鑰",
                           "當沖深度分析與晨報將降級為規則模式")
    return CheckResult("AI 金鑰", OK, "、".join(have))


def check_telegram(send=None) -> CheckResult:
    """Telegram 推播（實際送一則訊息）。"""
    chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not chat:
        return CheckResult("Telegram", WARN, "未設定 TELEGRAM_CHAT_ID")
    try:
        if send is None:
            from telegram_bot import send_text as send
        send(chat, "🩺 doctor：Telegram 推播測試")
    except Exception as e:
        return CheckResult("Telegram", FAIL, "推播失敗", str(e))
    return CheckResult("Telegram", OK, f"已送出至 {chat}")


# ══════════════════════════════════════════════════════════════════════════════
# 彙總
# ══════════════════════════════════════════════════════════════════════════════

_CHECKS = (
    ("check_workdir", ()),
    ("check_interpreter", ()),
    ("check_env", ()),
    ("check_halt", ()),
    ("check_shioaji", ("api",)),
    ("check_market_data", ("api",)),
    ("check_quota", ("api",)),
    ("check_db_schema", ()),
    ("check_processes", ()),
    ("check_ai_keys", ()),
)


def run_all_checks(api=None, include_telegram: bool = False) -> list[CheckResult]:
    """依序執行所有檢查。

    每一項都獨立包 try/except：自檢本身因為一項爆炸而全滅，就完全失去意義。
    """
    results: list[CheckResult] = []
    for fn_name, needs in _CHECKS:
        fn = globals().get(fn_name)
        try:
            results.append(fn(api=api) if "api" in needs else fn())
        except Exception as e:
            results.append(CheckResult(fn_name, FAIL, "檢查本身拋出例外", str(e)))
    if include_telegram:
        try:
            results.append(check_telegram())
        except Exception as e:
            results.append(CheckResult("Telegram", FAIL, "檢查本身拋出例外", str(e)))
    return results


def exit_code(results: list[CheckResult]) -> int:
    """只有 fail 算紅燈——warn 不該讓自動化流程失敗。"""
    return 1 if any(r.status == FAIL for r in results) else 0


def format_report(results: list[CheckResult], quiet: bool = False) -> str:
    lines = ["🩺 QUANT·AI 系統自檢", "━" * 32]
    for r in results:
        if quiet and r.status == OK:
            continue
        lines.append(f"{_ICON.get(r.status, '?')} {r.name}：{r.message}")
        if r.detail and r.status != OK:
            lines.append(f"     {r.detail}")
    n_fail = sum(1 for r in results if r.status == FAIL)
    n_warn = sum(1 for r in results if r.status == WARN)
    lines.append("━" * 32)
    lines.append("全部通過 ✅" if not n_fail and not n_warn
                 else f"失敗 {n_fail}　警告 {n_warn}")
    return "\n".join(lines)


def format_telegram(results: list[CheckResult]) -> str:
    """推播格式：**壞的排在前面**，不能讓人往下捲才看到問題。"""
    order = {FAIL: 0, WARN: 1, OK: 2}
    ordered = sorted(results, key=lambda r: order.get(r.status, 3))
    n_fail = sum(1 for r in results if r.status == FAIL)

    head = ("🚨 <b>系統自檢：發現問題</b>" if n_fail
            else "🩺 <b>QUANT·AI 今日已就緒</b>")
    lines = [head, "━━━━━━━━━━━━━━━━"]
    for r in ordered:
        lines.append(f"{_ICON.get(r.status, '?')} {r.name}：{r.message}")
        if r.detail and r.status == FAIL:
            lines.append(f"   <i>{r.detail}</i>")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="QUANT·AI 系統自檢")
    ap.add_argument("--telegram", action="store_true", help="推播結果到 Telegram")
    ap.add_argument("--quiet", action="store_true", help="只顯示失敗與警告")
    ap.add_argument("--no-market", action="store_true",
                    help="略過行情驗證（不消耗歷史資料額度）")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.no_market:
        global _CHECKS
        _CHECKS = tuple(c for c in _CHECKS if c[0] != "check_market_data")

    results = run_all_checks(include_telegram=args.telegram)
    print(format_report(results, quiet=args.quiet))

    if args.telegram:
        try:
            from telegram_bot import send_text
            chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
            if chat:
                send_text(chat, format_telegram(results))
        except Exception as e:
            print(f"⚠️ 推播失敗：{e}")

    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
