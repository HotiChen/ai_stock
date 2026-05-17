"""
daytrading_report.py — 今日當沖預測報告

從 daily_plans 撈今日候選股（08:30 已過 AI + 風控篩選），
計算技術指標當沖評分 + AI 信心勝率估算，回傳 Telegram HTML 格式報告。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

DB_PATH = "data/learning.db"

# confidence (0-10) → 勝率估算
# 公式：30% + confidence/10 * 50%  → 範圍 30%~80%
def _confidence_to_win_pct(confidence: int) -> float:
    return round(30.0 + (max(0, min(10, confidence)) / 10.0) * 50.0, 1)


def _fetch_historical_win_rate(db_path: str) -> Optional[float]:
    """從 learning_db 取系統近 30 天整體勝率，無資料回傳 None。"""
    try:
        from learning_db import LearningDB
        db = LearningDB(db_path)
        stats = db.win_rate_stats(end_date=date.today(), days=30)
        if stats["total"] >= 5:
            return round(stats["win_rate"] * 100, 1)
    except Exception as e:
        log.debug("_fetch_historical_win_rate failed: %s", e)
    return None


def _get_indicators(code: str, api=None) -> Optional[dict]:
    """嘗試取技術指標：Shioaji → yfinance → None。"""
    if api is not None:
        try:
            from technical_indicators import fetch_indicators
            return fetch_indicators(api, code)
        except Exception as e:
            log.debug("fetch_indicators(%s) failed: %s", code, e)

    try:
        import yfinance as yf
        from technical_indicators import calculate_indicators
        df = yf.Ticker(f"{code}.TW").history(period="3mo")
        if df is not None and not df.empty and len(df) >= 20:
            return calculate_indicators(df)
    except Exception as e:
        log.debug("yfinance indicators(%s) failed: %s", code, e)

    return None


def build_daytrading_report(api=None, db_path: str = DB_PATH) -> str:
    """建立今日當沖預測報告，回傳 Telegram HTML 字串。"""
    from research_db import load_daily_plan
    from stock_query import _assess_day_trading, _fetch_annual_trend

    # 1. 取今日候選股
    try:
        picks = load_daily_plan(date.today(), db_path)
    except Exception as e:
        log.warning("load_daily_plan failed: %s", e)
        picks = []

    if not picks:
        return (
            "⚡ <b>今日當沖預測</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "<i>今日尚無候選股。\n請等 08:30 盤前分析後再查詢。</i>"
        )

    # 2. 歷史系統勝率（用於背景參考）
    hist_win_rate = _fetch_historical_win_rate(db_path)

    # 3. 對每支股票計算當沖評分 + 勝率預測
    results = []
    for pick in picks:
        code       = pick.get("code", "")
        name       = pick.get("name", code)
        confidence = pick.get("confidence", 5)  # AI 信心分數 0-10

        if not code:
            continue

        indicators = _get_indicators(code, api=api)
        annual     = _fetch_annual_trend(code, api=api)
        assessment = _assess_day_trading(indicators, annual)
        dt_score   = assessment.get("score", 0)

        # 勝率預測：AI 信心 60% 權重 + 當沖評分 40% 權重
        ai_win_pct = _confidence_to_win_pct(confidence)
        dt_win_pct = round(30.0 + (dt_score / 10.0) * 50.0, 1)
        win_pct    = round(ai_win_pct * 0.6 + dt_win_pct * 0.4, 1)

        results.append({
            "code":         code,
            "name":         name,
            "confidence":   confidence,
            "dt_score":     dt_score,
            "verdict":      assessment.get("verdict", "—"),
            "win_pct":      win_pct,
            "reasons_good": assessment.get("reasons_good", []),
            "reasons_bad":  assessment.get("reasons_bad", []),
            "indicators":   indicators,
        })

    # 4. 排序：先按 win_pct，再按 dt_score
    results.sort(key=lambda x: (x["win_pct"], x["dt_score"]), reverse=True)
    qualified = [r for r in results if r["dt_score"] >= 4]

    if not qualified:
        return (
            "⚡ <b>今日當沖預測</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "<i>今日候選股當沖評分均偏低，建議觀望。</i>"
        )

    # 5. 組合報告
    lines = [
        "⚡ <b>今日當沖預測</b>",
        "━━━━━━━━━━━━━━━━",
    ]

    if hist_win_rate is not None:
        lines.append(f"📊 系統近 30 日實際勝率：<b>{hist_win_rate}%</b>")

    lines.append(f"<i>共 {len(qualified)} 支候選，依預測勝率排序</i>")
    lines.append("")

    for r in qualified[:8]:
        ind   = r["indicators"] or {}
        price = ind.get("current_price", "—")
        rsi   = ind.get("RSI", "—")
        vr    = ind.get("volume_ratio", "—")

        vr_str  = f"{vr:.1f}x"  if isinstance(vr,  float) else str(vr)
        rsi_str = f"{rsi:.1f}"  if isinstance(rsi, float) else str(rsi)
        price_str = f"{price:,.0f}" if isinstance(price, (int, float)) else str(price)

        lines.append(
            f"<b>{r['code']} {r['name']}</b>  "
            f"{r['verdict']}（當沖 {r['dt_score']}/10）"
        )
        lines.append(
            f"  🎯 預測勝率 <b>{r['win_pct']}%</b>"
            f"　AI 信心 {r['confidence']}/10"
        )
        if price != "—":
            lines.append(f"  現價 {price_str}　RSI {rsi_str}　量比 {vr_str}")
        for g in r["reasons_good"][:2]:
            lines.append(f"  ✅ {g}")
        for b in r["reasons_bad"][:1]:
            lines.append(f"  ⚠️ {b}")
        lines.append("")

    lines.append(
        f"<i>⚠️ 勝率為統計估算，非保證獲利。\n"
        f"資料時間：{date.today().strftime('%Y/%m/%d')} 盤前分析</i>"
    )

    return "\n".join(lines)
