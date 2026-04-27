from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import yfinance as yf


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class TechnicalIndicators:
    code:         str
    close:        float
    ma5:          float
    ma20:         float
    ma60:         float
    rsi:          float   # 0–100
    kd_k:         float   # 0–100
    kd_d:         float   # 0–100
    bb_upper:     float
    bb_mid:       float
    bb_lower:     float
    volume_ratio: float   # 今日量 / 5日均量

    @property
    def is_multi_head(self) -> bool:
        """MA5 > MA20 > MA60 多頭排列。"""
        return self.ma5 > self.ma20 > self.ma60

    @property
    def is_bb_overbought(self) -> bool:
        """股價超出布林上軌（過熱）。"""
        return self.close > self.bb_upper

    @property
    def is_volume_breakout(self) -> bool:
        """成交量 ≥ 5日均量 × 1.5（帶量突破）。"""
        return self.volume_ratio >= 1.5


# ── Pure calculation functions ────────────────────────────────────────────────

def calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    """計算 RSI。資料不足回傳 50.0。"""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[-period:].mean()
    avg_loss = losses[-period:].mean()
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def calc_kd(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 9,
) -> tuple[float, float]:
    """計算 KD（Stochastic Oscillator）。資料不足回傳 (50.0, 50.0)。"""
    if len(closes) < period:
        return 50.0, 50.0
    k, d = 50.0, 50.0
    for i in range(period - 1, len(closes)):
        window_high = highs[i - period + 1 : i + 1].max()
        window_low  = lows[i  - period + 1 : i + 1].min()
        denom = window_high - window_low
        if denom == 0:
            rsv = 50.0
        else:
            rsv = (closes[i] - window_low) / denom * 100
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
    return float(k), float(d)


def calc_bollinger(
    closes: np.ndarray,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float, float, float]:
    """回傳 (upper, mid, lower)。資料不足三值相等。"""
    if len(closes) < period:
        v = float(closes[-1]) if len(closes) > 0 else 0.0
        return v, v, v
    window = closes[-period:]
    mid    = float(window.mean())
    std    = float(window.std(ddof=1))
    return mid + num_std * std, mid, mid - num_std * std


def calc_volume_ratio(volumes: np.ndarray) -> float:
    """今日成交量 ÷ 前 5 日均量。資料不足或均量為 0 回傳 1.0。"""
    if len(volumes) < 2:
        return 1.0
    today   = float(volumes[-1])
    avg5    = float(volumes[-6:-1].mean()) if len(volumes) >= 6 else float(volumes[:-1].mean())
    if avg5 == 0:
        return 1.0
    return today / avg5


# ── Format for AI prompt ──────────────────────────────────────────────────────

def format_indicators_text(ind: TechnicalIndicators) -> str:
    lines = []

    # MA 多頭判斷
    if ind.is_multi_head:
        lines.append(f"均線：多頭排列 ✅（MA5={ind.ma5:.1f} > MA20={ind.ma20:.1f} > MA60={ind.ma60:.1f}）")
    else:
        lines.append(f"均線：非多頭排列（MA5={ind.ma5:.1f}，MA20={ind.ma20:.1f}，MA60={ind.ma60:.1f}）")

    # RSI
    if ind.rsi >= 80:
        rsi_note = "【超買，過熱】"
    elif ind.rsi <= 30:
        rsi_note = "【超賣，低檔反彈機會】"
    elif ind.rsi <= 50:
        rsi_note = "（偏弱）"
    else:
        rsi_note = "（偏強）"
    lines.append(f"RSI：{ind.rsi:.1f} {rsi_note}")

    # KD
    kd_note = ""
    if ind.kd_k > 80:
        kd_note = "【K值高檔，注意過熱】"
    elif ind.kd_k < 20:
        kd_note = "【K值低檔，超賣區】"
    if ind.kd_k > ind.kd_d:
        kd_note += "（K>D，黃金交叉偏多）"
    lines.append(f"KD：K={ind.kd_k:.1f}，D={ind.kd_d:.1f} {kd_note}")

    # Bollinger Bands
    bb_width_pct = (ind.bb_upper - ind.bb_lower) / ind.bb_mid * 100 if ind.bb_mid else 0
    if ind.is_bb_overbought:
        bb_note = f"【股價 {ind.close:.1f} 超出上軌 {ind.bb_upper:.1f}，BBU 過熱，不追】"
    elif ind.close < ind.bb_lower:
        bb_note = f"【股價跌破下軌 {ind.bb_lower:.1f}，超賣區】"
    else:
        bb_note = f"（股價在布林通道內，上軌 {ind.bb_upper:.1f}／下軌 {ind.bb_lower:.1f}）"
    lines.append(f"布林通道：中軌 {ind.bb_mid:.1f}，帶寬 {bb_width_pct:.1f}% {bb_note}")

    # Volume ratio
    if ind.is_volume_breakout:
        vol_note = f"【帶量突破，量比 {ind.volume_ratio:.2f}x ≥ 1.5x，強勢信號】"
    elif ind.volume_ratio < 0.7:
        vol_note = f"（量能萎縮，量比 {ind.volume_ratio:.2f}x，縮量回測中）"
    else:
        vol_note = f"（量比 {ind.volume_ratio:.2f}x，正常）"
    lines.append(f"成交量：{vol_note}")

    return "\n".join(lines)


# ── Data fetcher ──────────────────────────────────────────────────────────────

def fetch_indicators(code: str, days: int = 90) -> Optional[TechnicalIndicators]:
    """從 yfinance 抓歷史資料並計算所有技術指標。失敗回傳 None。"""
    try:
        ticker = f"{code}.TW"
        df = yf.download(ticker, period=f"{days}d", interval="1d",
                         progress=False, auto_adjust=True)

        # yfinance 可能回傳 MultiIndex columns
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)

        if df is None or len(df) < 30:
            return None

        closes  = df["Close"].dropna().values.astype(float)
        highs   = df["High"].dropna().values.astype(float)
        lows    = df["Low"].dropna().values.astype(float)
        volumes = df["Volume"].dropna().values.astype(float)

        if len(closes) < 30:
            return None

        ma5   = float(closes[-5:].mean())
        ma20  = float(closes[-20:].mean()) if len(closes) >= 20 else float(closes.mean())
        ma60  = float(closes[-60:].mean()) if len(closes) >= 60 else float(closes.mean())

        rsi           = calc_rsi(closes)
        kd_k, kd_d   = calc_kd(highs, lows, closes)
        bb_u, bb_m, bb_l = calc_bollinger(closes)
        vol_ratio     = calc_volume_ratio(volumes)

        return TechnicalIndicators(
            code=code, close=float(closes[-1]),
            ma5=ma5, ma20=ma20, ma60=ma60,
            rsi=rsi, kd_k=kd_k, kd_d=kd_d,
            bb_upper=bb_u, bb_mid=bb_m, bb_lower=bb_l,
            volume_ratio=vol_ratio,
        )
    except Exception:
        return None
