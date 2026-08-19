"""交易日各階段健康檢查 —— 壞了就主動用 Telegram 喊。

用法（由 launchd 排程呼叫）::

    python3 health_check.py --stage premarket    # 08:35
    python3 health_check.py --stage entry        # 09:15
    python3 health_check.py --stage settlement   # 13:40
    python3 health_check.py --summary            # 13:45，當日總結

為什麼要有這支
--------------
Tim 盤中在工地，無法即時查看終端機或 log，唯一的管道是 Telegram。
但系統目前所有失敗都只寫進 logs/ai_stock.log：

  - 2026-08-12 大盤指數取不到而回 0.0 → AI 判定「無方向」放棄全部 8 檔
  - 2026-08-13 當沖報告評分階段 TypeError → 整天沒有任何推播

兩天都是「Telegram 一片安靜」，只能靠**沒收到訊息**反推系統壞了。
沒收到訊息也可能是今天真的沒有標的，兩者無法區分——這正是要解決的。

設計取捨
--------
* **獨立腳本，不掛在 main.py 裡。** main.py 自己死掉正是要偵測的情況之一，
  由它來報告自己的死亡並不可靠。
* **只讀 log，不碰交易狀態。** 檢查程式不該有任何機會影響下單。
* **健康時完全不發訊息。** 每天固定收到「一切正常」會很快被忽略，
  真正的告警就淹沒在雜訊裡。只有每日總結是無條件發送。
* **「全部放棄」不算故障。** 那是合法的交易判斷；誤報會訓練人忽略告警。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as _time
from pathlib import Path
from typing import Callable, Iterable, Optional

_LOG_PATH = Path(__file__).resolve().parent / "logs" / "ai_stock.log"


@dataclass
class StageResult:
    stage: str
    label: str
    ok: bool
    detail: str
    #: 這個階段的時段還沒結束——現在沒紀錄是正常的，不是故障。
    #: 少了這個狀態，早上跑 --summary 會把「收盤複盤」報成 ❌，
    #: 誤報會讓真告警失去可信度。
    pending: bool = False


@dataclass(frozen=True)
class _Stage:
    label: str
    #: 只要出現任一個就算這個階段有跑完
    success_markers: tuple[str, ...]
    #: 出現任一個就判定故障，即使成功標記也在
    failure_markers: tuple[str, ...]
    #: 什麼時候才該做出判定（HH:MM）。早於它一律 pending。
    #: 與 window 分開是必要的：window 決定哪些 log 算數（保持寬鬆），
    #: deadline 決定何時判定。2026-08-19 08:35 的檢查比 08:36:13 的紀錄
    #: 早了 73 秒，就推了一則假的「盤前失敗」給在工地的人。
    deadline: str
    #: 只採計這個時段（HH:MM）內的紀錄。
    #: 沒有時間窗的話，凌晨殘留的紀錄會被當成當天正式執行——2026-08-14 00:38
    #: 就有一筆殘留的 "Premarket: 1 approved"，讓什麼都還沒跑的凌晨回報一切正常。
    window: tuple[str, str]


STAGES: dict[str, _Stage] = {
    "premarket": _Stage(
        label="盤前當沖預測",
        success_markers=("Premarket:", "今日當沖預測"),
        failure_markers=("DayTrading report push failed",),
        window=("08:25", "09:00"),
        deadline="08:45",
    ),
    "entry": _Stage(
        label="開盤確認",
        # 「0 繼續 / 8 放棄」與「無 watching 持倉」都算跑完——當天沒有好標的
        # 是正常結果，不是故障。
        success_markers=("DT 9:05 確認",),
        failure_markers=("DT 9:05 確認通知失敗", "DT 9:10 下單失敗"),
        window=("09:00", "09:30"),
        deadline="09:15",
    ),
    "settlement": _Stage(
        label="收盤複盤",
        success_markers=("PostMarket:",),
        failure_markers=("PostMarketJob 失敗",),
        window=("13:30", "14:05"),
        deadline="13:50",
    ),
}


def _in_window(line: str, window: tuple[str, str]) -> bool:
    """該行的時間是否落在 [start, end) 之間。時間格式為 'YYYY-MM-DD HH:MM:SS,mmm'。"""
    m = re.match(r"^\S+ (\d{2}:\d{2})", line)
    if not m:
        return False
    return window[0] <= m.group(1) < window[1]


def _today_lines(lines: Iterable[str], today: str,
                 window: Optional[tuple[str, str]] = None) -> list[str]:
    """只留今天、且落在指定時段內的紀錄。

    日期過濾擋掉「昨天的成功讓今天看起來健康」；時段過濾擋掉「凌晨的殘留
    紀錄被當成當天正式執行」。兩者都實際發生過。
    """
    todays = [ln for ln in lines if ln.startswith(today)]
    if window is None:
        return todays
    return [ln for ln in todays if _in_window(ln, window)]


def _deadline_has_passed(deadline: str, now: _time) -> bool:
    h, m = (int(x) for x in deadline.split(":"))
    return now >= _time(h, m)


def check_stage(
    stage: str,
    lines: Iterable[str],
    today: Optional[str] = None,
    now: Optional[_time] = None,
) -> StageResult:
    """依 log 判定某階段是否正常結束。

    ``now`` 用來分辨「時段還沒到」與「時段過了卻沒紀錄」。不傳就用現在時間。
    """
    spec = STAGES[stage]
    today = today or date.today().isoformat()
    now = now if now is not None else datetime.now().time()
    todays = _today_lines(lines, today, spec.window)

    for marker in spec.failure_markers:
        hit = next((ln for ln in todays if marker in ln), None)
        if hit:
            # 取訊息本體，去掉時間戳與 logger 名稱，讓告警短而可讀
            detail = re.sub(r"^\S+ \S+ \[[^\]]+\] \w+: ", "", hit).strip()
            return StageResult(stage, spec.label, False, detail)

    if any(m in ln for ln in todays for m in spec.success_markers):
        return StageResult(stage, spec.label, True, "")

    if not _deadline_has_passed(spec.deadline, now):
        return StageResult(
            stage, spec.label, False,
            f"尚未到判定時間（{spec.deadline}）",
            pending=True,
        )

    return StageResult(
        stage, spec.label, False,
        f"{spec.window[0]}–{spec.window[1]} 之間沒有任何紀錄"
        "（main.py 可能未啟動，或排程未觸發）",
    )


# ── 訊息 ─────────────────────────────────────────────────────────────────────

def build_alert(result: StageResult, today: Optional[str] = None) -> str:
    today = today or date.today().isoformat()
    return (
        "🚨 <b>系統告警</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"階段：<b>{result.label}</b>\n"
        f"日期：{today}\n\n"
        f"<code>{result.detail}</code>\n\n"
        "<i>此階段未正常完成，今日後續流程可能連帶受影響。</i>"
    )


def build_summary(results: list[StageResult], today: Optional[str] = None) -> str:
    today = today or date.today().isoformat()
    # pending 是「時段還沒到」，不算未完成——早上跑總結時不該長得像故障。
    failed = [r for r in results if not r.ok and not r.pending]
    head = "⚠️ <b>今日有階段未完成</b>" if failed else "✅ <b>今日系統正常</b>"

    lines = [head, "━━━━━━━━━━━━━━━━", f"日期：{today}", ""]
    for r in results:
        icon = "✅" if r.ok else ("⏳" if r.pending else "❌")
        lines.append(f"{icon} {r.label}")
        if not r.ok:
            lines.append(f"    <code>{r.detail}</code>")
    return "\n".join(lines)


def _send_telegram(text: str) -> None:
    from telegram_bot import send_text
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if chat_id:
        send_text(chat_id, text)


def maybe_alert(
    result: StageResult,
    today: Optional[str] = None,
    send: Callable[[str], None] = _send_telegram,
) -> None:
    """只在故障時送出告警。正常或時段未到時保持安靜。"""
    if not result.ok and not result.pending:
        send(build_alert(result, today=today))


# ── CLI ──────────────────────────────────────────────────────────────────────

def _read_log() -> list[str]:
    if not _LOG_PATH.exists():
        return []
    return _LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()


def main() -> int:
    parser = argparse.ArgumentParser(description="交易日階段健康檢查")
    parser.add_argument("--stage", choices=sorted(STAGES), help="檢查單一階段")
    parser.add_argument("--summary", action="store_true", help="送出當日總結")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

    lines = _read_log()
    today = date.today().isoformat()

    if args.summary:
        results = [check_stage(s, lines, today) for s in ("premarket", "entry", "settlement")]
        _send_telegram(build_summary(results, today))
        for r in results:
            print(f"  {'✅' if r.ok else '❌'} {r.label}  {r.detail}")
        return 0

    if args.stage:
        result = check_stage(args.stage, lines, today)
        maybe_alert(result, today)
        icon = "✅" if result.ok else ("⏳" if result.pending else "❌")
        print(f"  {icon} {result.label}  {result.detail}")
        # pending 不是故障，回 0——否則 launchctl list 會把「還沒到時間」
        # 顯示成跟真故障一樣的非零結束碼。
        return 0 if (result.ok or result.pending) else 1

    parser.error("需指定 --stage 或 --summary")
    return 2


if __name__ == "__main__":
    sys.exit(main())
