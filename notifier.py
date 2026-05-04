from __future__ import annotations

"""
Notifier — unified Telegram push for all system events.

Every decision, analysis, order, and alert flows through here.
Import and call the relevant function from any module.
"""

import os
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

from logger import get_logger

load_dotenv(override=True)

log = get_logger(__name__)

_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_VERBOSE = os.getenv("TELEGRAM_VERBOSE", "false").lower() == "true"


def _send(text: str, chat_id: str = "") -> None:
    cid = chat_id or _CHAT_ID
    if not cid or not _TOKEN:
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        if resp.status_code >= 400:
            log.warning("Telegram sendMessage HTTP %s: %s", resp.status_code, resp.text)
    except Exception:
        pass


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ── 系統事件 ──────────────────────────────────────────────────────────────────

def notify_system_start(simulation: bool) -> None:
    mode = "🟡 模擬模式" if simulation else "🔴 實盤模式"
    _send(
        f"🚀 <b>AI Stock 系統啟動</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"模式：{mode}\n\n"
        f"排程：\n"
        f"  08:30 盤前 AI 分析\n"
        f"  09:00 開盤下單\n"
        f"  13:35 收盤總結"
    )


def notify_system_stop() -> None:
    _send(f"🛑 <b>AI Stock 系統關閉</b>  {_ts()}")


# ── 盤前分析 ──────────────────────────────────────────────────────────────────

def notify_premarket_start(candidate_count: int) -> None:
    _send(
        f"🔍 <b>盤前分析開始</b>  {_ts()}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"候選股數量：{candidate_count} 檔\n"
        f"AI 模型：Haiku（快速分析）"
    )


def notify_analysis_result(  # verbose only
    code: str,
    name: str,
    signal: str,
    confidence: int,
    summary: str,
    factors: dict,
    target_price: Optional[float],
    stop_loss_price: Optional[float],
) -> None:
    emoji = {"buy": "✅", "sell": "❌", "hold": "⏸️"}.get(signal, "❓")
    signal_zh = {"buy": "買進", "sell": "賣出", "hold": "觀望"}.get(signal, signal)

    lines = [
        f"🤖 <b>AI 分析：{code} {name}</b>",
        f"━━━━━━━━━━━━━━━━",
        f"信號：{emoji} <b>{signal_zh}</b>　信心：{confidence}/10",
        f"",
        f"📝 {summary}",
        f"",
        f"<b>分析維度：</b>",
    ]
    if factors.get("historical_trend"):
        lines.append(f"  📈 技術面：{factors['historical_trend']}")
    if factors.get("news"):
        lines.append(f"  📰 新聞面：{factors['news']}")
    if factors.get("theme"):
        lines.append(f"  🏷️ 題材面：{factors['theme']}")
    if factors.get("us_market"):
        lines.append(f"  🇺🇸 美股影響：{factors['us_market']}")
    if factors.get("tw_policy"):
        lines.append(f"  🏛️ 台灣政策：{factors['tw_policy']}")

    if target_price or stop_loss_price:
        lines.append("")
        if target_price:
            lines.append(f"🎯 目標價：{target_price}")
        if stop_loss_price:
            lines.append(f"🛡️ 停損價：{stop_loss_price}")

    if _VERBOSE:
        _send("\n".join(lines))


# ── 風控 ──────────────────────────────────────────────────────────────────────

def notify_risk_approved(code: str, name: str, budget: float, reason: str = "") -> None:
    if not _VERBOSE:
        return
    note = f"\n📝 {reason}" if reason else ""
    _send(
        f"✅ <b>風控通過：{code} {name}</b>\n"
        f"預算：{budget:,.0f} 元{note}"
    )


def notify_risk_rejected(code: str, name: str, reason: str) -> None:
    if not _VERBOSE:
        return
    reason_zh = {
        "blacklisted":  "列入黑名單",
        "ex_dividend":  "今日除息",
        "limit_up":     "漲停，無法追價",
        "limit_down":   "跌停，風險過高",
    }.get(reason, reason)
    _send(
        f"🚫 <b>風控拒絕：{code} {name}</b>\n"
        f"原因：{reason_zh}"
    )


def notify_risk_summary(approved: list[dict], rejected: list[dict]) -> None:
    lines = [
        f"🛡️ <b>風控審查完成</b>  {_ts()}",
        f"━━━━━━━━━━━━━━━━",
        f"✅ 通過：{len(approved)} 檔",
        f"❌ 拒絕：{len(rejected)} 檔",
    ]
    if approved:
        lines.append("\n<b>通過清單：</b>")
        for p in approved:
            lines.append(f"  • {p['code']} {p.get('name', '')}｜{p.get('budget', 0):,.0f} 元")
    if rejected:
        lines.append("\n<b>拒絕清單：</b>")
        for p in rejected:
            reason = p.get("reason", "")
            lines.append(f"  • {p['code']} {p.get('name', '')}｜{reason}")
    _send("\n".join(lines))


