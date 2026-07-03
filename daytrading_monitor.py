"""
daytrading_monitor.py — 當沖盤中即時監控 + 追蹤停利邏輯

持倉狀態：
  watching → 尚未進場，監控進場區間 / 靜態停損停利（發 Telegram 警報）
  active   → 已進場，啟動追蹤停利機制（觸發時 sell_required=True）
  closed   → 已出場，不再監控

追蹤停利規則（優先順序）：
  1. 跌幅 >= stop_loss_pct        → 停損出場
  2. 漲幅 >= take_profit_pct      → 漲停前天花板出場
  3. 時間 >= force_close_time     → 強制平倉（避免交割）
  4. 峰值漲幅 >= trailing_start_pct 且峰值回落 >= trailing_gap_pct → 追蹤停利
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

from atomic_json import atomic_write_json

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
    status:       str = "watching"    # watching | active | closed | skipped
    alerts_sent:  list = field(default_factory=list)
    ai_summary:   str  = ""           # 8:30 盤前 AI 建議摘要（供 9:05 再確認使用）
    # ── 實際成交後填寫 ─────────────────────────────────────────────────────
    entry_price:  Optional[float] = None   # 實際買入均價
    peak_price:   Optional[float] = None   # 持倉期間最高達到的價格
    quantity:     int = 0                  # 持倉數量（張 or 股）
    lot_type:     str = "common"           # "common" | "intraday_odd"


@dataclass
class DaytradingAlert:
    code:          str
    name:          str
    alert_type:    str     # "entry"|"target"|"stoploss"|"trailing_stop"|
                           # "take_profit_ceiling"|"stop_loss"|"force_close"
    price:         float
    message:       str
    time:          datetime
    sell_required: bool = False   # True = 需執行實際賣單


@dataclass
class TrailingStopResult:
    """check_trailing_stop() 的純函數回傳值。"""
    should_sell:   bool
    reason:        str    # "" | "stop_loss" | "take_profit_ceiling" |
                          #    "trailing_stop" | "force_close"
    message:       str
    trigger_price: float


# ── Pure functions ────────────────────────────────────────────────────────────

def check_trailing_stop(
    entry_price: float,
    current_price: float,
    peak_price: float,
    config,                            # DaytradingConfig（鴨型別避免循環 import）
    current_time: Optional[datetime] = None,
) -> TrailingStopResult:
    """
    追蹤停利 / 停損純函數，不修改任何外部狀態。

    參數
    ----
    entry_price   : 買入均價
    current_price : 當前即時價格
    peak_price    : 持倉期間已觀測到的最高價（呼叫前需先更新）
    config        : DaytradingConfig
    current_time  : 測試用時間注入（None = datetime.now()）

    規則優先順序
    ------------
    1. 跌幅 >= stop_loss_pct        → 停損出場
    2. 漲幅 >= take_profit_pct      → 天花板停利（漲停前出場）
    3. 時間 >= force_close_time     → 強制平倉
    4. 峰值漲幅 >= trailing_start_pct
       且峰值回落 >= trailing_gap_pct → 追蹤停利觸發
    """
    now = current_time or datetime.now()
    gain_pct = (current_price - entry_price) / entry_price * 100
    peak_gain_pct = (peak_price - entry_price) / entry_price * 100
    peak_drop_pct = (current_price - peak_price) / peak_price * 100

    def _sell(reason: str, msg: str) -> TrailingStopResult:
        return TrailingStopResult(
            should_sell=True, reason=reason,
            message=msg, trigger_price=current_price,
        )

    # 1. 停損
    if gain_pct <= -config.stop_loss_pct:
        return _sell(
            "stop_loss",
            f"停損觸發：跌幅 {gain_pct:.2f}%（停損線 -{config.stop_loss_pct}%）",
        )

    # 2. 天花板停利
    if gain_pct >= config.take_profit_pct:
        return _sell(
            "take_profit_ceiling",
            f"天花板停利：漲幅 {gain_pct:+.2f}%（上限 +{config.take_profit_pct}%）",
        )

    # 3. 強制平倉時間
    h, m = map(int, config.force_close_time.split(":"))
    if now.time() >= dtime(h, m):
        return _sell(
            "force_close",
            f"強制平倉：到達 {config.force_close_time}，避免交割",
        )

    # 4. 追蹤停利（峰值漲幅達門檻後，從峰值回落觸發）
    if peak_gain_pct >= config.trailing_start_pct:
        if peak_drop_pct <= -config.trailing_gap_pct:
            return _sell(
                "trailing_stop",
                f"追蹤停利：高點 +{peak_gain_pct:.2f}% → 回落 {abs(peak_drop_pct):.2f}%，"
                f"現漲幅 {gain_pct:+.2f}%",
            )

    return TrailingStopResult(
        should_sell=False, reason="", message="", trigger_price=current_price,
    )


def update_peak_price(pos: DaytradingPosition, current_price: float) -> bool:
    """若 current_price 超過 peak_price 則更新，回傳是否有更新。"""
    if pos.peak_price is None or current_price > pos.peak_price:
        pos.peak_price = current_price
        return True
    return False


def check_position_alerts(
    pos: DaytradingPosition,
    current_price: float,
    config=None,        # DaytradingConfig；active 狀態時必須提供
) -> list[DaytradingAlert]:
    """規則式警報檢查，回傳本次新觸發的警報（已發過的不重複）。

    - watching：進場區間 / 靜態停損停利（原有邏輯，向下相容）
    - active  ：追蹤停利系統（需要 config；呼叫前請先 update_peak_price）
    """
    alerts: list[DaytradingAlert] = []
    now = datetime.now()

    def _alert(
        alert_type: str,
        message: str,
        sell_required: bool = False,
    ) -> DaytradingAlert:
        return DaytradingAlert(
            code=pos.code, name=pos.name,
            alert_type=alert_type, price=current_price,
            message=message, time=now,
            sell_required=sell_required,
        )

    # ── active：追蹤停利 ──────────────────────────────────────────────────────
    if pos.status == "active" and pos.entry_price is not None and config is not None:
        peak = pos.peak_price if pos.peak_price is not None else pos.entry_price
        result = check_trailing_stop(
            entry_price=pos.entry_price,
            current_price=current_price,
            peak_price=peak,
            config=config,
        )
        if result.should_sell and result.reason not in pos.alerts_sent:
            alerts.append(_alert(result.reason, result.message, sell_required=True))
        return alerts

    # ── watching：靜態停損停利 ─────────────────────────────────────────────────

    # 停利優先
    if pos.target_price is not None and "target" not in pos.alerts_sent:
        if current_price >= pos.target_price:
            alerts.append(_alert(
                "target",
                f"{pos.name} 達目標價 {pos.target_price:,.1f}，考慮停利！",
            ))

    # 停損
    if pos.stop_loss is not None and "stoploss" not in pos.alerts_sent:
        if current_price <= pos.stop_loss:
            alerts.append(_alert(
                "stoploss",
                f"{pos.name} 跌破停損 {pos.stop_loss:,.1f}，建議停損出場！",
            ))

    # 進場區間（僅在尚未進場時）
    if (pos.entry_low is not None and pos.entry_high is not None
            and "entry" not in pos.alerts_sent):
        if pos.entry_low <= current_price <= pos.entry_high:
            alerts.append(_alert(
                "entry",
                f"{pos.name} 進入進場區間 {pos.entry_low:,.1f}–{pos.entry_high:,.1f}，可考慮進場",
            ))

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
            "ai_summary":   p.ai_summary,
            "entry_price":  p.entry_price,
            "peak_price":   p.peak_price,
            "quantity":     p.quantity,
            "lot_type":     p.lot_type,
        }
        for p in positions
    ]
    atomic_write_json(path, data, indent=2)


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
                ai_summary=d.get("ai_summary", ""),
                entry_price=d.get("entry_price"),
                peak_price=d.get("peak_price"),
                quantity=d.get("quantity", 0),
                lot_type=d.get("lot_type", "common"),
            )
            for d in data
        ]
    except Exception as e:
        log.error("load_daytrading_positions failed: %s", e)
        return []


def mark_position_entered(
    pos: DaytradingPosition,
    entry_price: float,
    quantity: int,
    lot_type: str = "common",
) -> None:
    """買單成交後，把持倉標記為 active 並記錄進場資訊。"""
    pos.status      = "active"
    pos.entry_price = entry_price
    pos.peak_price  = entry_price   # 初始峰值 = 進場價
    pos.quantity    = quantity
    pos.lot_type    = lot_type


def mark_position_skipped(pos: DaytradingPosition) -> None:
    """9:05 開盤確認時過濾掉的候選：標記為 skipped，不再進場。"""
    pos.status = "skipped"


# ── Main monitor pass ─────────────────────────────────────────────────────────

def run_daytrading_monitor(
    api=None,
    path: str = _DEFAULT_PATH,
    config=None,        # DaytradingConfig；None 時 active 持倉跳過追蹤停利
) -> list[DaytradingAlert]:
    """掃描所有當沖持倉即時價格，回傳新觸發的警報清單。

    警報中 sell_required=True 的項目代表已觸發出場訊號，
    呼叫方應負責執行賣單並將 pos.status 標為 'closed'。
    """
    positions = load_daytrading_positions(path=path)
    if not positions:
        return []

    all_alerts: list[DaytradingAlert] = []
    changed = False

    for pos in positions:
        if pos.status == "closed":
            continue

        price = fetch_current_price(pos.code, api=api)
        if price is None:
            log.debug("無法取得 %s 即時價格，跳過", pos.code)
            continue

        # active 持倉：先更新峰值，再檢查追蹤停利
        if pos.status == "active":
            if update_peak_price(pos, price):
                changed = True

        alerts = check_position_alerts(pos, price, config=config)

        for a in alerts:
            pos.alerts_sent.append(a.alert_type)
            changed = True

        all_alerts.extend(alerts)

    if changed:
        save_daytrading_positions(positions, path=path)

    return all_alerts


# ── Telegram message formatter ────────────────────────────────────────────────

_ALERT_EMOJI = {
    "entry":               "🟢",
    "target":              "🎯",
    "stoploss":            "🛑",
    "stop_loss":           "🛑",
    "take_profit_ceiling": "🎯",
    "trailing_stop":       "📈",
    "force_close":         "⏰",
}

_SELL_LABEL = {
    "stop_loss":           "停損出場",
    "take_profit_ceiling": "天花板停利",
    "trailing_stop":       "追蹤停利",
    "force_close":         "強制平倉",
}


def format_alerts_message(alerts: list[DaytradingAlert]) -> str:
    if not alerts:
        return ""

    now_str = datetime.now().strftime("%H:%M")
    lines = [f"⚡ <b>當沖監控警報 {now_str}</b>", "━━━━━━━━━━━━━━━━"]

    for a in alerts:
        emoji = _ALERT_EMOJI.get(a.alert_type, "📢")
        if a.sell_required:
            type_label = _SELL_LABEL.get(a.alert_type, "出場訊號")
        else:
            type_label = {
                "entry":    "進場",
                "target":   "停利",
                "stoploss": "停損",
            }.get(a.alert_type, a.alert_type)

        sell_tag = " <b>⚠️ 建議立即賣出</b>" if a.sell_required else ""
        lines.append(
            f"{emoji} <b>{a.code} {a.name}</b>　{type_label}{sell_tag}\n"
            f"   現價 {a.price:,.1f}　{a.message}"
        )

    return "\n".join(lines)
