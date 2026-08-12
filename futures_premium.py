"""
futures_premium.py — 台指期貨溢價/貼水

計算台指期（TXF）與加權指數現貨之間的溢貼水，
供盤前當沖分析使用。

資料來源優先順序：
  現貨：Shioaji 指數快照 → yfinance ^TWII
  期貨：Shioaji TXF 快照 → TWSE MIS API
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT  = 8
_FLAT_THRESHOLD   = 0.05   # ±0.05% 以內視為平水
_SCORE_THRESHOLD  = 0.3    # ±0.3% 以上才影響評分（供外部使用）

# TWSE MIS API — 台指期近月合約（B5 = 近月，月份代碼每月更新）
_TWSE_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_TX_EX_CH     = "tse_TXFB5.tw"


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class FuturesPremium:
    spot_index:    float   # 加權指數現值
    futures_price: float   # 台指期近月成交價
    premium:       float   # futures - spot（正=溢價，負=貼水）
    premium_pct:   float   # premium / spot * 100
    label:         str     # 人讀標籤


# ── Pure calculation ──────────────────────────────────────────────────────────

def calc_premium(spot: float, futures: float) -> dict:
    """計算溢貼水，回傳 dict(premium, premium_pct, label)。"""
    premium = round(futures - spot, 2)
    premium_pct = round(premium / spot * 100, 3) if spot else 0.0

    if abs(premium_pct) < _FLAT_THRESHOLD:
        label = "平水"
    elif premium > 0:
        label = f"溢價 {premium:.0f} 點（+{premium_pct:.2f}%）"
    else:
        label = f"貼水 {abs(premium):.0f} 點（{premium_pct:.2f}%）"

    return {"premium": premium, "premium_pct": premium_pct, "label": label}


# ── Data fetchers ─────────────────────────────────────────────────────────────

def fetch_spot_index(api=None) -> Optional[float]:
    """取加權指數現值：Shioaji → yfinance → None。"""
    # 1. Shioaji 指數快照
    if api is not None:
        try:
            snap = api.snapshots([api.Contracts.Indices["TAIEX"]])
            if snap:
                return round(float(snap[0].close), 2)
        except Exception as e:
            log.debug("Shioaji spot index failed: %s", e)

    # 2. yfinance ^TWII
    try:
        import yfinance as yf
        df = yf.Ticker("^TWII").history(period="1d")
        if df is not None and not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    except Exception as e:
        log.debug("yfinance spot index failed: %s", e)

    return None


def _near_month_txf(api):
    """取台指期近月合約物件。

    ``api.Contracts.Futures.TXF`` 是**合約集合**（StreamMultiContract），底下才是
    TXFR1／TXFR2／TXF202608… 等真正的合約。原本直接把集合丟進 ``snapshots()``，
    永遠取不到報價，於是溢貼水一路是 None，最後在 _fetch_market 被寫成 0.0。

    優先 ``TXFR1``（近月連續，不必自己算轉倉）；模擬環境偶爾沒有這個別名，
    退而取代碼最小的月份合約（即最近到期的那個）。
    """
    txf = api.Contracts.Futures.TXF
    r1 = getattr(txf, "TXFR1", None)
    if r1 is not None:
        return r1
    months = sorted(c for c in dir(txf) if c.startswith("TXF2"))
    return getattr(txf, months[0]) if months else None


def fetch_futures_price(api=None) -> Optional[float]:
    """取台指期近月成交價：Shioaji → TWSE MIS API → None。"""
    # 1. Shioaji TXF 快照
    if api is not None:
        try:
            contract = _near_month_txf(api)
            if contract is not None:
                snaps = api.snapshots([contract])
                if snaps:
                    return round(float(snaps[0].close), 2)
        except Exception as e:
            log.debug("Shioaji futures price failed: %s", e)

    # 2. TWSE MIS API fallback
    try:
        import requests as _req
        resp = _req.get(
            _TWSE_MIS_URL,
            params={"ex_ch": _TX_EX_CH},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.ok:
            msg = resp.json().get("msgArray", [])
            if msg:
                price = msg[0].get("z", "")
                if price and price != "-":
                    return round(float(price), 2)
    except Exception as e:
        log.debug("TWSE MIS futures price failed: %s", e)

    return None


# ── Main entry ────────────────────────────────────────────────────────────────

def fetch_futures_premium(api=None) -> Optional[FuturesPremium]:
    """取台指期溢貼水。任一資料來源失敗即回傳 None。"""
    spot    = fetch_spot_index(api=api)
    futures = fetch_futures_price(api=api)

    if spot is None or futures is None:
        return None

    p = calc_premium(spot, futures)
    return FuturesPremium(
        spot_index=spot,
        futures_price=futures,
        premium=p["premium"],
        premium_pct=p["premium_pct"],
        label=p["label"],
    )
