"""
dt_paper_trade.py — 每日當沖模擬倉（Paper Trading）

從今天開始，每天 13:35 DaytradingReview 完成後自動執行：
  1. 取今日 dt_score 最高的 long 推薦股（#1 Pick）
  2. 以當日開盤價模擬進場
  3. 依照 TP/SL 出場邏輯計算損益
  4. 更新本金，記錄到 paper_trading.db
  5. 每 7 / 14 / 21 / 28 天自動輸出里程碑報告並推播 Telegram

初始本金：30,000 元（--capital 參數可修改）
手續費：單邊 0.1425%（買 + 賣，模擬券商實際費用）
交易稅：0.3%（賣方收，當沖減半 0.15%）
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DB_PATH       = "data/paper_trading.db"
_REVIEW_DB     = "data/daytrading_review.db"
_INIT_CAPITAL  = 30_000.0   # 初始本金（元）
_COMMISSION    = 0.001425   # 手續費（單邊）
_TAX_INTRADAY  = 0.0015     # 當沖交易稅（賣方，正常 0.003 減半）
_MIN_CAPITAL   = 1_000.0    # 低於此金額停止交易


# ══════════════════════════════════════════════════════════════════════════════
# 資料庫
# ══════════════════════════════════════════════════════════════════════════════

class PaperTradeDB:
    def __init__(self, path: str = _DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    date           TEXT    NOT NULL UNIQUE,
                    code           TEXT,
                    name           TEXT,
                    dt_score       INTEGER,
                    entry_price    REAL,
                    exit_price     REAL,
                    outcome        TEXT,
                    position_size  REAL,
                    pnl            REAL,
                    pnl_pct        REAL,
                    capital_before REAL,
                    capital_after  REAL,
                    note           TEXT    DEFAULT '',
                    created_at     TEXT    DEFAULT (datetime('now','localtime'))
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # 首次寫入初始本金
            c.execute("""
                INSERT OR IGNORE INTO config(key, value)
                VALUES('initial_capital', ?), ('start_date', ?)
            """, (str(_INIT_CAPITAL), date.today().isoformat()))

    # ── 讀寫 ─────────────────────────────────────────────────────────────────

    def get_capital(self) -> float:
        """取得目前本金（最後一筆的 capital_after，或初始值）。"""
        with self._conn() as c:
            row = c.execute(
                "SELECT capital_after FROM trades ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row:
                return row["capital_after"]
            val = c.execute(
                "SELECT value FROM config WHERE key='initial_capital'"
            ).fetchone()
            return float(val["value"]) if val else _INIT_CAPITAL

    def get_initial_capital(self) -> float:
        with self._conn() as c:
            val = c.execute(
                "SELECT value FROM config WHERE key='initial_capital'"
            ).fetchone()
            return float(val["value"]) if val else _INIT_CAPITAL

    def save_trade(self, trade: dict) -> None:
        with self._conn() as c:
            c.execute("""
                INSERT OR REPLACE INTO trades
                    (date, code, name, dt_score,
                     entry_price, exit_price, outcome,
                     position_size, pnl, pnl_pct,
                     capital_before, capital_after, note)
                VALUES
                    (:date, :code, :name, :dt_score,
                     :entry_price, :exit_price, :outcome,
                     :position_size, :pnl, :pnl_pct,
                     :capital_before, :capital_after, :note)
            """, trade)

    def get_trades(self, start_date: str = "2000-01-01") -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM trades WHERE date >= ? ORDER BY date ASC",
                (start_date,)
            ).fetchall()

    def get_trade(self, target_date: str) -> Optional[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM trades WHERE date = ?", (target_date,)
            ).fetchone()

    def count_trading_days(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    def get_start_date(self) -> str:
        with self._conn() as c:
            val = c.execute(
                "SELECT value FROM config WHERE key='start_date'"
            ).fetchone()
            return val["value"] if val else date.today().isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# 損益計算
# ══════════════════════════════════════════════════════════════════════════════

def _calc_pnl(
    capital: float,
    entry: float,
    exit_price: float,
) -> tuple[float, float]:
    """
    計算扣除手續費後的損益。
    回傳 (pnl, pnl_pct)
    """
    buy_fee  = capital * _COMMISSION
    sell_fee = capital * _COMMISSION
    tax      = capital * _TAX_INTRADAY
    gross    = capital * (exit_price - entry) / entry
    net_pnl  = gross - buy_fee - sell_fee - tax
    net_pct  = net_pnl / capital
    return round(net_pnl, 0), round(net_pct, 4)


def _get_top1_pick(today: str, review_db: str) -> Optional[dict]:
    """
    從 daytrading_review.db 取今日 dt_score 最高且已有 OHLC 的 long 推薦。
    """
    try:
        conn = sqlite3.connect(review_db, timeout=10)
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT *
            FROM   dt_prediction_log
            WHERE  date   = ?
              AND  action = 'long'
              AND  outcome IS NOT NULL
              AND  daily_open IS NOT NULL
            ORDER  BY dt_score DESC
            LIMIT  1
        """, (today,)).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("_get_top1_pick failed: %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 每日執行
# ══════════════════════════════════════════════════════════════════════════════

def run_daily_paper_trade(
    today: Optional[str] = None,
    db_path: str = _DB_PATH,
    review_db: str = _REVIEW_DB,
) -> Optional[dict]:
    """
    執行今日模擬交易，回傳交易記錄 dict，無法執行則回傳 None。
    """
    if today is None:
        today = date.today().isoformat()

    db = PaperTradeDB(db_path)

    # 避免重複執行
    if db.get_trade(today):
        log.info("paper trade for %s already recorded, skipping", today)
        return None

    capital = db.get_capital()

    # 本金耗盡
    if capital < _MIN_CAPITAL:
        trade = dict(
            date=today, code=None, name=None, dt_score=None,
            entry_price=None, exit_price=None, outcome="no_capital",
            position_size=0, pnl=0, pnl_pct=0,
            capital_before=capital, capital_after=capital,
            note="本金不足，停止交易",
        )
        db.save_trade(trade)
        return trade

    # 取今日 #1 推薦
    pick = _get_top1_pick(today, review_db)
    if not pick:
        trade = dict(
            date=today, code=None, name=None, dt_score=None,
            entry_price=None, exit_price=None, outcome="no_pick",
            position_size=0, pnl=0, pnl_pct=0,
            capital_before=capital, capital_after=capital,
            note="今日無有效當沖推薦",
        )
        db.save_trade(trade)
        return trade

    # 進場價：當日開盤
    entry = pick["daily_open"]
    outcome = pick["outcome"]   # hit_target / hit_stop / neutral / untestable

    # untestable：當日振幅過小，這筆預測無法被驗證（多半是報價來源異常）。
    # 不可當成 neutral 用收盤價結算——那會憑空生出一筆 ~0% 的假交易紀錄，
    # 污染模擬倉的績效曲線。比照 no_pick 的作法記錄並跳過。
    if outcome == "untestable":
        trade = dict(
            date=today, code=pick["code"], name=pick.get("name"),
            dt_score=pick.get("dt_score"),
            entry_price=None, exit_price=None, outcome="untestable",
            position_size=0, pnl=0, pnl_pct=0,
            capital_before=capital, capital_after=capital,
            note="當日振幅過小，預測無法驗證，略過",
        )
        db.save_trade(trade)
        return trade

    # 出場價
    if outcome == "hit_target" and pick.get("target_price"):
        exit_price = pick["target_price"]
    elif outcome == "hit_stop" and pick.get("stop_loss"):
        exit_price = pick["stop_loss"]
    else:
        exit_price = pick["daily_close"] or entry  # neutral → 收盤

    pnl, pnl_pct = _calc_pnl(capital, entry, exit_price)
    capital_after = round(capital + pnl, 0)

    trade = dict(
        date=today,
        code=pick["code"],
        name=pick["name"],
        dt_score=pick["dt_score"],
        entry_price=entry,
        exit_price=exit_price,
        outcome=outcome,
        position_size=capital,
        pnl=pnl,
        pnl_pct=pnl_pct,
        capital_before=capital,
        capital_after=capital_after,
        note=pick.get("ai_summary", "")[:100],
    )
    db.save_trade(trade)
    log.info(
        "paper trade %s: %s %s  outcome=%s  pnl=%+.0f  capital=%s→%s",
        today, pick["code"], pick["name"], outcome,
        pnl, f"{capital:,.0f}", f"{capital_after:,.0f}",
    )
    return trade


# ══════════════════════════════════════════════════════════════════════════════
# 報告生成
# ══════════════════════════════════════════════════════════════════════════════

_OUTCOME_ICON = {
    "hit_target": "✅",
    "hit_stop":   "❌",
    "neutral":    "⬜",
    "no_pick":    "—",
    "no_capital": "🚫",
}
_OUTCOME_ZH = {
    "hit_target": "達目標",
    "hit_stop":   "觸停損",
    "neutral":    "收盤出場",
    "no_pick":    "無推薦",
    "no_capital": "資金不足",
}


def generate_report(
    milestone_days: int = 0,
    db_path: str = _DB_PATH,
    send_telegram: bool = False,
) -> str:
    """
    生成報告。milestone_days=0 代表「今日報告」。
    milestone_days=7/14/21/28 代表里程碑報告。
    """
    db     = PaperTradeDB(db_path)
    trades = db.get_trades()

    if not trades:
        return "📊 尚無交易記錄，請等待今日收盤後執行。"

    init_capital = db.get_initial_capital()
    start_date   = db.get_start_date()

    # 篩選特定天數的交易
    if milestone_days > 0:
        cutoff = (
            date.fromisoformat(start_date) + timedelta(days=milestone_days)
        ).isoformat()
        window = [t for t in trades if t["date"] <= cutoff]
        period_label = f"第 {milestone_days} 天里程碑"
    else:
        window = list(trades)
        period_label = "最新狀態"

    if not window:
        return f"📊 {period_label}：尚無資料。"

    # 統計
    real_trades = [t for t in window if t["outcome"] not in ("no_pick", "no_capital", None)]
    hits        = sum(1 for t in real_trades if t["outcome"] == "hit_target")
    stops       = sum(1 for t in real_trades if t["outcome"] == "hit_stop")
    neutrals    = sum(1 for t in real_trades if t["outcome"] == "neutral")
    no_trades   = sum(1 for t in window if t["outcome"] in ("no_pick", "no_capital"))

    win_rate    = hits / len(real_trades) * 100 if real_trades else 0
    total_days  = len(window)

    # 最後一筆本金
    last_capital = window[-1]["capital_after"]
    total_pnl    = last_capital - init_capital
    total_pnl_pct = total_pnl / init_capital * 100

    # 最大回撤
    peak = init_capital
    max_dd = 0.0
    running = init_capital
    for t in window:
        running = t["capital_after"]
        if running > peak:
            peak = running
        dd = (peak - running) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # 連勝/連敗
    streak_win = streak_loss = cur_streak = 0
    last_outcome = None
    for t in real_trades:
        o = t["outcome"]
        if o == last_outcome:
            cur_streak += 1
        else:
            cur_streak = 1
        if o == "hit_target":
            streak_win = max(streak_win, cur_streak)
        elif o == "hit_stop":
            streak_loss = max(streak_loss, cur_streak)
        last_outcome = o

    # 平均損益
    avg_win  = (
        sum(t["pnl"] for t in real_trades if t["outcome"] == "hit_target") / hits
        if hits else 0
    )
    avg_loss = (
        sum(t["pnl"] for t in real_trades if t["outcome"] == "hit_stop") / stops
        if stops else 0
    )
    expectancy = (win_rate/100 * avg_win + (1-win_rate/100) * avg_loss) if real_trades else 0

    pnl_sign = "+" if total_pnl >= 0 else ""
    cap_emoji = "📈" if total_pnl >= 0 else "📉"

    lines = [
        f"{'='*44}",
        f"  模擬倉報告 ── {period_label}",
        f"  起始：{start_date}  /  統計 {total_days} 個交易日",
        f"{'='*44}",
        f"",
        f"  {cap_emoji} 本金變化",
        f"  初始：  {init_capital:>10,.0f} 元",
        f"  現在：  {last_capital:>10,.0f} 元",
        f"  損益：  {pnl_sign}{total_pnl:>9,.0f} 元  ({pnl_sign}{total_pnl_pct:.1f}%)",
        f"  最大回撤：{max_dd:.1f}%",
        f"",
        f"  📊 交易統計（共 {len(real_trades)} 筆有效交易）",
        f"  ✅ 達目標：{hits:>3} 筆",
        f"  ❌ 觸停損：{stops:>3} 筆",
        f"  ⬜ 收盤出場：{neutrals:>2} 筆",
        f"  — 無推薦日：{no_trades:>2} 天",
        f"",
        f"  🎯 勝率：{win_rate:.1f}%",
        f"  💰 平均獲利：+{avg_win:,.0f} 元",
        f"  💸 平均停損：{avg_loss:,.0f} 元",
        f"  🔢 期望值：{expectancy:+,.0f} 元／筆",
        f"  🏆 最長連勝：{streak_win} 天",
        f"  💀 最長連敗：{streak_loss} 天",
        f"",
        f"{'─'*44}",
        f"  最近 10 筆記錄",
        f"{'─'*44}",
    ]

    for t in window[-10:]:
        icon = _OUTCOME_ICON.get(t["outcome"], "?")
        zh   = _OUTCOME_ZH.get(t["outcome"], "")
        if t["code"]:
            pnl_str = f"{t['pnl']:+,.0f}"
            lines.append(
                f"  {t['date']}  {icon} {t['code']} {t['name']}"
                f"  {zh}  {pnl_str} 元"
            )
        else:
            lines.append(f"  {t['date']}  {icon} {zh}")

    lines += [
        f"{'='*44}",
    ]

    report = "\n".join(lines)

    # Telegram 推播
    if send_telegram:
        try:
            token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
            if token and chat_id:
                import requests
                tg_text = (
                    f"📊 <b>模擬倉 {period_label}</b>\n"
                    f"{'━'*22}\n"
                    f"{cap_emoji} 本金 {init_capital:,.0f} → <b>{last_capital:,.0f}</b> 元\n"
                    f"損益 <b>{pnl_sign}{total_pnl:,.0f} 元（{pnl_sign}{total_pnl_pct:.1f}%）</b>\n"
                    f"最大回撤 {max_dd:.1f}%\n\n"
                    f"🎯 勝率 <b>{win_rate:.1f}%</b>（{hits}達目標 / {stops}停損 / {neutrals}收盤出）\n"
                    f"期望值 {expectancy:+,.0f} 元／筆\n"
                    f"最長連勝 {streak_win} 天 · 最長連敗 {streak_loss} 天"
                )
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": tg_text, "parse_mode": "HTML"},
                    timeout=10,
                )
        except Exception as e:
            log.warning("Telegram send failed: %s", e)

    return report


