from __future__ import annotations

from dataclasses import dataclass

from stock_research import StockFundamentals


@dataclass
class Signal:
    label:       str
    color:       str   # "green" | "red" | "yellow" | "gray" | "blue"
    description: str


# ── Classifiers ───────────────────────────────────────────────────────────────

def classify_pe_ratio(pe: float | None) -> Signal:
    if pe is None:
        return Signal("P/E 無資料", "gray", "本益比資料不可取得")
    if pe < 0:
        return Signal("P/E 負值", "red", f"本益比 {pe:.1f}，公司目前虧損")
    if pe < 10:
        return Signal(f"P/E {pe:.1f} 低估", "green", "本益比低於 10，可能被市場低估")
    if pe < 20:
        return Signal(f"P/E {pe:.1f} 合理", "blue", "本益比在合理區間（10–20）")
    if pe < 30:
        return Signal(f"P/E {pe:.1f} 偏高", "yellow", "本益比偏高，需搭配成長性評估")
    return Signal(f"P/E {pe:.1f} 昂貴", "red", "本益比超過 30，估值偏貴，謹慎")


def classify_rsi(rsi: float | None) -> Signal:
    if rsi is None:
        return Signal("RSI 無資料", "gray", "RSI 指標無法取得")
    if rsi < 30:
        return Signal(f"RSI {rsi:.1f} 超賣", "green", "RSI 低於 30，技術面超賣，可能反彈")
    if rsi > 70:
        return Signal(f"RSI {rsi:.1f} 超買", "red", "RSI 高於 70，技術面超買，注意回檔風險")
    return Signal(f"RSI {rsi:.1f} 正常", "blue", "RSI 在正常區間（30–70）")


def classify_ma_trend(
    ma5: float | None,
    ma20: float | None,
    ma60: float | None,
) -> Signal:
    if any(v is None for v in (ma5, ma20, ma60)):
        if all(v is None for v in (ma5, ma20, ma60)):
            return Signal("均線 無資料", "gray", "均線資料無法取得")
        return Signal("均線 資料不完整", "gray", "部分均線資料缺失")

    if ma5 > ma20 > ma60:
        return Signal("均線 多頭排列", "green", "MA5 > MA20 > MA60，短中長線均向上，強勢格局")
    if ma5 < ma20 < ma60:
        return Signal("均線 空頭排列", "red", "MA5 < MA20 < MA60，短中長線均向下，弱勢格局")
    if ma5 > ma20:
        return Signal("均線 短線偏多", "yellow", "MA5 > MA20，短線偏多，但尚未完全多頭排列")
    return Signal("均線 整理中", "yellow", "均線交叉整理，方向待確認")


def build_signal_badges(
    fundamentals: StockFundamentals,
    indicators: dict,
) -> list[Signal]:
    badges: list[Signal] = []

    # P/E ratio
    badges.append(classify_pe_ratio(fundamentals.pe_ratio))

    # RSI
    rsi = indicators.get("RSI") or indicators.get("RSI(14)")
    if rsi is not None:
        badges.append(classify_rsi(float(rsi)))

    # MA trend
    ma5  = indicators.get("MA5")
    ma20 = indicators.get("MA20")
    ma60 = indicators.get("MA60")
    if any(v is not None for v in (ma5, ma20, ma60)):
        badges.append(classify_ma_trend(
            float(ma5)  if ma5  is not None else None,
            float(ma20) if ma20 is not None else None,
            float(ma60) if ma60 is not None else None,
        ))

    # High dividend yield
    if fundamentals.dividend_yield is not None and fundamentals.dividend_yield >= 0.04:
        pct = fundamentals.dividend_yield * 100
        badges.append(Signal(
            f"殖利率 {pct:.1f}%",
            "green",
            f"年殖利率 {pct:.1f}%，高於 4% 具配息吸引力",
        ))

    # Revenue growth
    if fundamentals.revenue_growth is not None:
        rg = fundamentals.revenue_growth
        if rg >= 0.10:
            badges.append(Signal(
                f"營收成長 {rg*100:+.0f}%",
                "green",
                f"年營收成長 {rg*100:.1f}%，成長動能強",
            ))
        elif rg < -0.10:
            badges.append(Signal(
                f"營收衰退 {rg*100:.0f}%",
                "red",
                f"年營收衰退 {rg*100:.1f}%，需留意",
            ))

    return badges


# ── Fundamental verdict ───────────────────────────────────────────────────────

def get_fundamental_verdict(f: StockFundamentals) -> str:
    scores: list[int] = []

    if f.pe_ratio is not None:
        if f.pe_ratio < 10:
            scores.append(2)
        elif f.pe_ratio < 20:
            scores.append(1)
        elif f.pe_ratio < 30:
            scores.append(0)
        else:
            scores.append(-1)

    if f.dividend_yield is not None:
        scores.append(1 if f.dividend_yield >= 0.04 else 0)

    if f.revenue_growth is not None:
        if f.revenue_growth >= 0.10:
            scores.append(2)
        elif f.revenue_growth >= 0:
            scores.append(1)
        else:
            scores.append(-1)

    if f.profit_margin is not None:
        scores.append(1 if f.profit_margin >= 0.15 else 0)

    if not scores:
        return "基本面資料不足，無法評估"

    total = sum(scores)
    if total >= 4:
        return "基本面優異，多項指標正向"
    if total >= 2:
        return "基本面良好，整體穩健"
    if total >= 0:
        return "基本面普通，無明顯亮點"
    return "基本面偏弱，需謹慎評估"
