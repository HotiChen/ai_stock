"""
daytrading_report.py — 今日當沖預測報告

掃描全市場股票，計算技術指標當沖評分（0-10），回傳 Telegram HTML 格式報告。
報表只呈現：
  - 技術評分（dt_score / 10）：來自 _assess_day_trading() 的 heuristic 評分
  - 系統近 30 日實際勝率（hist_win_rate）：來自 learning_db 的歷史回測統計
兩者語義不同，不得混稱。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from daytrading_analyzer import run_daytrading_analysis

log = logging.getLogger(__name__)

DB_PATH = "data/learning.db"


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
    """嘗試取技術指標：Shioaji → yfinance fallback → None。"""
    if api is not None:
        try:
            from technical_indicators import fetch_indicators
            result = fetch_indicators(api, code)
            if result is not None:
                return result
        except Exception as e:
            log.debug("fetch_indicators(%s) failed: %s", code, e)

    try:
        import yfinance as yf
        from technical_indicators import calculate_indicators
        df = yf.Ticker(f"{code}.TW").history(period="6mo")
        if df is not None and not df.empty and len(df) >= 80:
            return calculate_indicators(df)
    except Exception as e:
        log.debug("yfinance indicators(%s) failed: %s", code, e)

    return None


_MAX_CHIP_PICKS = 10   # 查連續買超天數的上限，避免 TWSE rate limit
_MIN_DT_SCORE   = 4    # 進入 qualified 的最低技術評分門檻


def _fetch_chip_data(today_str: str) -> dict:
    """抓今日全市場三大法人資料，失敗回空 dict。"""
    try:
        from chip_data import fetch_institutional_investors
        return fetch_institutional_investors(today_str)
    except Exception as e:
        log.warning("fetch_institutional_investors failed: %s", e)
        return {}


def _fetch_market() -> dict:
    """抓大盤漲跌幅 + 台指期溢貼水，失敗各自回 0.0。"""
    result: dict = {"index_change_pct": 0.0, "futures_premium_pct": 0.0}
    try:
        from market_index import fetch_market_index_change
        pct = fetch_market_index_change()
        if pct == 0.0:
            log.warning("fetch_market_index_change returned 0.0，大盤方向加權暫停")
        result["index_change_pct"] = pct
    except Exception as e:
        log.warning("fetch_market_index_change failed: %s", e)
    try:
        from futures_premium import fetch_futures_premium
        fp = fetch_futures_premium()
        if fp is not None:
            result["futures_premium_pct"] = fp.premium_pct
    except Exception as e:
        log.debug("fetch_futures_premium failed: %s", e)
    return result


def _market_label(pct: float) -> str:
    if pct <= -1.0:
        return f"📉 {pct:.2f}%（大跌，慎！）"
    if pct <= -0.3:
        return f"📉 {pct:.2f}%（偏弱）"
    if pct >= 1.0:
        return f"📈 +{pct:.2f}%（強勢）"
    return f"📊 {pct:+.2f}%（平盤）"


def _resolve_name(code: str, name: str) -> str:
    """若 name 與 code 相同（代表未成功取到名稱），嘗試用 yfinance 補查。"""
    if name and name != code:
        return name
    try:
        import yfinance as yf
        info = yf.Ticker(f"{code}.TW").fast_info
        short = getattr(info, "shortName", None) or getattr(info, "longName", None)
        if short:
            # yfinance 有時回傳 "2330.TW" 格式，略過
            if short != f"{code}.TW" and not short.endswith(".TW"):
                return short
    except Exception:
        pass
    return name or code


def _get_stock_universe(api, top_n: int = 50) -> list[dict]:
    """取得全市場候選池（掃描所有股票），固定回傳 code+name 的列表。

    Real mode : Shioaji snapshots → 取量/漲跌排前 top_n
    Sim mode  : TWSE → yfinance → 保底清單
    """
    try:
        from market_scanner import (
            fetch_twse_sim_candidates, ScanCriteria,
            get_all_stock_codes, screen_candidates,
        )
        from market_scan import batch_fetch_snapshots
    except Exception as e:
        log.warning("_get_stock_universe import failed: %s", e)
        return []

    if api is not None:
        try:
            codes = get_all_stock_codes(api)
            snaps = batch_fetch_snapshots(api, codes)
            rows  = screen_candidates(snaps, ScanCriteria(
                min_volume=500, min_price=10.0, max_price=5000.0, top_n=top_n,
            ))
            return [{"code": r["code"], "name": r.get("name", r["code"]), "confidence": 5}
                    for r in rows]
        except Exception as e:
            log.warning("_get_stock_universe (shioaji) failed: %s", e)

    # 模擬模式：TWSE → yfinance → 保底
    rows = fetch_twse_sim_candidates(ScanCriteria(
        min_volume=0, min_price=0.0, max_price=999999.0, top_n=top_n,
    ))
    result = []
    for r in rows:
        code = r["code"]
        name = _resolve_name(code, r.get("name", code))
        result.append({"code": code, "name": name, "confidence": 5})
    return result


def build_daytrading_report(
    api=None,
    db_path: str = DB_PATH,
    analysis_count: int | None = None,
    display_count: int | None = None,
) -> str:
    """建立今日當沖預測報告，回傳 Telegram HTML 字串。

    掃描全市場所有股票，用技術指標評分排序，對前 ``analysis_count`` 名執行 AI
    當沖深度分析（產生進場區間/目標/停損、並存成 watching 供 9:05 再確認），
    訊息則顯示前 ``display_count`` 名。兩個數字未指定時從 DaytradingConfig 載入
    （預設分析 8 / 顯示 20，可用 DT_ANALYSIS_COUNT / DT_DISPLAY_COUNT 覆蓋）。
    不依賴 research.db，獨立運作。
    """
    if analysis_count is None or display_count is None:
        from daytrading_config import load_daytrading_config
        _cfg = load_daytrading_config()
        if analysis_count is None:
            analysis_count = _cfg.analysis_count
        if display_count is None:
            display_count = _cfg.display_count

    from stock_query import _assess_day_trading

    today_str = date.today().strftime("%Y%m%d")

    # 1. 取全市場候選池（不依賴 research.db）
    picks = _get_stock_universe(api, top_n=50)

    if not picks:
        return (
            "⚡ <b>今日當沖預測</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "<i>無法取得股票資料，請確認網路連線。</i>"
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

    # 6. 對每支股票計算當沖技術評分
    results = []
    for i, pick in enumerate(picks):
        code = pick.get("code", "")
        name = pick.get("name", code)
        if not code:
            continue

        indicators = _get_indicators(code, api=api)

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

        assessment = _assess_day_trading(indicators, chip=chip, market=market)
        dt_score   = assessment.get("score", 0)
        data_ok    = assessment.get("data_ok", True)

        results.append({
            "code":         code,
            "name":         name,
            "dt_score":     dt_score,
            "data_ok":      data_ok,
            "verdict":      assessment.get("verdict", "—"),
            "reasons_good": assessment.get("reasons_good", []),
            "reasons_bad":  assessment.get("reasons_bad", []),
            "indicators":   indicators,
            "chip":         chip,
        })

    # 7. 排序後依門檻篩選：必須有技術資料且評分 >= _MIN_DT_SCORE
    results.sort(key=lambda x: x["dt_score"], reverse=True)
    qualified = [r for r in results if r["data_ok"] and r["dt_score"] >= _MIN_DT_SCORE]

    if not qualified:
        header = (
            "⚡ <b>今日當沖預測</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            f"大盤：{_market_label(market['index_change_pct'])}\n"
        )
        has_any_data = any(r["data_ok"] for r in results)
        if not has_any_data:
            return header + "<i>⚠️ 技術資料不足，無法產生預測。\n請確認市場資料來源是否正常。</i>"
        return header + "<i>今日各股當沖條件不成熟，建議觀望。</i>"

    # 8. AI 當沖分析（前 analysis_count 名，均已通過資料門檻）
    ai_map: dict = {}
    for r in qualified[:analysis_count]:
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

    # 8b. 儲存盤中監控倉位
    #     存所有 AI 分析過的 qualified 標的（含 AI 判斷 skip 的），全部設為 watching，
    #     讓 9:05 開盤再確認（run_opening_reconfirm）能對每一支結合開盤實況重新判斷。
    #     skip 標的 entry/target/stop 為 None，監控（check_position_alerts）與
    #     9:05 reconfirm（build_opening_reconfirm_prompt）均已 None-safe。
    try:
        from daytrading_monitor import DaytradingPosition, save_daytrading_positions
        dt_positions = [
            DaytradingPosition(
                code=r["code"], name=r["name"],
                entry_low=ai.entry_low, entry_high=ai.entry_high,
                target_price=ai.target_price, stop_loss=ai.stop_loss,
                dt_score=r["dt_score"],
                ai_summary=ai.summary,
            )
            for r in qualified[:analysis_count]
            if (ai := ai_map.get(r["code"])) is not None
        ]
        if dt_positions:
            save_daytrading_positions(dt_positions)
    except Exception as e:
        log.warning("save_daytrading_positions failed: %s", e)

    # 8c. 存預測至複盤 DB（所有 qualified long，供收盤後驗收）
    try:
        from daytrading_db import DaytradingDB, DTPrediction
        predictions = []
        for r in qualified[:display_count]:
            ai = ai_map.get(r["code"])
            predictions.append(DTPrediction(
                date=date.today().isoformat(),
                code=r["code"], name=r["name"],
                dt_score=r["dt_score"],
                action=ai.action if ai else "skip",
                entry_low=ai.entry_low if ai else None,
                entry_high=ai.entry_high if ai else None,
                target_price=ai.target_price if ai else None,
                stop_loss=ai.stop_loss if ai else None,
                ai_summary=ai.summary if ai else "",
            ))
        if predictions:
            n = DaytradingDB(db_path).save_predictions(predictions)
            log.info("daytrading_db: saved %d predictions", n)
    except Exception as e:
        log.warning("save_predictions failed: %s", e)

    # 8d. 老薑回饋：儲存今日資料缺口，供明日 Gemini 晨報補充
    try:
        from super_trader import save_daily_feedback, make_feedback
        feedbacks = [
            make_feedback(
                code=r["code"], name=r["name"],
                score=ai_map[r["code"]].data_quality_score if r["code"] in ai_map else 5,
                missing=ai_map[r["code"]].missing_data if r["code"] in ai_map else [],
            )
            for r in qualified[:display_count]
        ]
        save_daily_feedback(feedbacks)
    except Exception as e:
        log.warning("super_trader.save_daily_feedback failed: %s", e)

    # 9. 組合報告
    lines = [
        "⚡ <b>今日當沖預測</b>",
        "━━━━━━━━━━━━━━━━",
        f"大盤：{_market_label(market['index_change_pct'])}",
    ]

    if hist_win_rate is not None:
        lines.append(f"📊 系統近 30 日實際勝率：<b>{hist_win_rate}%</b>")

    lines.append(f"<i>全市場掃描，技術評分前 {len(qualified)} 支</i>")
    lines.append("")

    for r in qualified[:display_count]:
        ind   = r["indicators"] or {}
        price = ind.get("current_price", "—")
        rsi   = ind.get("RSI", "—")
        vr    = ind.get("volume_ratio", "—")

        vr_str    = f"{vr:.1f}x"   if isinstance(vr,    float) else str(vr)
        rsi_str   = f"{rsi:.1f}"   if isinstance(rsi,   float) else str(rsi)
        price_str = f"{price:,.0f}" if isinstance(price, (int, float)) else str(price)

        lines.append(
            f"<b>{r['code']} {r['name']}</b>  "
            f"{r['verdict']}（技術評分 {r['dt_score']}/10）"
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

        # AI 當沖建議（前 analysis_count 名才有）
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
        f"<i>⚠️ 技術評分為啟發式指標，非模型校準機率，不代表實際獲利機率。\n"
        f"資料時間：{date.today().strftime('%Y/%m/%d')} 盤前分析</i>"
    )

    return "\n".join(lines)
