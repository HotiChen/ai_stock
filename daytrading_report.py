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
from datetime import date, datetime
from typing import Optional

import dt_rules
from daytrading_analyzer import DayTradingAnalysis, _calc_prices_from_atr, run_daytrading_analysis

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
    """取技術指標：Shioaji 即時 kbars → Shioaji 歷史日線 → None。

    原本第二段是 yfinance，已移除。兩段都走 Shioaji：第一段用
    fetch_indicators（即時合約路徑），失敗再用歷史日線重算，兩者資料來源
    一致，8:30 算出的指標才會和盤中看到的價格對得起來。
    """
    if api is None:
        import shioaji_session
        api = shioaji_session.get_api()
    if api is None:
        log.warning("_get_indicators(%s)：無 Shioaji 連線", code)
        return None

    try:
        from technical_indicators import fetch_indicators
        result = fetch_indicators(api, code)
        if result is not None:
            return result
    except Exception as e:
        log.debug("fetch_indicators(%s) failed: %s", code, e)

    try:
        from datetime import timedelta

        import shioaji_history as sh
        from technical_indicators import INDICATOR_HISTORY_DAYS, calculate_indicators
        end = date.today()
        # 走增量快取：8:30 每支候選都會進到這裡，每天重抓整段會燒光
        # Shioaji 的每日歷史額度。
        df = sh.fetch_daily_cached(api, code,
                                   end - timedelta(days=INDICATOR_HISTORY_DAYS),
                                   end, chunk_days=INDICATOR_HISTORY_DAYS)
        if df is not None and len(df) >= 80:
            return calculate_indicators(df)
    except Exception as e:
        log.debug("Shioaji 歷史指標(%s) failed: %s", code, e)

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


def _resolve_name(code: str, name: str, api=None) -> str:
    """若 name 與 code 相同（代表未成功取到名稱），用 Shioaji contract.name 補查。

    原本走 yfinance fast_info，已移除：它常回英文名或 "2330.TW" 這種無意義
    字串（原程式碼還得特別過濾），而 Shioaji 的 contract.name 本來就是中文股名。
    """
    if name and name != code:
        return name
    import shioaji_quotes

    if api is None:
        import shioaji_session
        api = shioaji_session.get_api(connect=False)
    return shioaji_quotes.stock_name(api, code) or name or code


