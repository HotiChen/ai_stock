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


def calc_vwap(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
) -> float:
    """Volume Weighted Average Price using typical price (H+L+C)/3."""
    if len(closes) == 0:
        return 0.0
    typical   = (highs + lows + closes) / 3.0
    total_vol = float(volumes.sum())
    if total_vol == 0.0:
        return float(closes[-1])
    return float((typical * volumes).sum() / total_vol)


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

    vwap       = calc_vwap(highs[-20:], lows[-20:], closes[-20:], volumes[-20:])

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
        "VWAP":             round(vwap, 2),
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
    """從 Shioaji 抓歷史日線，計算所有指標。失敗回傳 None。

    **這個函式原本一次呼叫 api.kbars 查 150 天，而 Shioaji 的上限是 30 天**
    （回 400 "Kbars date range must not exceed 30 days."）——也就是說它從來
    沒有成功過。以前 _get_indicators 有 yfinance 備援接住，所以沒人發現；
    2026-09-02 移除備援後，8:30 選股就完全拿不到指標，當天零候選。

    改走 shioaji_history.fetch_daily：它會把區間切成 30 天以內的分段，
    並把分鐘 K 聚合成日線（calculate_indicators 要的是日線，原本直接把
    分鐘 K 餵進去其實也是錯的）。
    """
    from datetime import date, timedelta

    import shioaji_history as sh

    try:
        end = date.today()
        start = end - timedelta(days=INDICATOR_HISTORY_DAYS)
        df = sh.fetch_daily(api, code, start, end)
        if df is None or len(df) < 80:
            return None
        return calculate_indicators(df)
    except Exception as e:
        log.debug("fetch_indicators(%s) failed: %s", code, e)
        return None


# ── Shioaji batch fetcher（原 yfinance batch，M4：補齊技術指標資料管道）──────

#: 抓多少日曆天的歷史來算指標。calculate_indicators 需要 >= 80 根日 K；
#: 130 個日曆天約 92 個交易日，已有餘裕。
#:
#: 原本設 180，多抓的 50 天在冷啟動時是實打實的流量：每支約多 3 MB，
#: 50 支候選就多 150 MB——而 Shioaji 每日歷史額度只有 500 MB。
INDICATOR_HISTORY_DAYS = 130


def fetch_indicators_shioaji_batch(codes: list[str], api=None) -> dict[str, dict]:
    """批次計算技術指標，資料來源 Shioaji kbars（分鐘 K 聚合成日線）。

    取代原本的 yfinance 版本：台股在 yfinance 上常缺值、除權息調整與券商端
    不一致，導致 8:30 算出的指標和實際盤中看到的價格對不起來。

    Returns:
        {code: calculate_indicators() dict}，失敗或資料不足的 code 不在 dict 裡。

    效能：每支股票一次 kbars 呼叫（chunk_days 設為整個區間），80 支約 80 次
    呼叫。若改用小 chunk 會變成數百次呼叫，8:30 跑不完。
    """
    if not codes:
        return {}

    if api is None:
        import shioaji_session
        api = shioaji_session.get_api()
    if api is None:
        log.warning("技術指標批次：無 Shioaji 連線，回傳空結果")
        return {}

    from datetime import date, timedelta

    import shioaji_history as sh

    end = date.today()
    start = end - timedelta(days=INDICATOR_HISTORY_DAYS)

    result: dict[str, dict] = {}
    for code in codes:
        # 增量快取：第一次抓完整區間，之後每天只抓新增的那幾根。
        # 原本每天重抓 180 天分鐘 K × 每支候選，是 Shioaji 每日 500 MB
        # 額度被燒光的主因（2026-09-02）。
        df = sh.fetch_daily_cached(api, code, start, end,
                                   chunk_days=INDICATOR_HISTORY_DAYS)
        if df is None or len(df) < 80:
            continue
        try:
            result[code] = calculate_indicators(df)
        except Exception as e:
            log.debug("calc_indicators failed for %s: %s", code, e)

    log.info("技術指標 Shioaji batch：%d/%d 支成功", len(result), len(codes))
    return result


#: 向後相容別名。舊名字裡的 "yfinance" 已不再反映實際來源，新程式碼請用
#: fetch_indicators_shioaji_batch。
fetch_indicators_yfinance_batch = fetch_indicators_shioaji_batch


# ── fetch_intraday_vwap ───────────────────────────────────────────────────────

def fetch_intraday_vwap(code: str, api=None) -> Optional[float]:
    """今日盤中 VWAP，資料來源 Shioaji 1 分 K。取不到回 None。

    原本有 yfinance 備援，已移除：台股分鐘 K 在 yfinance 上延遲且常缺值，
    與實際交易用的報價來源不一致，算出的 VWAP 會和券商端對不起來。
    api=None 時向 shioaji_session 取共用連線。
    """
    if api is None:
        import shioaji_session
        api = shioaji_session.get_api(connect=False)

    # Shioaji 1-min kbars
    if api is not None:
        try:
            from datetime import date as _date
            import pandas as _pd
            today = _date.today().strftime("%Y-%m-%d")
            contract = api.Contracts.Stocks.get(code)
            kbars = api.kbars(
                contract,
                start=today, end=today,
                timeframe=api.constant.Timeframe.Minute,
            )
            if kbars and kbars.get("ts") and len(kbars["ts"]) > 0:
                df = _pd.DataFrame(kbars)
                return round(calc_vwap(
                    df["High"].values.astype(float),
                    df["Low"].values.astype(float),
                    df["Close"].values.astype(float),
                    df["Volume"].values.astype(float),
                ), 2)
        except Exception as e:
            log.debug("Shioaji intraday VWAP failed for %s: %s", code, e)

    log.debug("fetch_intraday_vwap(%s)：Shioaji 無分鐘 K，回傳 None", code)
    return None
