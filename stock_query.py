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

def _assess_day_trading(indicators: Optional[dict], annual: dict) -> dict:
    """從技術指標評估當沖適合度。

    Returns:
        dict with keys:
            verdict  — "✅ 適合當沖" / "🟡 尚可" / "❌ 不建議當沖"
            score    — int 0–10
            reasons_good — list[str]
            reasons_bad  — list[str]
    """
    reasons_good: list[str] = []
    reasons_bad:  list[str] = []
    score = 5  # baseline

    if indicators is None:
        return dict(
            verdict="🟡 尚可（無法取得技術指標）",
            score=5,
            reasons_good=[],
            reasons_bad=["技術指標資料不可用"],
        )

    volume_ratio = indicators.get("volume_ratio", 1.0)
    rsi          = indicators.get("RSI", 50.0)
    atr          = indicators.get("ATR", 0.0)
    current_price= indicators.get("current_price", 0.0)
    bullish      = indicators.get("bullish_alignment", False)
    bearish      = indicators.get("bearish_alignment", False)
    bb_pos       = indicators.get("BB_position", 0.5)
    kd_k         = indicators.get("KD_K", 50.0)
    kd_d         = indicators.get("KD_D", 50.0)

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
# query_stock  (main entry point)
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(r"^\d{4,6}$")


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

        # 2. 技術指標
        indicators = None
        if api is not None:
            try:
                from technical_indicators import fetch_indicators  # type: ignore
                indicators = fetch_indicators(api, code)
            except Exception as e:
                log.warning("fetch_indicators error for %s: %s", code, e)
        else:
            # 嘗試用 yfinance 計算技術指標
            try:
                import yfinance as yf  # type: ignore
                import numpy as np
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
            dt_assessment = _assess_day_trading(indicators, annual)
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
