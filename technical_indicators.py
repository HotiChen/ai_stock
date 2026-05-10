from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class TechnicalIndicators:
    code:         str
    close:        float
    ma5:          float
    ma20:         float
    ma60:         float
    rsi:          float
    kd_k:         float
    kd_d:         float
    bb_upper:     float
    bb_mid:       float
    bb_lower:     float
    volume_ratio: float

    @property
    def is_multi_head(self) -> bool:
        return self.ma5 > self.ma20 > self.ma60

    @property
    def is_bb_overbought(self) -> bool:
        return self.close > self.bb_upper

    @property
    def is_volume_breakout(self) -> bool:
        return self.volume_ratio >= 1.5


# ── Pure calculation functions ────────────────────────────────────────────────

def calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas   = np.diff(closes)
    gains    = np.where(deltas > 0, deltas, 0.0)
    losses   = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[-period:].mean()
    avg_loss = losses[-period:].mean()
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return float(100 - 100 / (1 + avg_gain / avg_loss))


def calc_kd(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 9,
) -> tuple[float, float]:
    if len(closes) < period:
        return 50.0, 50.0
    k, d = 50.0, 50.0
    for i in range(period - 1, len(closes)):
        window_high = highs[i - period + 1 : i + 1].max()
        window_low  = lows[i  - period + 1 : i + 1].min()
        denom = window_high - window_low
        rsv   = (closes[i] - window_low) / denom * 100 if denom != 0 else 50.0
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
    return float(k), float(d)


def calc_bollinger(
    closes: np.ndarray,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float, float, float]:
    if len(closes) < period:
        v = float(closes[-1]) if len(closes) > 0 else 0.0
        return v, v, v
    window = closes[-period:]
    mid    = float(window.mean())
    std    = float(window.std(ddof=1))
    return mid + num_std * std, mid, mid - num_std * std


def calc_volume_ratio(volumes: np.ndarray) -> float:
    if len(volumes) < 2:
        return 1.0
    today = float(volumes[-1])
    avg5  = float(volumes[-6:-1].mean()) if len(volumes) >= 6 else float(volumes[:-1].mean())
    return today / avg5 if avg5 != 0 else 1.0


def calc_macd(
    closes: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float, float]:
    """回傳 (MACD_histogram_today, MACD_histogram_prev)。資料不足回傳 (0.0, 0.0)。"""
    if len(closes) < slow + signal:
        return 0.0, 0.0

    def _ema(arr: np.ndarray, n: int) -> np.ndarray:
        alpha  = 2 / (n + 1)
        result = np.zeros(len(arr))
        result[0] = arr[0]
        for i in range(1, len(arr)):
            result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
        return result

    ema_fast   = _ema(closes, fast)
    ema_slow   = _ema(closes, slow)
    macd_line  = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram  = macd_line - signal_line
    return float(histogram[-1]), float(histogram[-2])


def calc_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> float:
    """Average True Range。資料不足回傳 0.0。"""
    if len(closes) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        tr_list.append(tr)
    tr_arr = np.array(tr_list)
    return float(tr_arr[-period:].mean())


# ── calculate_indicators: dict format for rules.py ────────────────────────────

def _df_to_arrays(df) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """DataFrame → (closes, highs, lows, volumes)。"""
    if hasattr(df.columns, "levels"):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    closes  = df["Close"].dropna().values.astype(float)
    highs   = df["High"].dropna().values.astype(float)
    lows    = df["Low"].dropna().values.astype(float)
    volumes = df["Volume"].dropna().values.astype(float)
    return closes, highs, lows, volumes