# ── 開盤 ──────────────────────────────────────────────────────────────────────

def notify_market_open(index_price: float = 0, index_change_pct: float = 0) -> None:
    arrow = "▲" if index_change_pct >= 0 else "▼"
    idx_text = (
        f"加權指數：{index_price:,.0f} {arrow}{abs(index_change_pct):.2f}%"
        if index_price > 0 else "指數資料取得中..."
    )
    _send(
        f"🔔 <b>09:00 開盤</b>  {_ts()}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{idx_text}\n"
        f"開始執行今日委託..."
    )


# ── 委託 ──────────────────────────────────────────────────────────────────────

def notify_order_success(
    code: str,
    name: str,
    action: str,
    quantity: int,
    price: float,
    amount: float,
    lot_type: str,
    order_id: str,
) -> None:
    action_zh = "買進" if action == "buy" else "賣出"
    unit = "張" if lot_type == "common" else "股"
    _send(
        f"📋 <b>委託成功：{code} {name}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"方向：{action_zh}\n"
        f"數量：{quantity} {unit}\n"
        f"價格：{price:.2f} 元\n"
        f"金額：{amount:,.0f} 元\n"
        f"委託號：<code>{order_id}</code>"
    )


def notify_order_skipped(code: str, name: str, reason: str) -> None:
    _send(
        f"⏭️ <b>跳過委託：{code} {name}</b>\n"
        f"原因：{reason}"
    )


# ── 盤中監控 ──────────────────────────────────────────────────────────────────

def notify_price_alert(
    code: str,
    name: str,
    alert_type: str,
    current_price: float,
    target_price: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
) -> None:
    if alert_type == "target_hit":
        _send(
            f"🎯 <b>達到目標價！{code} {name}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"現價：{current_price:.2f}　目標：{target_price}\n"
            f"建議考慮獲利了結"
        )
    elif alert_type == "stop_loss":
        _send(
            f"🚨 <b>觸及停損！{code} {name}</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"現價：{current_price:.2f}　停損：{stop_loss_price}\n"
            f"⚠️ 請手動處理停損（系統為測試版，不會自動下單）"
        )


def notify_snapshot(code: str, name: str, price: float, change_price: float) -> None:
    if not _VERBOSE:
        return
    arrow = "▲" if change_price >= 0 else "▼"
    _send(
        f"📡 {code} {name}　"
        f"{price:.2f} {arrow}{abs(change_price):.2f}"
    )


# ── 收盤 ──────────────────────────────────────────────────────────────────────

def notify_settlement(
    plan_type: str,
    settlement: dict,
    entry_date: str = "",
) -> None:
    """推播模擬結算結果到 Telegram。"""
    total_pnl     = settlement.get("total_pnl", 0.0)
    total_pnl_pct = settlement.get("total_pnl_pct", 0.0)
    per_stock     = settlement.get("per_stock", [])
    pnl_emoji     = "📈" if total_pnl >= 0 else "📉"
    plan_zh       = {"aggressive": "積極版", "balanced": "均衡版", "conservative": "保守版"}.get(plan_type, plan_type)

    lines = [
        f"{pnl_emoji} <b>模擬結算結果</b>  {_ts()}",
        f"━━━━━━━━━━━━━━━━",
        f"策略：{plan_zh}",
        f"買入日：{entry_date}" if entry_date else "",
        f"總損益：<b>{total_pnl:+,.0f} 元（{total_pnl_pct:+.2f}%）</b>",
        f"",
        f"<b>個股明細：</b>",
    ]
    lines = [l for l in lines if l != ""]

    for s in per_stock:
        icon = "🟢" if s["pnl"] >= 0 else "🔴"
        lines.append(
            f"{icon} {s['code']} {s['name']}"
            f"　{s['entry_price']:.1f}→{s['closing_price']:.1f}"
            f"　{s['pnl']:+,.0f}元"
        )

    _send("\n".join(lines))


def notify_market_close(
    total_pnl: float,
    trade_count: int,
    review: str = "",
) -> None:
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    lines = [
        f"🔕 <b>13:35 收盤</b>  {_ts()}",
        f"━━━━━━━━━━━━━━━━",
        f"{pnl_emoji} 今日損益：<b>{total_pnl:+,.0f}</b> 元",
        f"📋 成交筆數：{trade_count} 筆",
    ]
    if review:
        lines.append(f"\n📝 {review}")
    lines.append("\n明日排程 08:30 繼續。")
    _send("\n".join(lines))
