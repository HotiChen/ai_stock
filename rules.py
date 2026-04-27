from __future__ import annotations


def evaluate_signals(ind: dict, prev_ind: dict | None = None) -> list[dict]:
    """
    輸入 calculate_indicators() 輸出的 dict，
    回傳觸發的訊號清單，每筆 {"type", "name", "weight"}。
    """
    signals = []
    p = ind

    # ── 一、均線訊號 ──────────────────────────────────────────────
    if prev_ind:
        if prev_ind["MA5"] < prev_ind["MA20"] and p["MA5"] > p["MA20"]:
            signals.append({"type": "BUY",  "name": "均線黃金交叉", "weight": 2})
        if prev_ind["MA5"] > prev_ind["MA20"] and p["MA5"] < p["MA20"]:
            signals.append({"type": "SELL", "name": "均線死亡交叉", "weight": 2})

    if p.get("bullish_alignment"):
        signals.append({"type": "BUY",  "name": "均線多頭排列", "weight": 1})
    if p.get("bearish_alignment"):
        signals.append({"type": "SELL", "name": "均線空頭排列", "weight": 1})

    # ── 二、支撐壓力訊號 ──────────────────────────────────────────
    price      = p["current_price"]
    resistance = p["resistance"]
    support    = p["support"]

    if price > resistance * 0.995 and p["volume_ratio"] > 1.5:
        signals.append({"type": "BUY",   "name": "帶量突破壓力", "weight": 3})
    if price < support * 1.005:
        signals.append({"type": "SELL",  "name": "跌破支撐",     "weight": 3})
    if resistance * 0.97 <= price <= resistance:
        signals.append({"type": "WATCH", "name": "接近壓力位",   "weight": 1})
    if support <= price <= support * 1.03:
        signals.append({"type": "WATCH", "name": "接近支撐位",   "weight": 1})

    # ── 三、成交量訊號 ────────────────────────────────────────────
    if p["volume_ratio"] > 1.5 and price > p["MA20"]:
        signals.append({"type": "BUY",   "name": "帶量站上均線", "weight": 2})
    if p["volume_ratio"] < 0.7 and price > support:
        signals.append({"type": "BUY",   "name": "量縮回測支撐", "weight": 2})
    if p["volume_ratio"] > 2.0 and price < p["MA20"]:
        signals.append({"type": "SELL",  "name": "爆量長黑",     "weight": 3})
    if prev_ind and p["volume_ratio"] > 1.2 and price < prev_ind["current_price"]:
        signals.append({"type": "WATCH", "name": "量增價跌",     "weight": 1})

    # ── 四、KD 訊號 ───────────────────────────────────────────────
    if prev_ind:
        k, d = p["KD_K"], p["KD_D"]
        pk, pd = p["KD_K_prev"], p["KD_D_prev"]
        if k < 20 and pk < pd and k > d:
            signals.append({"type": "BUY",  "name": "KD 低檔黃金交叉", "weight": 3})
        if k > 80 and pk > pd and k < d:
            signals.append({"type": "SELL", "name": "KD 高檔死亡交叉", "weight": 3})

    if p["KD_K"] > 80 and p["KD_D"] > 80:
        signals.append({"type": "WATCH", "name": "KD 超買鈍化", "weight": 1})

    # ── 五、RSI 訊號 ──────────────────────────────────────────────
    if p["RSI"] < 30:
        signals.append({"type": "BUY",   "name": "RSI 超賣", "weight": 2})
    if p["RSI"] > 70:
        signals.append({"type": "WATCH", "name": "RSI 超買", "weight": 1})
    if prev_ind and prev_ind["RSI"] < 50 and p["RSI"] > 50:
        signals.append({"type": "BUY",   "name": "RSI 穿越 50", "weight": 2})

    # ── 六、MACD 訊號 ─────────────────────────────────────────────
    if p["MACD_hist_prev"] < 0 and p["MACD_hist"] > 0:
        signals.append({"type": "BUY",  "name": "MACD 柱狀翻正", "weight": 2})
    if p["MACD_hist_prev"] > 0 and p["MACD_hist"] < 0:
        signals.append({"type": "SELL", "name": "MACD 柱狀翻負", "weight": 2})

    return signals


def score_signals(signals: list[dict]) -> dict:
    """
    把訊號清單換算成綜合分數與建議。
    """
    buy_score   = sum(s["weight"] for s in signals if s["type"] == "BUY")
    sell_score  = sum(s["weight"] for s in signals if s["type"] == "SELL")
    watch_score = sum(s["weight"] for s in signals if s["type"] == "WATCH")

    if buy_score >= 5 and sell_score == 0:
        recommendation = "強力買進"
    elif buy_score >= 3 and sell_score == 0:
        recommendation = "買進"
    elif sell_score >= 5:
        recommendation = "強力賣出"
    elif sell_score >= 3:
        recommendation = "賣出"
    elif buy_score > 0 and sell_score == 0:
        recommendation = "觀察偏多"
    else:
        recommendation = "觀察"

    return {
        "buy_score":      buy_score,
        "sell_score":     sell_score,
        "watch_score":    watch_score,
        "recommendation": recommendation,
        "signals":        [s["name"] for s in signals],
    }