def calculate_indicators(df) -> dict:
    """
    輸入 OHLCV DataFrame（至少 80 筆），
    輸出完整指標 dict，直接供 rules.py 使用。
    """
    closes, highs, lows, volumes = _df_to_arrays(df)
    if len(closes) < 80:
        raise ValueError(f"資料不足，需要至少 80 筆，目前只有 {len(closes)} 筆")

    price = float(closes[-1])
    ma5   = float(closes[-5:].mean())
    ma10  = float(closes[-10:].mean())
    ma20  = float(closes[-20:].mean())
    ma60  = float(closes[-60:].mean())

    rsi          = calc_rsi(closes)
    kd_k, kd_d  = calc_kd(highs, lows, closes)
    # prev KD: recalc on closes[:-1]
    if len(closes) > 1:
        kd_k_p, kd_d_p = calc_kd(highs[:-1], lows[:-1], closes[:-1])
    else:
        kd_k_p, kd_d_p = 50.0, 50.0

    macd_hist, macd_hist_prev = calc_macd(closes)
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes)
    bb_pos  = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) != 0 else 0.5
    vol_r   = calc_volume_ratio(volumes)
    atr     = calc_atr(highs, lows, closes)

    resistance = float(highs[-20:].max())
    support    = float(lows[-20:].min())

    bullish = bool(price > ma5 > ma10 > ma20)
    bearish = bool(price < ma5 < ma10 < ma20)

    return {
        "current_price":    round(price,  2),
        "MA5":              round(ma5,   2),
        "MA10":             round(ma10,  2),
        "MA20":             round(ma20,  2),
        "MA60":             round(ma60,  2),
        "RSI":              round(rsi,   2),
        "KD_K":             round(kd_k,  2),
        "KD_D":             round(kd_d,  2),
        "KD_K_prev":        round(kd_k_p, 2),
        "KD_D_prev":        round(kd_d_p, 2),
        "MACD_hist":        round(macd_hist,      4),
        "MACD_hist_prev":   round(macd_hist_prev, 4),
        "BB_upper":         round(bb_upper,  2),
        "BB_lower":         round(bb_lower,  2),
        "BB_position":      round(float(bb_pos), 2),
        "volume_ratio":     round(float(vol_r),  2),
        "resistance":       round(resistance, 2),
        "support":          round(support,    2),
        "ATR":              round(atr, 2),
        "bullish_alignment": bullish,
        "bearish_alignment": bearish,
        "trailing_stop":    round(price * 0.93, 2),
        "stop_loss_ma20":   round(ma20  * 0.99, 2),
    }


# ── Format for AI prompt ──────────────────────────────────────────────────────

def format_indicators_text(ind) -> str:
    """接受 TechnicalIndicators dataclass 或 calculate_indicators() 的 dict。"""
    if isinstance(ind, dict):
        close     = ind["current_price"]
        ma5, ma20, ma60 = ind["MA5"], ind["MA20"], ind["MA60"]
        rsi       = ind["RSI"]
        kd_k, kd_d = ind["KD_K"], ind["KD_D"]
        bb_upper  = ind["BB_upper"]
        bb_lower  = ind["BB_lower"]
        bb_mid    = (bb_upper + bb_lower) / 2
        vol_r     = ind["volume_ratio"]
        multi_head   = ind.get("bullish_alignment", ma5 > ma20 > ma60)
        bb_overbought = close > bb_upper
        vol_break = vol_r >= 1.5
    else:
        close, ma5, ma20, ma60 = ind.close, ind.ma5, ind.ma20, ind.ma60
        rsi       = ind.rsi
        kd_k, kd_d = ind.kd_k, ind.kd_d
        bb_upper, bb_mid, bb_lower = ind.bb_upper, ind.bb_mid, ind.bb_lower
        vol_r     = ind.volume_ratio
        multi_head    = ind.is_multi_head
        bb_overbought = ind.is_bb_overbought
        vol_break     = ind.is_volume_breakout

    lines = []

    if multi_head:
        lines.append(f"均線：多頭排列 ✅（MA5={ma5:.1f} > MA20={ma20:.1f} > MA60={ma60:.1f}）")
    else:
        lines.append(f"均線：非多頭排列（MA5={ma5:.1f}，MA20={ma20:.1f}，MA60={ma60:.1f}）")

    rsi_note = "【超買】" if rsi >= 80 else "【超賣，低檔反彈機會】" if rsi <= 30 else ("（偏弱）" if rsi <= 50 else "（偏強）")
    lines.append(f"RSI：{rsi:.1f} {rsi_note}")

    kd_note = "【K值高檔】" if kd_k > 80 else "【K值低檔超賣】" if kd_k < 20 else ""
    kd_note += "（K>D 偏多）" if kd_k > kd_d else ""
    lines.append(f"KD：K={kd_k:.1f}，D={kd_d:.1f} {kd_note}")

    bb_width_pct = (bb_upper - bb_lower) / ((bb_upper + bb_lower) / 2) * 100
    if bb_overbought:
        bb_note = f"【股價 {close:.1f} 超出上軌 {bb_upper:.1f}，BBU 過熱，不追】"
    elif close < bb_lower:
        bb_note = f"【股價跌破下軌 {bb_lower:.1f}，超賣區】"
    else:
        bb_note = f"（上軌 {bb_upper:.1f}／下軌 {bb_lower:.1f}）"
    lines.append(f"布林通道：帶寬 {bb_width_pct:.1f}% {bb_note}")

    if vol_break:
        vol_note = f"【帶量突破，量比 {vol_r:.2f}x ≥ 1.5x】"
    elif vol_r < 0.7:
        vol_note = f"（量能萎縮，量比 {vol_r:.2f}x，縮量回測中）"
    else:
        vol_note = f"（量比 {vol_r:.2f}x）"
    lines.append(f"成交量：{vol_note}")

    return "\n".join(lines)


