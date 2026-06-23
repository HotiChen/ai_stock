"""
paper_trader.py — 當沖紙上追蹤模式（不下任何委託，只追蹤損益）

用途
----
使用者想驗證「8:30 選股 → 9:05 再確認 → 進場 → 盤中追蹤 → 出場」這條邏輯，
但**不需要真的下單**。本模組在 9:10 以當前市價把通過 9:05 再確認、仍為
watching 的持倉「紙上進場」（status → active），之後用**與真實當沖完全相同**
的出場邏輯（daytrading_monitor.check_position_alerts / check_trailing_stop）
追蹤，觸發停損 / 天花板停利 / 移動停利 / 強制平倉時記錄到帳本並標記 closed。

與真實下單路徑完全隔離
------------------------
  * 不呼叫 executor / place_stock_order / force_stop_loss，不碰 Shioaji 委託
  * 帳本寫入 data/paper_trades_YYYYMMDD.json，不寫 research.db 的 daily_trades，
    因此不污染「近 30 日實際勝率」等真實統計
  * 由 DaytradingConfig.paper_trade_only（env DT_PAPER_ONLY）啟用，預設關閉

持倉狀態仍沿用 daytrading_positions.json，因此既有的 10:00 / 13:00 盤中損益
訊息會自動把紙上持倉一起報出來，無需另外處理。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

# 複用真實當沖的出場邏輯與報價來源（不重寫，確保測的就是正式邏輯）
from daytrading_monitor import (
    fetch_current_price,
    update_peak_price,
    check_position_alerts,
)

log = logging.getLogger(__name__)

_LEDGER_DIR     = "data"
_SHARES_PER_LOT = 1000


# ── Ledger helpers ────────────────────────────────────────────────────────────

def _ledger_path(day: date | None = None, ddir: str = _LEDGER_DIR) -> str:
    day = day or date.today()
    return f"{ddir}/paper_trades_{day.strftime('%Y%m%d')}.json"


def _pnl(entry_price: float, exit_price: float, quantity: int, lot_type: str) -> float:
    """紙上損益（元）。common = 整張（×1000 股）；其餘視為零股（×1 股）。"""
    multiplier = _SHARES_PER_LOT if lot_type == "common" else 1
    return (exit_price - entry_price) * quantity * multiplier


def load_paper_trades(day: date | None = None, ddir: str = _LEDGER_DIR) -> list[dict]:
    """讀取當日紙上交易帳本；檔案不存在或損毀回 []。"""
    path = Path(_ledger_path(day, ddir))
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error("load_paper_trades failed: %s", e)
        return []


def record_paper_exit(
    pos,
    exit_price: float,
    reason: str,
    day: date | None = None,
    ddir: str = _LEDGER_DIR,
) -> dict:
    """把一筆紙上出場 append 進帳本，回傳該 trade dict。"""
    day = day or date.today()
    trade = {
        "date":        day.isoformat(),
        "code":        pos.code,
        "name":        pos.name,
        "entry_price": pos.entry_price,
        "exit_price":  exit_price,
        "quantity":    pos.quantity,
        "lot_type":    pos.lot_type,
        "pnl":         _pnl(pos.entry_price, exit_price, pos.quantity, pos.lot_type),
        "reason":      reason,
        "exit_time":   datetime.now().isoformat(timespec="seconds"),
    }
    path = Path(_ledger_path(day, ddir))
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = load_paper_trades(day, ddir)
    ledger.append(trade)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    return trade


# ── Entry / monitor ───────────────────────────────────────────────────────────

def paper_enter(positions, api=None, quantity: int = 1, lot_type: str = "common") -> list:
    """把 watching 持倉以當前市價紙上進場（status → active）。

    回傳實際進場的 position list（無法取得報價者跳過、維持 watching）。
    只 mutate positions，**不負責存檔**，呼叫端決定何時 save。
    """
    entered = []
    for pos in positions:
        if pos.status != "watching":
            continue
        price = fetch_current_price(pos.code, api=api)
        if price is None or price <= 0:
            log.warning("paper_enter: 無法取得 %s 報價，維持 watching", pos.code)
            continue
        pos.entry_price = price
        pos.peak_price  = price
        pos.quantity    = quantity
        pos.lot_type    = lot_type
        pos.status      = "active"
        entered.append(pos)
        log.info("paper_enter: %s %s @ %.2f ×%d", pos.code, pos.name, price, quantity)
    return entered


def paper_monitor_pass(positions, api, config, day: date | None = None,
                       ddir: str = _LEDGER_DIR) -> tuple[bool, list[dict]]:
    """對 active 紙上持倉跑一次出場檢查（複用 check_position_alerts 的真實邏輯）。

    觸發出場 → record_paper_exit + status='closed'。
    回傳 (changed, closed_trades)；changed=True 代表 positions 有變動需存檔。
    """
    changed = False
    closed: list[dict] = []
    for pos in positions:
        if pos.status != "active":
            continue
        price = fetch_current_price(pos.code, api=api)
        if price is None or price <= 0:
            continue
        if update_peak_price(pos, price):
            changed = True
        alerts = check_position_alerts(pos, price, config=config)
        sell = next((a for a in alerts if a.sell_required), None)
        if sell is not None:
            trade = record_paper_exit(pos, price, sell.alert_type, day=day, ddir=ddir)
            pos.status = "closed"
            changed = True
            closed.append(trade)
            log.info("paper exit: %s %s @ %.2f（%s）損益 %+.0f",
                     pos.code, pos.name, price, sell.alert_type, trade["pnl"])
    return changed, closed


# ── Telegram messages ─────────────────────────────────────────────────────────

_REASON_LABEL = {
    "stop_loss":           "停損",
    "take_profit_ceiling": "天花板停利",
    "trailing_stop":       "移動停利",
    "force_close":         "強制平倉",
}


def build_entry_message(entered: list) -> str:
    if not entered:
        return ""
    lines = ["📝 <b>紙上模擬進場（不下單）</b>", "━━━━━━━━━━━━━━━━"]
    for p in entered:
        lines.append(
            f"<b>{p.code} {p.name}</b>　買 {p.entry_price:,.1f} ×{p.quantity}"
            f"{'張' if p.lot_type == 'common' else '股'}"
        )
    lines.append("<i>整日追蹤這幾檔的出場邏輯，收盤結算損益。</i>")
    return "\n".join(lines)


def build_exit_message(closed: list[dict]) -> str:
    if not closed:
        return ""
    lines = ["📝 <b>紙上模擬出場</b>", "━━━━━━━━━━━━━━━━"]
    for t in closed:
        tag    = "🟢" if t["pnl"] >= 0 else "🔴"
        reason = _REASON_LABEL.get(t["reason"], t["reason"])
        lines.append(
            f"{tag} <b>{t['code']} {t['name']}</b>　{reason}\n"
            f"   買 {t['entry_price']:,.1f} → 賣 {t['exit_price']:,.1f}　"
            f"損益 {t['pnl']:+,.0f} 元"
        )
    return "\n".join(lines)


def build_paper_summary(day: date | None = None, ddir: str = _LEDGER_DIR) -> str:
    """收盤後紙上模擬損益彙總；當日無紙上交易回 ""。"""
    trades = load_paper_trades(day, ddir)
    if not trades:
        return ""
    total = sum(t.get("pnl", 0.0) for t in trades)
    wins  = sum(1 for t in trades if t.get("pnl", 0.0) > 0)
    n     = len(trades)
    win_rate = (wins / n * 100) if n else 0.0
    tag = "🟢" if total >= 0 else "🔴"

    lines = [
        "📝 <b>今日紙上模擬結算</b>",
        "━━━━━━━━━━━━━━━━",
        f"{tag} 合計損益：<b>{total:+,.0f} 元</b>",
        f"勝率 {wins}/{n}（{win_rate:.0f}%）",
        "",
    ]
    for t in trades:
        t_tag  = "🟢" if t.get("pnl", 0.0) >= 0 else "🔴"
        reason = _REASON_LABEL.get(t.get("reason", ""), t.get("reason", ""))
        lines.append(
            f"{t_tag} <b>{t['code']} {t['name']}</b>　"
            f"{t['entry_price']:,.1f} → {t['exit_price']:,.1f}　"
            f"{t['pnl']:+,.0f} 元（{reason}）"
        )
    lines.append("")
    lines.append("<i>純模擬，未實際下單。</i>")
    return "\n".join(lines)
