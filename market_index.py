"""市場指數工具：取得大盤（加權指數）當日漲跌幅。"""
from __future__ import annotations

import logging

import yfinance as yf

log = logging.getLogger(__name__)

_TWII = "^TWII"


def fetch_market_index_change() -> float:
    """回傳今日加權指數漲跌幅 %，失敗回傳 0.0。"""
    try:
        ticker = yf.Ticker(_TWII)
        df = ticker.history(period="2d")
        if df.empty or len(df) < 2:
            return 0.0
        prev_close  = df["Close"].iloc[-2]
        today_close = df["Close"].iloc[-1]
        if prev_close == 0:
            return 0.0
        return round((today_close - prev_close) / prev_close * 100, 2)
    except Exception as e:
        log.warning("fetch_market_index_change 失敗：%s", e)
        return 0.0