# ── Data fetcher（100 日）─────────────────────────────────────────────────────

def fetch_indicators(api, code: str) -> Optional[dict]:
    """
    從 Shioaji 抓 100 日歷史，計算所有指標。
    回傳 calculate_indicators() 的 dict，失敗回傳 None。
    """
    import pandas as pd
    from datetime import date, timedelta
    
    try:
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            return None
            
        end = date.today()
        start = end - timedelta(days=150) # Approx 100 trading days
        
        kbars = api.kbars(contract, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
        if not kbars or not kbars.get('ts') or len(kbars['ts']) < 80:
            return None
            
        df = pd.DataFrame({**kbars})
        return calculate_indicators(df)
    except Exception:
        return None


# ── yfinance batch fetcher（M4：補齊技術指標資料管道）───────────────────────────

def fetch_indicators_yfinance_batch(codes: list[str]) -> dict[str, dict]:
    """
    批次透過 yfinance 抓 6 個月日線，計算所有技術指標。
    在 Shioaji 無法取得 kbars（simulation / 盤前）時作為主要資料來源。

    Returns:
        {code: calculate_indicators() dict}，失敗或資料不足的 code 不在 dict 裡。
    """
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        log.warning("yfinance 未安裝，無法取得技術指標")
        return {}

    if not codes:
        return {}

    def _suffix(code: str) -> str:
        return f"{code}.TW" if code.isdigit() else f"{code}.TWO"

    tickers: dict[str, str] = {code: _suffix(code) for code in codes}
    symbols = list(tickers.values())

    # ── 批次下載 ─────────────────────────────────────────────────────────────
    try:
        raw = yf.download(
            symbols,
            period="6mo",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        log.warning("yfinance batch OHLCV download failed: %s", e)
        return {}

    if raw is None or raw.empty:
        return {}

    # ── 拆分每支股票的 DataFrame ──────────────────────────────────────────────
    data_map: dict[str, "pd.DataFrame"] = {}
    is_multi = isinstance(raw.columns, pd.MultiIndex)

    if is_multi:
        for sym in symbols:
            try:
                df = raw.xs(sym, level=1, axis=1).dropna()
                if len(df) >= 80:
                    data_map[sym] = df
            except KeyError:
                pass
    else:
        # 單支 symbol fallback（symbols 長度為 1 時 yfinance 不返回 MultiIndex）
        df = raw.dropna()
        if len(df) >= 80 and symbols:
            data_map[symbols[0]] = df

    # ── 計算指標 ──────────────────────────────────────────────────────────────
    result: dict[str, dict] = {}
    for code, symbol in tickers.items():
        df = data_map.get(symbol)
        if df is None:
            continue
        try:
            result[code] = calculate_indicators(df)
        except Exception as e:
            log.debug("calc_indicators failed for %s: %s", code, e)

    log.info("技術指標 yfinance batch：%d/%d 支成功", len(result), len(codes))
    return result
