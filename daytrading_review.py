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

# 預測資料庫的位置由 daytrading_db 定義（表的擁有者）。
# 這裡曾經硬寫 data/daytrading_review.db，而寫入端走 DB_PATH，
# 兩邊指向不同檔案，複盤與學習因此永遠讀到 0 筆。
from daytrading_db import DEFAULT_DB_PATH as _PREDICTION_DB
_DEFAULT_DB = _PREDICTION_DB


#: 當日振幅低於此百分比時，視為「預測無法被驗證」而非「沒觸發」。
#: 台股正常交易日個股振幅極少低於 0.5%；低於此值幾乎必然是報價來源
#: 異常（券商未登入時股價退化）。見 _determine_outcome 的說明。
_MIN_TESTABLE_RANGE_PCT = 0.5


def _fetch_intraday_bars(code: str) -> Optional[list[dict]]:
    """抓今日分鐘 K 線，回傳 list of {open, high, low, close} 或 None。

    優先使用 yfinance 1m interval 取今日盤中資料。
    若失敗（非今日或資料不足），fallback 回日 K 邏輯。
    """
    try:
        import yfinance as yf
        df = yf.Ticker(f"{code}.TW").history(period="1d", interval="1m")
        if df is not None and not df.empty and len(df) >= 5:
            bars = [
                {
                    "open":  float(r["Open"]),
                    "high":  float(r["High"]),
                    "low":   float(r["Low"]),
                    "close": float(r["Close"]),
                }
                for _, r in df.iterrows()
            ]
            return bars
    except Exception as e:
        log.debug("_fetch_intraday_bars(%s) 1m failed: %s", code, e)

    # Fallback：日 K 線（period="2d"，取最後一根）
    try:
        import yfinance as yf
        df = yf.Ticker(f"{code}.TW").history(period="2d")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            return [{
                "open":  float(row["Open"]),
                "high":  float(row["High"]),
                "low":   float(row["Low"]),
                "close": float(row["Close"]),
            }]
    except Exception as e:
        log.debug("_fetch_intraday_bars(%s) daily fallback failed: %s", code, e)

    return None


def _ohlc_from_bars(bars: list[dict]) -> dict:
    """從 bar 清單彙總出當日 OHLC（供複盤摘要顯示用）。"""
    return {
        "open":  bars[0]["open"],
        "high":  max(b["high"]  for b in bars),
        "low":   min(b["low"]   for b in bars),
        "close": bars[-1]["close"],
    }


def _determine_outcome(
    target_price: Optional[float],
    stop_loss:    Optional[float],
    bars: list[dict],
) -> tuple[str, Optional[int]]:
    """逐 bar 掃描，按時間順序找第一個觸及 target 或 stop 的 bar。

    規則
    ----
    - 先碰到 high >= target_price → hit_target，was_correct = 1
    - 先碰到 low  <= stop_loss   → hit_stop，  was_correct = 0
    - 同一根 bar 同時觸及         → 保守視為 hit_stop（無法確認先後）
    - 都沒碰到                    → neutral，  was_correct = None

    若僅有一根日 K（fallback），保留原始保守邏輯（stop 優先）。

    資料品質守衛
    ------------
    當日振幅小於 ``_MIN_TESTABLE_RANGE_PCT`` 時直接回傳 ``untestable``。
    這不是「沒觸發」，是「這筆預測根本無法被驗證」——2026-05-29 那批 29 筆
    記錄產生於券商登入失敗期間，股價退化到當日振幅平均只有 0.03%，而目標
    固定在 ±3%，數學上永遠碰不到。若記成 neutral，它們會混進反事實分析的
    分母，用假的 ~0% 報酬稀釋統計。

    已知取捨：漲停鎖死的個股當日振幅也可能接近 0，會一併被標為 untestable。
    這類個股實際上也無法當沖進出，排除在勝率統計外是可接受的。
    """
    if bars:
        highs = [b["high"] for b in bars if b.get("high") is not None]
        lows  = [b["low"]  for b in bars if b.get("low")  is not None]
        if highs and lows:
            day_high, day_low = max(highs), min(lows)
            ref = bars[0].get("open") or day_high
            if ref and (day_high - day_low) / ref * 100 < _MIN_TESTABLE_RANGE_PCT:
                return "untestable", None

    for bar in bars:
        hit_target = target_price is not None and bar["high"] >= target_price
        hit_stop   = stop_loss    is not None and bar["low"]  <= stop_loss

        if hit_stop and hit_target:
            return "hit_stop", 0    # 保守：同一根 bar 無法確認先後
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
        bars = _fetch_intraday_bars(code)
        if bars is None:
            log.warning("review: 無法取得 %s 分鐘 K 線資料，跳過", code)
            continue

        ohlc = _ohlc_from_bars(bars)
        outcome, was_correct = _determine_outcome(
            row["target_price"], row["stop_loss"], bars
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
        # 用 .get 而非 [] ——新增 outcome 值時不該讓整份複盤摘要炸掉
        icon   = {"hit_target": "✅", "hit_stop": "❌",
                  "neutral": "⬜", "untestable": "⚠️"}.get(r["outcome"], "❔")
        label  = {"hit_target": "達目標", "hit_stop": "觸停損",
                  "neutral": "未觸發", "untestable": "資料不足，無法驗證"}.get(
                      r["outcome"], r["outcome"])

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
