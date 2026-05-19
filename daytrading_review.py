"""
daytrading_review.py — 收盤後當沖預測複盤

流程：
  1. 取今日 dt_prediction_log 中 action='long' 且未複盤的記錄
  2. 用 yfinance 抓當日 OHLC
  3. 判斷 outcome：hit_target / hit_stop / neutral
  4. 回寫 DB
  5. 回傳 Telegram HTML 摘要
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_DB = "data/daytrading_review.db"


def _fetch_ohlc(code: str) -> Optional[dict]:
    """抓今日 OHLC，失敗回 None。"""
    try:
        import yfinance as yf
        df = yf.Ticker(f"{code}.TW").history(period="2d")
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        return {
            "open":  float(row["Open"]),
            "high":  float(row["High"]),
            "low":   float(row["Low"]),
            "close": float(row["Close"]),
        }
    except Exception as e:
        log.debug("_fetch_ohlc(%s) failed: %s", code, e)
        return None


def _determine_outcome(
    target_price: Optional[float],
    stop_loss:    Optional[float],
    ohlc:         dict,
) -> tuple[str, Optional[int]]:
    """
    回傳 (outcome, was_correct)。
    保守原則：若當日同時觸及目標與停損，視為停損（先跌後漲無法確認）。
    """
    hit_target = target_price is not None and ohlc["high"] >= target_price
    hit_stop   = stop_loss    is not None and ohlc["low"]  <= stop_loss

    if hit_stop:
        return "hit_stop", 0
    if hit_target:
        return "hit_target", 1
    return "neutral", None


def run_daytrading_review(
    db_path: str = _DEFAULT_DB,
    today:   Optional[str] = None,
) -> str:
    """複盤今日當沖預測，回傳 Telegram HTML 摘要。"""
    from daytrading_db import DaytradingDB, DTReview

    if today is None:
        today = date.today().isoformat()

    db    = DaytradingDB(db_path)
    rows  = db.get_unreviewed(today)

    if not rows:
        return (
            "📋 <b>當沖預測複盤</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            f"<i>{today} 無待複盤記錄。</i>"
        )

    results = []
    for row in rows:
        code = row["code"]
        ohlc = _fetch_ohlc(code)
        if ohlc is None:
            log.warning("review: 無法取得 %s 收盤資料，跳過", code)
            continue

        outcome, was_correct = _determine_outcome(
            row["target_price"], row["stop_loss"], ohlc
        )
        review = DTReview(
            date=today, code=code,
            daily_open=ohlc["open"], daily_high=ohlc["high"],
            daily_low=ohlc["low"],   daily_close=ohlc["close"],
            outcome=outcome, was_correct=was_correct,
        )
        db.save_review(review)
        results.append({"row": row, "ohlc": ohlc,
                        "outcome": outcome, "was_correct": was_correct})

    # ── 組合摘要 ──────────────────────────────────────────────────
    wins    = sum(1 for r in results if r["was_correct"] == 1)
    losses  = sum(1 for r in results if r["was_correct"] == 0)
    neutral = sum(1 for r in results if r["was_correct"] is None)

    lines = [
        "📋 <b>當沖預測複盤</b>",
        "━━━━━━━━━━━━━━━━",
        f"日期：{today}　共複盤 {len(results)} 支",
        f"✅ 達標 {wins}　❌ 停損 {losses}　⬜ 未觸發 {neutral}",
        "",
    ]

    for r in results:
        row    = r["row"]
        ohlc   = r["ohlc"]
        icon   = {"hit_target": "✅", "hit_stop": "❌", "neutral": "⬜"}[r["outcome"]]
        label  = {"hit_target": "達目標", "hit_stop": "觸停損", "neutral": "未觸發"}[r["outcome"]]

        lines.append(
            f"{icon} <b>{row['code']} {row['name']}</b>（評分 {row['dt_score']}/10）"
        )
        lines.append(
            f"   目標 {row['target_price'] or '—'}　停損 {row['stop_loss'] or '—'}"
            f"　→ 最高 {ohlc['high']:.1f}　最低 {ohlc['low']:.1f}"
        )
        lines.append(f"   {label}　收盤 {ohlc['close']:.1f}")
        lines.append("")

    # 近 30 日累計勝率
    stats = db.win_rate_summary(days=30)
    if stats["total"] >= 3:
        wr = stats["win_rate"] * 100
        lines.append(
            f"<i>📊 近 30 日累計：{stats['total']} 筆，勝率 {wr:.1f}%</i>"
        )

    return "\n".join(lines)
