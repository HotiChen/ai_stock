"""
stock_query.py — Telegram 查股功能主模組

使用者在 Telegram 輸入股票代號，回傳完整股票分析報告。
所有外部 IO 均使用 lazy import + try/except，確保在 api=None 時也能正常降級。
"""
from __future__ import annotations

import re
import logging
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _fetch_stock_name
# ---------------------------------------------------------------------------

def _fetch_stock_name(code: str, api=None) -> str:
    """查股票名稱。
    優先用 Shioaji contract，失敗則用 yfinance，再失敗就回傳 code。
    """
    # 1. Shioaji
    if api is not None:
        try:
            contract = api.Contracts.Stocks.get(code)
            if contract and getattr(contract, "name", None):
                return contract.name
        except Exception as e:
            log.debug("Shioaji name lookup failed for %s: %s", code, e)

    # 2. yfinance fallback
    try:
        import yfinance as yf  # type: ignore
        ticker = yf.Ticker(f"{code}.TW")
        info = ticker.info
        name = info.get("longName") or info.get("shortName") or ""
        if name:
            return name
    except Exception as e:
        log.debug("yfinance name lookup failed for %s: %s", code, e)

    return code


# ---------------------------------------------------------------------------
# _fetch_annual_trend
# ---------------------------------------------------------------------------

def _fetch_annual_trend(code: str, api=None) -> dict:
    """用 yfinance 取近一年走勢。

    Returns:
        dict with keys:
            start_price, end_price, high_52w, low_52w,
            change_pct, monthly_closes, error
    """
    _empty = dict(
        start_price=None,
        end_price=None,
        high_52w=None,
        low_52w=None,
        change_pct=None,
        monthly_closes=[],
        error="",
    )
    try:
        import yfinance as yf  # type: ignore

        ticker = yf.Ticker(f"{code}.TW")
        df = ticker.history(period="1y")

        if df is None or df.empty:
            return {**_empty, "error": "yfinance 無資料"}

        closes = df["Close"].dropna()
        if len(closes) < 2:
            return {**_empty, "error": "資料筆數不足"}

        start_price = float(closes.iloc[0])
        end_price   = float(closes.iloc[-1])
        high_52w    = float(df["High"].max())
        low_52w     = float(df["Low"].min())
        change_pct  = (end_price - start_price) / start_price * 100 if start_price else 0.0

        # 取每月最後一個收盤價
        monthly = closes.resample("ME").last()
        monthly_closes = [round(float(v), 2) for v in monthly.values]

        return dict(
            start_price=round(start_price, 2),
            end_price=round(end_price, 2),
            high_52w=round(high_52w, 2),
            low_52w=round(low_52w, 2),
            change_pct=round(change_pct, 2),
            monthly_closes=monthly_closes,
            error=None,
        )
    except Exception as e:
        log.warning("_fetch_annual_trend failed for %s: %s", code, e)
        return {**_empty, "error": str(e)}


# ---------------------------------------------------------------------------
# _assess_day_trading
# ---------------------------------------------------------------------------

def _num(val, default: float):
    """取數值，把 None 當成「沒有這個值」而回傳 default。

    為什麼需要這個：dict.get(key, default) 的 default **只在 key 不存在時**
    生效。技術指標／籌碼／大盤資料算不出來時，key 往往是存在的、值為 None
    （例如資料筆數不足以算 RSI）。直接 .get 會取到 None，後續與門檻比較就
    炸成 TypeError（'>=' not supported between NoneType and float），整個
    build_daytrading_report 被上層 try/except 吞掉 → 當日零候選、預測不落庫。
    """
    return default if val is None else val


