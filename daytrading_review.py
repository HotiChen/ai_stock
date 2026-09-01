"""
daytrading_review.py — 收盤後當沖預測複盤

流程：
  1. 取今日 dt_prediction_log 中 action='long' 且未複盤的記錄
  2. 用 Shioaji 抓當日分鐘 K（聚合出 OHLC）
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


#: 當日振幅低於此百分比時，視為「預測無法被驗證」而非「沒觸發」。
#: 台股正常交易日個股振幅極少低於 0.5%；低於此值幾乎必然是報價來源
#: 異常（券商未登入時股價退化）。見 _determine_outcome 的說明。
_MIN_TESTABLE_RANGE_PCT = 0.5


def _fetch_intraday_bars(code: str, day=None, api=None) -> Optional[list[dict]]:
    """抓指定日期的分鐘 K，回傳 [{open, high, low, close}, ...] 或 None。

    資料來源為 Shioaji kbars。原本走 yfinance，已移除——yfinance 的 1 分鐘
    資料只保留最近 7 天，複盤回填的歷史預測（dt_backfill 產生的 60 個交易日）
    根本抓不到；而且沒有分鐘 K 就無法判斷「當日先觸停利還是先觸停損」，
    只能保守假設停損先觸發，會系統性低估績效。

    day=None 代表今日。api=None 時向 shioaji_session 取共用連線。
    """
    from datetime import date as _date

    import shioaji_history as sh

    if day is None:
        day = _date.today()
    if api is None:
        import shioaji_session
        api = shioaji_session.get_api()
    if api is None:
        log.warning("複盤：無 Shioaji 連線，無法取得 %s 的分鐘 K", code)
        return None

    bars = sh.fetch_minute_bars(api, code, day)
    if not bars:
        log.debug("_fetch_intraday_bars(%s, %s)：無分鐘 K", code, day)
        return None
    return bars


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


_MISSED_GAIN_PCT = 3.0   # 觀望股當日最高漲幅逾此值，視為「可能錯過」


def _day_gain_pct(ohlc: dict) -> float:
    """當日開盤到最高的漲幅（%），代表當沖可及的最大機會。"""
    o = ohlc.get("open") or 0.0
    if o <= 0:
        return 0.0
    return (ohlc["high"] - o) / o * 100.0


def run_daytrading_review(
    db_path: str = _DEFAULT_DB,
    today:   Optional[str] = None,
    include_skipped: bool = True,
) -> str:
    """複盤今日當沖預測，回傳 Telegram HTML 摘要。

    include_skipped=True（預設）：連 AI 判 skip 的候選也一併補 OHLC。
    long 照舊算 outcome/was_correct（準確率）；skip 沒有目標價可判對錯，
    was_correct 維持 None（不污染 long 勝率），但補齊的 OHLC 讓我們能看出
    「AI 說不要、結果它大漲」的機會成本，也供 dt_counterfactual 做反事實比較。
    """
    from daytrading_db import DaytradingDB, DTReview

    if today is None:
        today = date.today().isoformat()

    db    = DaytradingDB(db_path)
    rows  = db.get_unreviewed(today, include_skipped=include_skipped)

    # 複盤日期可能不是今天（回填的歷史預測、或補跑前幾天的複盤），
    # 所以要把日期一併傳給分鐘 K 抓取，不能讓它預設抓今日。
    try:
        review_day = date.fromisoformat(today)
    except ValueError:
        review_day = None

    if not rows:
        return (
            "📋 <b>當沖預測複盤</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            f"<i>{today} 無待複盤記錄。</i>"
        )

    results = []
    for row in rows:
        code = row["code"]
        bars = _fetch_intraday_bars(code, day=review_day)
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
    # long（實際建議做多）才計入準確率；skip 只補 OHLC 供機會成本檢視。
    longs = [r for r in results if r["row"]["action"] == "long"]
    skips = [r for r in results if r["row"]["action"] != "long"]

    wins    = sum(1 for r in longs if r["was_correct"] == 1)
    losses  = sum(1 for r in longs if r["was_correct"] == 0)
    neutral = sum(1 for r in longs if r["was_correct"] is None)

    lines = [
        "📋 <b>當沖預測複盤</b>",
        "━━━━━━━━━━━━━━━━",
        f"日期：{today}　做多預測 {len(longs)} 支"
        + (f"　觀望 {len(skips)} 支" if skips else ""),
        f"✅ 達標 {wins}　❌ 停損 {losses}　⬜ 未觸發 {neutral}",
        "",
    ]

    for r in longs:
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

    # ── 觀望檢視：AI 說不要，結果它漲了嗎？（機會成本 / 過濾是否過嚴）──
    if skips:
        missed = [r for r in skips if _day_gain_pct(r["ohlc"]) >= _MISSED_GAIN_PCT]
        lines.append(f"👁 <b>觀望檢視</b>（AI 判斷不做的 {len(skips)} 支）")
        if missed:
            lines.append(f"   其中 {len(missed)} 支當日最高漲逾 {_MISSED_GAIN_PCT:.0f}%：")
            for r in sorted(missed, key=lambda x: -_day_gain_pct(x["ohlc"]))[:5]:
                row, ohlc = r["row"], r["ohlc"]
                lines.append(
                    f"   · <b>{row['code']} {row['name']}</b>"
                    f"　開 {ohlc['open']:.1f} → 高 {ohlc['high']:.1f}"
                    f"（+{_day_gain_pct(ohlc):.1f}%）"
                )
            lines.append("   <i>機會成本參考：連續多日錯失代表過濾可能過嚴</i>")
        else:
            lines.append("   <i>無明顯錯失，過濾判斷合理</i>")
        lines.append("")

    # 近 30 日累計勝率
    stats = db.win_rate_summary(days=30)
    if stats["total"] >= 3:
        wr = stats["win_rate"] * 100
        lines.append(
            f"<i>📊 近 30 日累計：{stats['total']} 筆，勝率 {wr:.1f}%</i>"
        )

    return "\n".join(lines)
