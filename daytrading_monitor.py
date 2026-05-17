"""
daytrading_monitor.py — 當沖盤中即時監控

09:05 推播後把 AI 進場區間/目標/停損存檔，
盤中每 30 分鐘掃描一次即時價格，觸發進場/停利/停損警報（每種只發一次）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_PATH = "data/daytrading_positions.json"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class DaytradingPosition:
    code:         str
    name:         str
    entry_low:    Optional[float]
    entry_high:   Optional[float]
    target_price: Optional[float]
    stop_loss:    Optional[float]
    dt_score:     int
    status:       str = "watching"          # watching | entered | target_hit | stoploss_hit
    alerts_sent:  list = field(default_factory=list)


@dataclass
class DaytradingAlert:
    code:       str
    name:       str
    alert_type: str    # "entry" | "target" | "stoploss"
    price:      float
    message:    str
    time:       datetime


# ── Pure alert check ──────────────────────────────────────────────────────────

def check_position_alerts(
    pos: DaytradingPosition,
    current_price: float,
) -> list[DaytradingAlert]:
    """規則式警報檢查，回傳本次新觸發的警報（已發過的不重複）。"""
    alerts: list[DaytradingAlert] = []
    now = datetime.now()

    def _alert(alert_type: str, message: str) -> DaytradingAlert:
        return DaytradingAlert(
            code=pos.code, name=pos.name,
            alert_type=alert_type, price=current_price,
            message=message, time=now,
        )

    # 停利優先
    if pos.target_price is not None and "target" not in pos.alerts_sent:
        if current_price >= pos.target_price:
            alerts.append(_alert("target",
                f"{pos.name} 達目標價 {pos.target_price:,.1f}，考慮停利！"))

    # 停損
    if pos.stop_loss is not None and "stoploss" not in pos.alerts_sent:
        if current_price <= pos.stop_loss:
            alerts.append(_alert("stoploss",
                f"{pos.name} 跌破停損 {pos.stop_loss:,.1f}，建議停損出場！"))

    # 進場區間（僅在尚未進場時）
    if (pos.entry_low is not None and pos.entry_high is not None
            and "entry" not in pos.alerts_sent):
        if pos.entry_low <= current_price <= pos.entry_high:
            alerts.append(_alert("entry",
                f"{pos.name} 進入進場區間 {pos.entry_low:,.1f}–{pos.entry_high:,.1f}，可考慮進場"))

    return alerts


# ── Price fetcher ─────────────────────────────────────────────────────────────

def fetch_current_price(code: str, api=None) -> Optional[float]:
    """取即時股價：Shioaji → yfinance → None。"""
    if api is not None:
        try:
            contract = api.Contracts.Stocks.get(code)
            snaps = api.snapshots([contract])
            if snaps:
                return round(float(snaps[0].close), 2)
        except Exception as e:
            log.debug("Shioaji price fetch failed for %s: %s", code, e)

    try:
        import yfinance as yf
        df = yf.Ticker(f"{code}.TW").history(period="1d", interval="1m")
        if df is not None and not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    except Exception as e:
        log.debug("yfinance price fetch failed for %s: %s", code, e)

    return None


# ── Persistence ───────────────────────────────────────────────────────────────

def save_daytrading_positions(
    positions: list[DaytradingPosition],
    path: str = _DEFAULT_PATH,
) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "code":         p.code,
            "name":         p.name,
            "entry_low":    p.entry_low,
            "entry_high":   p.entry_high,
            "target_price": p.target_price,
            "stop_loss":    p.stop_loss,
            "dt_score":     p.dt_score,
            "status":       p.status,
            "alerts_sent":  p.alerts_sent,
        }
        for p in positions
    ]
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_daytrading_positions(path: str = _DEFAULT_PATH) -> list[DaytradingPosition]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [
            DaytradingPosition(
                code=d["code"],
                name=d["name"],
                entry_low=d.get("entry_low"),
                entry_high=d.get("entry_high"),
                target_price=d.get("target_price"),
                stop_loss=d.get("stop_loss"),
                dt_score=d.get("dt_score", 0),
                status=d.get("status", "watching"),
                alerts_sent=d.get("alerts_sent", []),
            )
            for d in data
        ]
    except Exception as e:
        log.error("load_daytrading_positions failed: %s", e)
        return []


# ── Main monitor pass ─────────────────────────────────────────────────────────

def run_daytrading_monitor(
    api=None,
    path: str = _DEFAULT_PATH,
) -> list[DaytradingAlert]:
    """掃描所有當沖候選股即時價格，回傳新觸發的警報清單。"""
    positions = load_daytrading_positions(path=path)
    if not positions:
        return []

    all_alerts: list[DaytradingAlert] = []

    for pos in positions:
        price = fetch_current_price(pos.code, api=api)
        if price is None:
            log.debug("無法取得 %s 即時價格，跳過", pos.code)
            continue

        alerts = check_position_alerts(pos, price)

        for a in alerts:
            pos.alerts_sent.append(a.alert_type)

        all_alerts.extend(alerts)

    if all_alerts:
        save_daytrading_positions(positions, path=path)

    return all_alerts


# ── Telegram message formatter ────────────────────────────────────────────────

_ALERT_EMOJI = {
    "entry":    "🟢",
    "target":   "🎯",
    "stoploss": "🛑",
}


def format_alerts_message(alerts: list[DaytradingAlert]) -> str:
    if not alerts:
        return ""

    now_str = datetime.now().strftime("%H:%M")
    lines = [f"⚡ <b>當沖監控警報 {now_str}</b>", "━━━━━━━━━━━━━━━━"]

    for a in alerts:
        emoji = _ALERT_EMOJI.get(a.alert_type, "📢")
        type_label = {"entry": "進場", "target": "停利", "stoploss": "停損"}.get(a.alert_type, a.alert_type)
        lines.append(
            f"{emoji} <b>{a.code} {a.name}</b>　{type_label}\n"
            f"   現價 {a.price:,.1f}　{a.message}"
        )

    return "\n".join(lines)