def _assess_day_trading(
    indicators: Optional[dict],
    chip: Optional[dict] = None,
    market: Optional[dict] = None,
) -> dict:
    """從技術指標、法人籌碼、大盤狀況評估當沖適合度。

    Args:
        indicators: 技術指標 dict（volume_ratio / RSI / ATR / KD / VWAP 等）。
                    傳入 None 時代表資料不可用，直接回傳 data_ok=False。
        chip:       法人籌碼 dict（foreign_net / investment_trust_net 等），可為 None。
        market:     大盤方向 dict（index_change_pct / futures_premium_pct），可為 None。

    Returns:
        dict with keys:
            verdict      — "✅ 適合當沖" / "🟡 尚可" / "❌ 不建議當沖" / "⚠️ 資料不足"
            score        — int 0–10
            data_ok      — bool；indicators=None 時為 False，否則為 True
            reasons_good — list[str]
            reasons_bad  — list[str]

    Note:
        年度走勢（annual）對盤中當沖評分無實際作用，已從簽名移除。
        若需年度趨勢分析，請改用 format_query_report / query_stock 路徑。
    """
    reasons_good: list[str] = []
    reasons_bad:  list[str] = []
    score = 5  # baseline

    if indicators is None:
        return dict(
            verdict="⚠️ 資料不足",
            score=0,
            data_ok=False,
            reasons_good=[],
            reasons_bad=["技術指標資料不可用"],
        )

    # 一律經 _num()：值可能存在但為 None（指標算不出來），不能直接比較
    volume_ratio = _num(indicators.get("volume_ratio"), 1.0)
    rsi          = _num(indicators.get("RSI"), 50.0)
    atr          = _num(indicators.get("ATR"), 0.0)
    current_price= _num(indicators.get("current_price"), 0.0)
    bullish      = bool(indicators.get("bullish_alignment") or False)
    bearish      = bool(indicators.get("bearish_alignment") or False)
    kd_k         = _num(indicators.get("KD_K"), 50.0)
    kd_d         = _num(indicators.get("KD_D"), 50.0)

    # ── 量比評估 ────────────────────────────────────────────────
    if volume_ratio >= 2.0:
        reasons_good.append(f"量比 {volume_ratio:.1f}x，爆量活躍，利於當沖")
        score += 2
    elif volume_ratio >= 1.3:
        reasons_good.append(f"量比 {volume_ratio:.1f}x，成交量放大")
        score += 1
    elif volume_ratio < 0.8:
        reasons_bad.append(f"量比僅 {volume_ratio:.1f}x，成交量萎縮，不利當沖")
        score -= 4  # 量比不足是當沖的硬傷，多扣一分確保落入不建議區間

    # ── RSI 評估 ─────────────────────────────────────────────────
    if rsi > 75:
        reasons_bad.append(f"RSI {rsi:.1f} 超買，短線回檔風險高")
        score -= 2
    elif rsi < 25:
        reasons_bad.append(f"RSI {rsi:.1f} 超賣，反彈空間有限但方向不確定")
        score -= 1
    elif 45 <= rsi <= 65:
        reasons_good.append(f"RSI {rsi:.1f} 健康，動能穩定")
        score += 1

    # ── 均線排列 ─────────────────────────────────────────────────
    if bullish:
        reasons_good.append("多頭排列，趨勢向上")
        score += 1
    elif bearish:
        reasons_bad.append("空頭排列，趨勢向下")
        score -= 1

    # ── ATR 波動性（當沖需要一定波動）──────────────────────────
    if current_price > 0 and atr > 0:
        atr_pct = atr / current_price * 100
        if atr_pct >= 2.0:
            reasons_good.append(f"ATR {atr_pct:.1f}%，波動充足，當沖空間大")
            score += 1
        elif atr_pct < 0.5:
            reasons_bad.append(f"ATR {atr_pct:.1f}%，波動過小，當沖獲利空間有限")
            score -= 1

    # ── KD 評估 ─────────────────────────────────────────────────
    if kd_k > kd_d and kd_k < 80:
        reasons_good.append(f"KD 黃金交叉（K={kd_k:.0f}），短線偏多")
        score += 1
    elif kd_k < kd_d and kd_k > 20:
        reasons_bad.append(f"KD 死亡交叉（K={kd_k:.0f}），短線偏空")
        score -= 1

    # ── VWAP 評估 ────────────────────────────────────────────────
    vwap = _num(indicators.get("VWAP"), 0.0)
    if vwap and vwap > 0 and current_price > 0:
        ratio = current_price / vwap
        if ratio > 1.005:
            reasons_good.append(f"站上 VWAP {vwap:,.1f}，籌碼偏多")
            score += 1
        elif ratio < 0.995:
            reasons_bad.append(f"跌破 VWAP {vwap:,.1f}，籌碼偏空")
            score -= 1

    # ── 法人籌碼評分 ─────────────────────────────────────────────
    if chip is not None:
        foreign_net  = _num(chip.get("foreign_net"), 0)
        trust_net    = _num(chip.get("investment_trust_net"), 0)
        dealer_net   = _num(chip.get("dealer_net"), 0)
        foreign_cont = _num(chip.get("foreign_continuous_buy"), 0)

        if foreign_net >= 1000:
            reasons_good.append(f"外資大買 {foreign_net:,.0f} 張，動能強勁")
            score += 2
        elif foreign_net >= 500:
            reasons_good.append(f"外資買超 {foreign_net:,.0f} 張")
            score += 1
        elif foreign_net <= -1000:
            reasons_bad.append(f"外資大賣 {-foreign_net:,.0f} 張，賣壓沉重")
            score -= 2
        elif foreign_net <= -500:
            reasons_bad.append(f"外資賣超 {-foreign_net:,.0f} 張")
            score -= 1

        if trust_net >= 300:
            reasons_good.append(f"投信買超 {trust_net:,.0f} 張")
            score += 1
        elif trust_net <= -300:
            reasons_bad.append(f"投信賣超 {-trust_net:,.0f} 張")
            score -= 1

        if foreign_cont >= 3:
            reasons_good.append(f"外資連續買超 {foreign_cont} 日")
            score += 1

        total_net = foreign_net + trust_net + dealer_net
        if total_net <= -2000:
            reasons_bad.append(f"三大法人合計賣超 {-total_net:,.0f} 張")
            score -= 1

    # ── 大盤方向加權 ─────────────────────────────────────────────
    if market is not None:
        index_pct = _num(market.get("index_change_pct"), 0.0)
        if index_pct <= -1.0:
            reasons_bad.append(f"大盤大跌 {index_pct:.2f}%，當沖風險高")
            score -= 2
        elif index_pct <= -0.3:
            reasons_bad.append(f"大盤走弱 {index_pct:.2f}%")
            score -= 1
        elif index_pct >= 1.0:
            reasons_good.append(f"大盤強勢 +{index_pct:.2f}%，多頭氛圍")
            score += 1

        # ── 台指期溢貼水 ─────────────────────────────────────────
        futures_pct = _num(market.get("futures_premium_pct"), 0.0)
        if futures_pct >= 0.3:
            reasons_good.append(f"台指期溢價 +{futures_pct:.2f}%，期市偏多")
            score += 1
        elif futures_pct <= -0.3:
            reasons_bad.append(f"台指期貼水 {futures_pct:.2f}%，期市偏空")
            score -= 1

    # ── 硬性否決條件 ─────────────────────────────────────────────
    # 量比嚴重不足時，直接否決（不論其他指標）
    if volume_ratio < 0.8:
        score = min(score, 3)

    # ── 決定 verdict ─────────────────────────────────────────────
    score = max(0, min(10, score))
    if score >= 6:
        verdict = "✅ 適合當沖"
    elif score >= 4:
        verdict = "🟡 尚可"
    else:
        verdict = "❌ 不建議當沖"

    return dict(
        verdict=verdict,
        score=score,
        data_ok=True,
        reasons_good=reasons_good,
        reasons_bad=reasons_bad,
    )


