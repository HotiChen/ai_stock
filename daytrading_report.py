"""
daytrading_report.py — 今日當沖預測報告

從 daily_plans 撈今日候選股（08:30 已過 AI + 風控篩選），
計算技術指標當沖評分 + AI 信心勝率估算，回傳 Telegram HTML 格式報告。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from daytrading_analyzer import run_daytrading_analysis

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


_MAX_CHIP_PICKS = 10  # 查連續買超天數的上限，避免 TWSE rate limit


def _fetch_chip_data(today_str: str) -> dict:
    """抓今日全市場三大法人資料，失敗回空 dict。"""
    try:
        from chip_data import fetch_institutional_investors
        return fetch_institutional_investors(today_str)
    except Exception as e:
        log.warning("fetch_institutional_investors failed: %s", e)
        return {}


def _fetch_market() -> dict:
    """抓大盤漲跌幅，失敗回 0.0 並記 warning。"""
    try:
        from market_index import fetch_market_index_change
        pct = fetch_market_index_change()
        if pct == 0.0:
            log.warning("fetch_market_index_change returned 0.0，大盤方向加權暫停")
        return {"index_change_pct": pct}
    except Exception as e:
        log.warning("fetch_market_index_change failed: %s", e)
        return {"index_change_pct": 0.0}


def _market_label(pct: float) -> str:
    if pct <= -1.0:
        return f"📉 {pct:.2f}%（大跌，慎！）"
    if pct <= -0.3:
        return f"📉 {pct:.2f}%（偏弱）"
    if pct >= 1.0:
        return f"📈 +{pct:.2f}%（強勢）"
    return f"📊 {pct:+.2f}%（平盤）"


def build_daytrading_report(api=None, db_path: str = DB_PATH) -> str:
    """建立今日當沖預測報告，回傳 Telegram HTML 字串。"""
    from research_db import load_daily_plan
    from stock_query import _assess_day_trading, _fetch_annual_trend

    today_str = date.today().strftime("%Y%m%d")

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

    # 2. 大盤方向（一次）
    market = _fetch_market()

    # 3. 三大法人（今日全市場，一次）
    chip_today = _fetch_chip_data(today_str)

    # 4. 連續買超快取（各日期共用，避免重複打 TWSE API）
    _date_cache: dict = {today_str: chip_today}

    def _cached_fetcher(date_str: str) -> dict:
        if date_str not in _date_cache:
            try:
                from chip_data import fetch_institutional_investors
                _date_cache[date_str] = fetch_institutional_investors(date_str)
            except Exception:
                _date_cache[date_str] = {}
        return _date_cache[date_str]

    # 5. 歷史系統勝率
    hist_win_rate = _fetch_historical_win_rate(db_path)

    # 6. 對每支股票計算當沖評分 + 勝率預測
    results = []
    for i, pick in enumerate(picks):
        code       = pick.get("code", "")
        name       = pick.get("name", code)
        confidence = pick.get("confidence", 5)

        if not code:
            continue

        indicators = _get_indicators(code, api=api)
        annual     = _fetch_annual_trend(code, api=api)

        # 法人籌碼：今日單日資料 + 連續買超天數（上限 _MAX_CHIP_PICKS 支）
        chip = chip_today.get(code)
        if chip is not None and i < _MAX_CHIP_PICKS:
            try:
                from chip_data import get_continuous_buy_days
                cont = get_continuous_buy_days(
                    code,
                    end_date=today_str,
                    data_fetcher=_cached_fetcher,
                    days=5,
                )
                chip = {**chip, **cont}
            except Exception as e:
                log.debug("get_continuous_buy_days(%s) failed: %s", code, e)

        assessment = _assess_day_trading(indicators, annual, chip=chip, market=market)
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
            "chip":         chip,
        })

    # 7. 排序：先按 win_pct，再按 dt_score
    results.sort(key=lambda x: (x["win_pct"], x["dt_score"]), reverse=True)
    qualified = [r for r in results if r["dt_score"] >= 4]

    if not qualified:
        return (
            "⚡ <b>今日當沖預測</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "<i>今日候選股當沖評分均偏低，建議觀望。</i>"
        )

    # 8. AI 當沖分析（前 3 名）
    ai_map: dict = {}
    for r in qualified[:3]:
        try:
            ai_map[r["code"]] = run_daytrading_analysis(
                code=r["code"], name=r["name"],
                indicators=r["indicators"],
                chip=r["chip"],
                market=market,
                dt_score=r["dt_score"],
            )
        except Exception as e:
            log.debug("run_daytrading_analysis(%s) failed: %s", r["code"], e)

    # 9. 組合報告
    lines = [
        "⚡ <b>今日當沖預測</b>",
        "━━━━━━━━━━━━━━━━",
        f"大盤：{_market_label(market['index_change_pct'])}",
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

        vr_str    = f"{vr:.1f}x"   if isinstance(vr,    float) else str(vr)
        rsi_str   = f"{rsi:.1f}"   if isinstance(rsi,   float) else str(rsi)
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

        # 法人籌碼摘要（有資料才顯示）
        chip = r.get("chip")
        if chip:
            fn = chip.get("foreign_net", 0)
            fn_str = f"外資 {fn:+,.0f} 張"
            cont = chip.get("foreign_continuous_buy", 0)
            if cont >= 2:
                fn_str += f"（連買 {cont} 日）"
            lines.append(f"  🏦 {fn_str}")

        for g in r["reasons_good"][:2]:
            lines.append(f"  ✅ {g}")
        for b in r["reasons_bad"][:1]:
            lines.append(f"  ⚠️ {b}")

        # AI 當沖建議（前 3 名才有）
        ai = ai_map.get(r["code"])
        if ai and ai.action == "long":
            ai_parts = [f"  🤖 {ai.timing}進場"]
            if ai.entry_low is not None and ai.entry_high is not None:
                ai_parts.append(f"進場區間 {ai.entry_low:,.1f}–{ai.entry_high:,.1f}")
            if ai.target_price is not None:
                ai_parts.append(f"目標 {ai.target_price:,.1f}")
            if ai.stop_loss is not None:
                ai_parts.append(f"停損 {ai.stop_loss:,.1f}")
            lines.append("　".join(ai_parts))
            if ai.summary:
                lines.append(f"  <i>{ai.summary}</i>")

        lines.append("")

    lines.append(
        f"<i>⚠️ 勝率為統計估算，非保證獲利。\n"
        f"資料時間：{date.today().strftime('%Y/%m/%d')} 盤前分析</i>"
    )

    return "\n".join(lines)
