"""intraday_monitor.py

盤中監控：每天 10:00 / 12:00 用 Gemini Flash + Search 驗證持倉 thesis，
輸出 CONTINUE / CAUTION / EXIT，並推播 Telegram。
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from ai_client import call_gemini_with_search
from morning_briefing import MorningBriefing
from portfolio import Position, SimulatedPortfolio

log = logging.getLogger(__name__)

_DEFAULT_REPORT_DIR = "data/intraday_reports"


# ── Verdict ───────────────────────────────────────────────────────────────────

class Verdict(str, Enum):
    CONTINUE = "CONTINUE"
    CAUTION  = "CAUTION"
    EXIT     = "EXIT"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PositionCheck:
    code:          str
    name:          str
    verdict:       Verdict
    reason:        str
    current_price: Optional[float]
    pnl_pct:       float


@dataclass
class IntradayReport:
    date:       date
    checked_at: datetime
    checks:     list[PositionCheck] = field(default_factory=list)

    @property
    def exit_positions(self) -> list[PositionCheck]:
        return [c for c in self.checks if c.verdict == Verdict.EXIT]

    @property
    def caution_positions(self) -> list[PositionCheck]:
        return [c for c in self.checks if c.verdict == Verdict.CAUTION]


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_position_check_prompt(
    position: Position,
    briefing: MorningBriefing,
    check_time: datetime,
) -> str:
    time_str    = check_time.strftime("%Y-%m-%d %H:%M")
    outlook_str = briefing.market_outlook or "unknown"
    analysis    = briefing.ai_analysis    or ""
    risks_str   = "、".join(briefing.key_risks)         if briefing.key_risks         else "無"
    sector_str  = "、".join(briefing.sector_focus)      if briefing.sector_focus      else "無"

    return f"""你是一位台股盤中即時監控分析師。現在時間：{time_str}

=== 待評估持倉 ===
股票代號：{position.code}
股票名稱：{position.name}
持有張數：{position.quantity} 張（均價 {position.avg_cost:.1f} 元）
原始進場理由：{position.entry_reason or "未記錄"}
進場日期：{position.entry_date}

=== 今日早晨研判 ===
市場展望：{outlook_str}
AI 分析摘要：{analysis}
主要風險：{risks_str}
關注族群：{sector_str}

=== 任務 ===
請立即搜尋 {position.code} {position.name} 最新即時動態（新聞、法人動向、技術面），
判斷原始進場 thesis 是否仍然成立。

請輸出以下 JSON（無其他文字）：
{{
  "verdict": "CONTINUE" | "CAUTION" | "EXIT",
  "reason": "100字內的判斷理由",
  "current_price": 最新股價（數字，無法取得則填 null）
}}