# ---------------------------------------------------------------------------
# format_query_report
# ---------------------------------------------------------------------------

def format_query_report(
    code: str,
    name: str,
    indicators: Optional[dict],
    annual: dict,
    news: list,
    analysis,   # DeepAnalysis | None
    dt_assessment: dict,
) -> str:
    """將所有資料格式化成 Telegram HTML 報告。"""
    lines: list[str] = []

    # ── 標題 ─────────────────────────────────────────────────────
    lines.append(f"📊 <b>{code} {name}</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # ── 年度走勢 ──────────────────────────────────────────────────
    if annual:
        start = annual.get("start_price")
        end   = annual.get("end_price")
        high  = annual.get("high_52w")
        low   = annual.get("low_52w")
        chg   = annual.get("change_pct")
        err   = annual.get("error")

        if err:
            lines.append(f"📅 年度走勢：<i>資料取得失敗（{err}）</i>")
        else:
            def _fmt(v):
                return f"{v:,.2f}" if v is not None else "N/A"
            chg_str = f"{chg:+.1f}%" if chg is not None else "N/A"
            lines.append(f"📅 <b>近一年走勢</b>")
            lines.append(f"  起始 {_fmt(start)} → 現在 {_fmt(end)}　{chg_str}")
            lines.append(f"  52週高：{_fmt(high)}　52週低：{_fmt(low)}")

    lines.append("")

    # ── 技術指標 ──────────────────────────────────────────────────
    lines.append("📈 <b>技術指標</b>")
    if indicators is None:
        lines.append("  <i>技術指標資料不可用</i>")
    else:
        price = indicators.get("current_price", "N/A")
        rsi   = indicators.get("RSI", "N/A")
        vr    = indicators.get("volume_ratio", "N/A")
        ma5   = indicators.get("MA5", "N/A")
        ma20  = indicators.get("MA20", "N/A")
        bb_pos= indicators.get("BB_position", "N/A")
        kd_k  = indicators.get("KD_K", "N/A")
        kd_d  = indicators.get("KD_D", "N/A")
        lines.append(f"  現價 {price}　RSI {rsi}　量比 {vr}x")
        lines.append(f"  MA5 {ma5}　MA20 {ma20}　BB位置 {bb_pos:.0%}" if isinstance(bb_pos, float) else f"  MA5 {ma5}　MA20 {ma20}")
        lines.append(f"  KD K={kd_k} D={kd_d}")

    lines.append("")

    # ── 當沖評估 ──────────────────────────────────────────────────
    lines.append("⚡ <b>當沖適合度</b>")
    verdict = dt_assessment.get("verdict", "—")
    score   = dt_assessment.get("score", "—")
    lines.append(f"  {verdict}（評分 {score}/10）")
    for r in dt_assessment.get("reasons_good", []):
        lines.append(f"  ✅ {r}")
    for r in dt_assessment.get("reasons_bad", []):
        lines.append(f"  ⚠️ {r}")

    lines.append("")

    # ── AI 分析 ───────────────────────────────────────────────────
    if analysis is not None:
        lines.append("🤖 <b>AI 深度分析</b>")
        signal_map = {"buy": "📈 買進", "sell": "📉 賣出", "hold": "⏸ 持觀望"}
        sig = signal_map.get(getattr(analysis, "signal", ""), getattr(analysis, "signal", "—"))
        conf = getattr(analysis, "confidence", "—")
        summary = getattr(analysis, "summary", "")
        target = getattr(analysis, "target_price", None)
        stop   = getattr(analysis, "stop_loss_price", None)
        hold   = getattr(analysis, "hold_days", None)
        lines.append(f"  訊號：{sig}　信心度：{conf}/10")
        if summary:
            lines.append(f"  {summary}")
        if target:
            lines.append(f"  目標價：{target}　停損：{stop}　持有天數：{hold}日")
        lines.append("")

    # ── 近期新聞 ──────────────────────────────────────────────────
    lines.append("📰 <b>近期新聞</b>")
    if not news:
        lines.append("  無近期新聞")
    else:
        for i, item in enumerate(news[:5], 1):
            lines.append(f"  {i}. {item}")

    lines.append("")
    lines.append(f"<i>資料來源：yfinance / Shioaji</i>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# resolve_stock_input  — 代號或名稱 → 代號
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(r"^\d{4,6}$")
_REQUEST_TIMEOUT = 10


def resolve_stock_input(text: str, api=None) -> tuple[str | None, str | None]:
    """把用戶輸入（代號或名稱）解析成股票代號。

    Returns:
        (code, None)         — 找到唯一結果
        (None, error_msg)    — 找不到或結果模糊
    """
    text = text.strip()
    if not text:
        return None, "❌ 請輸入股票代號或名稱"

    # 1. 純數字 → 直接視為代號
    if _CODE_RE.match(text):
        return text, None

    import config

    # 2. config.STOCK_NAMES 完全比對（離線優先）
    rev = {v: k for k, v in config.STOCK_NAMES.items()}
    if text in rev:
        return rev[text], None

    # 3. Shioaji contracts（完全比對 > 部分比對）
    if api is not None:
        exact: list[tuple[str, str]] = []
        partial: list[tuple[str, str]] = []
        for exchange in ("TSE", "OTC", "OES"):
            try:
                for c in getattr(api.Contracts.Stocks, exchange, []):
                    if c.name == text:
                        exact.append((c.code, c.name))
                    elif text in c.name:
                        partial.append((c.code, c.name))
            except Exception:
                pass
        if len(exact) == 1:
            return exact[0][0], None
        if not exact and len(partial) == 1:
            return partial[0][0], None
        if exact or partial:
            candidates = exact or partial
            hint = "　".join(f"{c} {n}" for c, n in candidates[:5])
            return None, f"🔍 找到多個結果，請輸入代號：\n{hint}"

    # 4. TWSE OpenAPI 線上搜尋（上市 + 上櫃）
    try:
        import requests as _req

        results: list[tuple[str, str]] = []
        found_exact = False
        for url in (
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
        ):
            try:
                resp = _req.get(url, timeout=_REQUEST_TIMEOUT, verify=True)
                if not resp.ok:
                    continue
                for item in resp.json():
                    name_key = "Name" if "Name" in item else "CompanyName"
                    code_key = "Code" if "Code" in item else "SecuritiesCode"
                    n = item.get(name_key, "")
                    c = item.get(code_key, "")
                    if n == text:
                        results.append((c, n))
                        found_exact = True
                    elif text in n:
                        results.append((c, n))
            except Exception as e:
                log.debug("TWSE/TPEX name lookup failed (%s): %s", url, e)
            if found_exact:
                break

        if len(results) == 1:
            return results[0][0], None
        if len(results) > 1:
            # 優先完全比對
            exact_r = [(c, n) for c, n in results if n == text]
            if len(exact_r) == 1:
                return exact_r[0][0], None
            hint = "　".join(f"{c} {n}" for c, n in (exact_r or results)[:5])
            return None, f"🔍 找到多個結果，請輸入代號：\n{hint}"
    except Exception as e:
        log.debug("resolve_stock_input TWSE error: %s", e)

    # 5. config.STOCK_NAMES 部分比對（最後 fallback）
    partial_cfg = [(c, n) for c, n in config.STOCK_NAMES.items() if text in n]
    if len(partial_cfg) == 1:
        return partial_cfg[0][0], None
    if len(partial_cfg) > 1:
        hint = "　".join(f"{c} {n}" for c, n in partial_cfg[:5])
        return None, f"🔍 找到多個結果，請輸入代號：\n{hint}"

    return None, f"❌ 找不到「{text}」\n請確認股票名稱或改用代號（例如：2330）"


# ---------------------------------------------------------------------------
# query_stock  (main entry point)
# ---------------------------------------------------------------------------


def query_stock(code: str, api=None) -> str:
    """查股主入口，回傳格式化 Telegram 文字報告。

    Args:
        code: 股票代號，應為 4-6 位數字
        api:  Shioaji API（可為 None，會自動降級）

    Returns:
        Telegram 格式化字串（永不 raise）
    """
    # 輸入驗證
    code = code.strip() if isinstance(code, str) else ""
    if not _CODE_RE.match(code):
        return f"❌ 無效的股票代號「{code}」\n請輸入 4-6 位數字，例如：2330"

    try:
        # 1. 股票名稱
        try:
            name = _fetch_stock_name(code, api=api)
        except Exception as e:
            log.warning("_fetch_stock_name error: %s", e)
            name = code

        # 2. 技術指標：Shioaji → yfinance fallback
        indicators = None
        if api is not None:
            try:
                from technical_indicators import fetch_indicators  # type: ignore
                indicators = fetch_indicators(api, code)
            except Exception as e:
                log.warning("fetch_indicators error for %s: %s", code, e)

        if indicators is None:
            # yfinance fallback（Shioaji 無資料或 api=None 時）
            try:
                import yfinance as yf  # type: ignore
                from technical_indicators import calculate_indicators  # type: ignore
                df = yf.Ticker(f"{code}.TW").history(period="6mo")
                if df is not None and not df.empty and len(df) >= 80:
                    indicators = calculate_indicators(df)
            except Exception as e:
                log.debug("yfinance indicators calc failed: %s", e)

        # 3. 年度走勢
        try:
            annual = _fetch_annual_trend(code, api=api)
        except Exception as e:
            log.warning("_fetch_annual_trend error: %s", e)
            annual = {"error": str(e), "monthly_closes": [], "change_pct": None,
                      "start_price": None, "end_price": None,
                      "high_52w": None, "low_52w": None}

        # 4. 新聞
        news: list[str] = []
        try:
            from stock_research import fetch_stock_news  # type: ignore
            news = fetch_stock_news(code, name, max_items=5)
        except Exception as e:
            log.debug("fetch_stock_news error: %s", e)

        # 5. AI 深度分析
        analysis = None
        try:
            from deep_analyzer import run_deep_analysis  # type: ignore
            analysis = run_deep_analysis(
                api=api,
                code=code,
                name=name,
                news=news,
                fundamentals_text="",
                market_summary="",
                theme_info="",
            )
        except Exception as e:
            log.debug("run_deep_analysis error: %s", e)

        # 6. 當沖評估
        try:
            dt_assessment = _assess_day_trading(indicators)
        except Exception as e:
            log.warning("_assess_day_trading error: %s", e)
            dt_assessment = {"verdict": "🟡 尚可", "score": 5,
                             "reasons_good": [], "reasons_bad": []}

        # 7. 格式化報告
        return format_query_report(
            code=code,
            name=name,
            indicators=indicators,
            annual=annual,
            news=news,
            analysis=analysis,
            dt_assessment=dt_assessment,
        )

    except Exception as e:
        log.error("query_stock unexpected error for %s: %s", code, e)
        return f"⚠️ 查詢 {code} 時發生錯誤，請稍後再試。"