def maybe_send_milestone(
    db_path: str = _DB_PATH,
    milestones: tuple = (7, 14, 21, 28),
) -> None:
    """
    若今天正好是第 7/14/21/28 個交易日，自動推送 Telegram 里程碑報告。
    """
    db    = PaperTradeDB(db_path)
    total = db.count_trading_days()
    if total in milestones:
        log.info("Milestone reached: day %d", total)
        report = generate_report(
            milestone_days=0,  # 用全部資料
            db_path=db_path,
            send_telegram=True,
        )
        print(f"\n🏁 里程碑！第 {total} 個交易日")
        print(report)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _today_summary(trade: dict) -> str:
    if trade is None:
        return "今日已記錄或資料不足"
    icon    = _OUTCOME_ICON.get(trade["outcome"], "?")
    zh      = _OUTCOME_ZH.get(trade["outcome"], "")
    pnl     = trade.get("pnl", 0) or 0
    cap     = trade.get("capital_after", 0) or 0
    code    = trade.get("code") or "—"
    name    = trade.get("name") or ""
    sign    = "+" if pnl >= 0 else ""

    if trade["outcome"] in ("no_pick", "no_capital"):
        return f"  今日：{zh}，本金維持 {cap:,.0f} 元"

    return (
        f"  今日：{icon} {code} {name}  {zh}\n"
        f"  損益：{sign}{pnl:,.0f} 元\n"
        f"  本金：{trade['capital_before']:,.0f} → {cap:,.0f} 元"
    )


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="當沖模擬倉")
    parser.add_argument("--run",   action="store_true", help="執行今日模擬交易")
    parser.add_argument("--date",  default=None,         help="指定日期（YYYY-MM-DD）")
    parser.add_argument("--report",type=int, default=0,  help="輸出報告（0=全部, 7/14/21/28=里程碑）")
    parser.add_argument("--capital", type=float, default=None, help="重設初始本金（元）")
    parser.add_argument("--reset", action="store_true",  help="清除所有記錄重新開始")
    args = parser.parse_args()

    if args.reset:
        confirm = input("⚠ 確定要清除所有模擬倉記錄？輸入 YES 確認：")
        if confirm.strip() == "YES":
            Path(_DB_PATH).unlink(missing_ok=True)
            print("✅ 已清除，下次執行將重新開始。")
        else:
            print("已取消。")
        exit()

    db = PaperTradeDB(_DB_PATH)

    if args.capital is not None:
        with db._conn() as c:
            c.execute("UPDATE config SET value=? WHERE key='initial_capital'",
                      (str(args.capital),))
        print(f"✅ 初始本金已設定為 {args.capital:,.0f} 元")

    if args.run:
        today = args.date or date.today().isoformat()
        print(f"\n🔄 執行 {today} 模擬交易...")
        trade = run_daily_paper_trade(today=today)
        print(_today_summary(trade))
        maybe_send_milestone()
        print()

    if args.report is not None:
        print()
        print(generate_report(milestone_days=args.report))