判斷標準：
- CONTINUE：thesis 仍成立，繼續持有
- CAUTION：出現負面訊號，需密切關注（但尚不需出場）
- EXIT：thesis 已破壞，建議停損或停利出場
"""


# ── Response parser ───────────────────────────────────────────────────────────

def parse_check_response(
    raw: str,
    code: str,
    name: str,
    avg_cost: float,
) -> PositionCheck:
    """解析 Gemini 回傳的 JSON，失敗時回傳 CAUTION。"""
    # 剝除 markdown code block
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return PositionCheck(
            code=code, name=name,
            verdict=Verdict.CAUTION,
            reason=f"回應解析失敗：{raw[:100]}",
            current_price=None,
            pnl_pct=0.0,
        )

    verdict_str = str(data.get("verdict", "")).upper()
    verdict_map = {
        "CONTINUE": Verdict.CONTINUE,
        "CAUTION":  Verdict.CAUTION,
        "EXIT":     Verdict.EXIT,
    }
    verdict = verdict_map.get(verdict_str, Verdict.CAUTION)
    reason  = str(data.get("reason", ""))

    current_price = data.get("current_price")
    if current_price is not None:
        try:
            current_price = float(current_price)
        except (TypeError, ValueError):
            current_price = None

    pnl_pct = 0.0
    if current_price and avg_cost and avg_cost > 0:
        pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 2)

    return PositionCheck(
        code=code, name=name,
        verdict=verdict, reason=reason,
        current_price=current_price, pnl_pct=pnl_pct,
    )


# ── Check single position ─────────────────────────────────────────────────────

def check_position(
    position: Position,
    briefing: MorningBriefing,
    check_time: datetime,
) -> PositionCheck:
    """呼叫 Gemini Flash + Search 驗證單一持倉 thesis。"""
    prompt = build_position_check_prompt(position, briefing, check_time)
    raw    = call_gemini_with_search(prompt)

    if not raw:
        log.warning("check_position %s: Gemini returned empty, defaulting to CAUTION", position.code)
        return PositionCheck(
            code=position.code, name=position.name,
            verdict=Verdict.CAUTION,
            reason="無法取得 AI 分析，建議人工確認",
            current_price=None, pnl_pct=0.0,
        )

    return parse_check_response(raw, position.code, position.name, position.avg_cost)


# ── Run full monitor pass ─────────────────────────────────────────────────────

def run_intraday_monitor(
    portfolio:  SimulatedPortfolio,
    briefing:   MorningBriefing,
    check_time: Optional[datetime] = None,
) -> IntradayReport:
    """對所有持倉逐一進行盤中驗證，回傳 IntradayReport。"""
    if check_time is None:
        check_time = datetime.now()

    positions: list[Position] = list(portfolio.get_positions())
    checks: list[PositionCheck] = []

    for pos in positions:
        try:
            result = check_position(pos, briefing, check_time)
        except Exception as e:
            log.error("check_position %s failed: %s", pos.code, e)
            result = PositionCheck(
                code=pos.code, name=pos.name,
                verdict=Verdict.CAUTION,
                reason=f"檢查失敗：{e}",
                current_price=None, pnl_pct=0.0,
            )
        checks.append(result)

    return IntradayReport(
        date=check_time.date(),
        checked_at=check_time,
        checks=checks,
    )


# ── Format message ────────────────────────────────────────────────────────────

_VERDICT_EMOJI = {
    Verdict.CONTINUE: "✅",
    Verdict.CAUTION:  "⚠️",
    Verdict.EXIT:     "🔴",
}


def format_monitor_message(report: IntradayReport) -> str:
    time_str = report.checked_at.strftime("%H:%M")

    if not report.checks:
        return f"📊 盤中監控 {time_str}\n\n目前空手無持倉。"

    lines = [f"📊 <b>盤中持倉監控 {time_str}</b>\n"]

    for chk in report.checks:
        emoji   = _VERDICT_EMOJI.get(chk.verdict, "❓")
        pnl_str = f"{chk.pnl_pct:+.2f}%" if chk.pnl_pct else "N/A"
        price_str = f"@{chk.current_price:.1f}" if chk.current_price else ""
        lines.append(
            f"{emoji} <b>{chk.code} {chk.name}</b> {price_str} {pnl_str}\n"
            f"   {chk.reason}"
        )

    # Summary
    exits   = len(report.exit_positions)
    cauts   = len(report.caution_positions)
    if exits:
        lines.append(f"\n🔴 <b>{exits} 個持倉建議出場，請確認！</b>")
    if cauts:
        lines.append(f"⚠️ {cauts} 個持倉需留意")

    return "\n".join(lines)


# ── Persistence ───────────────────────────────────────────────────────────────

def _report_to_dict(report: IntradayReport) -> dict:
    return {
        "date":       report.date.isoformat(),
        "checked_at": report.checked_at.isoformat(),
        "checks": [
            {
                "code":          c.code,
                "name":          c.name,
                "verdict":       c.verdict.value,
                "reason":        c.reason,
                "current_price": c.current_price,
                "pnl_pct":       c.pnl_pct,
            }
            for c in report.checks
        ],
    }


def _report_from_dict(data: dict) -> IntradayReport:
    checks = [
        PositionCheck(
            code=c["code"],
            name=c["name"],
            verdict=Verdict(c["verdict"].upper()),
            reason=c["reason"],
            current_price=c.get("current_price"),
            pnl_pct=c.get("pnl_pct", 0.0),
        )
        for c in data.get("checks", [])
    ]
    return IntradayReport(
        date=date.fromisoformat(data["date"]),
        checked_at=datetime.fromisoformat(data["checked_at"]),
        checks=checks,
    )


def save_intraday_report(
    report: IntradayReport,
    path: Optional[str] = None,
) -> None:
    if path is None:
        Path(_DEFAULT_REPORT_DIR).mkdir(parents=True, exist_ok=True)
        path = f"{_DEFAULT_REPORT_DIR}/{report.date.isoformat()}.json"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(_report_to_dict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_intraday_report(path: str) -> Optional[IntradayReport]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return _report_from_dict(data)
    except Exception as e:
        log.error("load_intraday_report failed: %s", e)
        return None


# ── Telegram push ─────────────────────────────────────────────────────────────

def send_monitor_push(report: IntradayReport) -> None:
    """發送盤中監控結果到 Telegram。"""
    try:
        from telegram_bot import send_text
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not chat_id:
            log.warning("TELEGRAM_CHAT_ID not set, skipping push")
            return
        msg = format_monitor_message(report)
        send_text(chat_id, msg)
    except Exception as e:
        log.error("send_monitor_push failed: %s", e)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    portfolio_path = "data/portfolio.json"
    today          = date.today()

    # Load portfolio
    portfolio = SimulatedPortfolio.load(portfolio_path)

    # Load today's morning briefing
    from morning_briefing import load_briefing
    briefing = load_briefing(today)
    if briefing is None:
        log.error("找不到今日早晨簡報，無法執行盤中監控")
        sys.exit(1)

    now    = datetime.now()
    report = run_intraday_monitor(portfolio, briefing, check_time=now)

    msg = format_monitor_message(report)
    print(msg)

    save_intraday_report(report)
    send_monitor_push(report)
    log.info("盤中監控完成，已存檔並推播")