def _get_stock_universe(api, top_n: int = 50) -> list[dict]:
    """取得全市場候選池（掃描所有股票），固定回傳 code+name 的列表。

    Real mode : Shioaji snapshots → 取量/漲跌排前 top_n
    Sim mode  : TWSE → Shioaji → 保底清單
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

    # 模擬模式：TWSE → Shioaji → 保底
    rows = fetch_twse_sim_candidates(ScanCriteria(
        min_volume=0, min_price=0.0, max_price=999999.0, top_n=top_n,
    ))
    result = []
    for r in rows:
        code = r["code"]
        name = _resolve_name(code, r.get("name", code))
        result.append({"code": code, "name": name, "confidence": 5})
    return result


def _advisor_analysis(
    code: str,
    name: str,
    indicators: Optional[dict],
    chip: Optional[dict],
    market: dict,
    dt_score: int,
    rule: "dt_rules.RuleDecision",
    capture: dict,
) -> DayTradingAnalysis:
    """llm_mode="advisor"：dt_rules 決定 action/entry，LLM 僅提供評論摘要
    （呼叫失敗完全不影響決策，只影響 summary 文字）。target/stop 沿用既有
    ATR 公式路徑（daytrading_analyzer._calc_prices_from_atr），把規則算出的
    進場區間餵給它，行為與 decider 模式的 ATR 計算方式一致。
    """
    entry_low, entry_high = dt_rules.rule_entry_range(indicators)
    if rule.action != "long":
        entry_low = entry_high = None

    dqs, missing = 5, []
    try:
        comment = run_daytrading_analysis(
            code=code, name=name, indicators=indicators, chip=chip,
            market=market, dt_score=dt_score, capture=capture,
        )
        comment_text = comment.summary or "（無評論）"
        dqs = comment.data_quality_score
        missing = comment.missing_data
        # parsed_action：LLM 評論本身的判斷（僅供落庫比較，不影響決策）。
        # 明確覆寫而非只靠 run_daytrading_analysis 內部填入，確保呼叫端把
        # run_daytrading_analysis 整個替換掉（測試常見作法）時仍可正確取值。
        capture["parsed_action"] = comment.action
    except Exception as e:
        log.debug("advisor 模式 AI 評論失敗 %s: %s", code, e)
        comment_text = "AI 評論不可用"
    summary = f"(顧問評論) {comment_text}"

    target_price = stop_loss = None
    if rule.action == "long" and entry_high is not None:
        atr = None
        if indicators:
            raw_atr = indicators.get("ATR")
            try:
                atr = float(raw_atr) if raw_atr is not None else None
            except (TypeError, ValueError):
                atr = None
        target_price, stop_loss = _calc_prices_from_atr(entry_high, atr)

        # ATR 不存在時，固定百分比保底（與 parse_daytrading_response 的保底邏輯一致）
        if stop_loss is None and entry_low is not None:
            stop_loss = round(entry_low * 0.97, 2)
        if target_price is None:
            target_price = round(entry_high * 1.03, 2)
        # 確保停損不落在進場區間內
        if stop_loss is not None and entry_low is not None and stop_loss >= entry_low:
            stop_loss = round(entry_low * 0.97, 2)

    return DayTradingAnalysis(
        code=code, name=name,
        action=rule.action, confidence=dt_score,
        entry_low=entry_low, entry_high=entry_high,
        target_price=target_price, stop_loss=stop_loss,
        timing="開盤" if rule.action == "long" else "觀望",
        summary=summary,
        data_quality_score=dqs,
        missing_data=missing,
    )


def _open_review_db(db_cls, review_db_path: str | None):
    """開啟「當沖複盤資料庫」(data/daytrading_review.db)。

    為什麼要獨立這個 helper：預測（dt_prediction_log）與 AI 決策
    （ai_decision_log）屬於**複盤資料庫**，和本模組參數 db_path 指的
    research / learning 資料庫是兩個不同的檔案，絕不可混用。

    歷史 bug：這兩處曾寫成 DaytradingDB(db_path)，於是預測全部落進
    research.db / learning.db；而三個讀取端（daytrading_review、
    dt_paper_trade、adaptive_scorer）都讀 data/daytrading_review.db
    → 複盤永遠「無待複盤記錄」、每日 #1 Pick 永遠 no_pick、
    adaptive_scorer 學不到東西，且 log 還會顯示 "saved N predictions"
    讓人以為有存到。

    review_db_path=None 時使用 DaytradingDB 自己的預設路徑（正式環境）；
    測試可傳入 tmp 路徑以免污染正式複盤庫。
    """
    return db_cls(review_db_path) if review_db_path else db_cls()


def build_daytrading_report(
    api=None,
    db_path: str = DB_PATH,
    analysis_count: int | None = None,
    display_count: int | None = None,
    review_db_path: str | None = None,
) -> str:
    """建立今日當沖預測報告，回傳 Telegram HTML 字串。

    掃描全市場所有股票，用技術指標評分排序，對前 ``analysis_count`` 名執行 AI
    當沖深度分析（產生進場區間/目標/停損、並存成 watching 供 9:05 再確認），
    訊息則顯示前 ``display_count`` 名。兩個數字未指定時從 DaytradingConfig 載入
    （預設分析 8 / 顯示 20，可用 DT_ANALYSIS_COUNT / DT_DISPLAY_COUNT 覆蓋）。
    不依賴 research.db，獨立運作。
    """
    # llm_mode 一律要讀（決定 8:30 AI 決策是否降級為顧問），analysis_count /
    # display_count 未指定時一併從同一份設定取得。
    from daytrading_config import load_daytrading_config
    _cfg = load_daytrading_config()
    if analysis_count is None:
        analysis_count = _cfg.analysis_count
    if display_count is None:
        display_count = _cfg.display_count
    llm_mode = _cfg.llm_mode

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
    #    llm_mode="decider"（預設）：LLM 直接決定 action/entry，行為與現狀完全相同。
    #    llm_mode="advisor"        ：dt_rules 決定 action/entry，LLM 只提供評論
    #                                （失敗不影響決策），target/stop 仍走既有 ATR 公式。
    #    兩種 mode 都會順便算出規則決策（rule_decide_action），落庫供反事實分析
    #    比較「規則 vs LLM」（task 4：ai_decision_log）。
    ai_map: dict = {}
    for r in qualified[:analysis_count]:
        capture: dict = {}
        rule = dt_rules.rule_decide_action(r["dt_score"], r["indicators"], market)

        if llm_mode == "advisor":
            ai_map[r["code"]] = _advisor_analysis(
                code=r["code"], name=r["name"],
                indicators=r["indicators"], chip=r["chip"], market=market,
                dt_score=r["dt_score"], rule=rule, capture=capture,
            )
        else:
            try:
                ai_map[r["code"]] = run_daytrading_analysis(
                    code=r["code"], name=r["name"],
                    indicators=r["indicators"],
                    chip=r["chip"],
                    market=market,
                    dt_score=r["dt_score"],
                    capture=capture,
                )
                # decider 模式：parsed_action 就是最終決策本身。明確設定（而非只
                # 靠 run_daytrading_analysis 內部填入 capture），確保測試把
                # run_daytrading_analysis 整個 mock 掉時仍能正確落庫。
                capture["parsed_action"] = ai_map[r["code"]].action
            except Exception as e:
                log.debug("run_daytrading_analysis(%s) failed: %s", r["code"], e)

        # AI 決策全落庫（task 4）：不論 llm_mode、成功與否都要記錄，失敗不得
        # 影響報表產生。
        try:
            from daytrading_db import DaytradingDB
            ai = ai_map.get(r["code"])
            now = datetime.now()
            _open_review_db(DaytradingDB, review_db_path).log_ai_decision(
                date=date.today().isoformat(),
                time=now.strftime("%H:%M"),
                code=r["code"],
                stage="premarket",
                llm_mode=llm_mode,
                dt_score=r["dt_score"],
                prompt=capture.get("prompt"),
                raw_response=capture.get("raw"),
                parsed_action=capture.get("parsed_action"),
                rule_action=rule.action,
                final_action=ai.action if ai else None,
                features={
                    "indicators": r["indicators"],
                    "chip": r["chip"],
                    "market": market,
                },
            )
        except Exception as e:
            log.debug("log_ai_decision(premarket, %s) failed: %s", r["code"], e)

    # 8b. 儲存盤中監控倉位
    #     存所有 AI 分析過的 qualified 標的（含 AI 判斷 skip 的），全部設為 watching，
    #     讓 9:05 開盤再確認（run_opening_reconfirm）能對每一支結合開盤實況重新判斷。
    #     skip 標的 entry/target/stop 為 None，監控（check_position_alerts）與
    #     9:05 reconfirm（build_opening_reconfirm_prompt）均已 None-safe。
    try:
        from daytrading_monitor import DaytradingPosition, replace_today
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
            replace_today(dt_positions)
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
            n = _open_review_db(DaytradingDB, review_db_path).save_predictions(predictions)
            log.info("daytrading_db: saved %d predictions → %s",
                     n, review_db_path or "data/daytrading_review.db")
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
