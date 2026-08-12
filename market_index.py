"""市場指數工具：取得大盤（加權指數）當日漲跌幅。

資料來源優先序：Shioaji → yfinance → 無。

為什麼是這個順序：yfinance 在這台機器上長期取不到台股資料（`^TWII` 與個股的
`.TW`／`.TWO` 都常回空），而 Shioaji 連線本來就已經為了報價與下單而建立，
拿指數只是順便。2026-08-12 09:05 就是因為只走 yfinance 而讀到 0.0，
導致當日 8 檔當沖候選全部被判定「大盤無方向」而放棄——當天大盤實際漲 0.63%。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import yfinance as yf

log = logging.getLogger(__name__)

_TWII = "^TWII"

#: 加權指數在 Shioaji 的合約代碼（api.Contracts.Indexs.TSE）。
_TSE_INDEX_CODE = "001"

_index_api = None
_index_api_tried = False


def _get_index_api():
    """取得（或建立）供查指數用的 Shioaji 連線。

    自己維護 singleton 而不是共用 telegram_bot 的那個，是因為 telegram_bot
    已經 import 本模組，反向 import 會造成循環相依。登入失敗只記一次
    warning 就放棄，之後直接回 None，不重試——盤中每分鐘重登會拖慢流程。
    """
    global _index_api, _index_api_tried
    if _index_api is not None or _index_api_tried:
        return _index_api
    _index_api_tried = True
    try:
        import shioaji as sj

        api = sj.Shioaji(
            simulation=os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"
        )
        api.login(
            os.getenv("SHIOAJI_API_KEY", ""),
            os.getenv("SHIOAJI_SECRET_KEY", ""),
        )
        _index_api = api
    except Exception as e:
        log.warning("指數用 Shioaji 連線建立失敗，改用 yfinance：%s", e)
        _index_api = None
    return _index_api


def _from_shioaji() -> Optional[float]:
    api = _get_index_api()
    if api is None:
        return None
    try:
        contract = api.Contracts.Indexs.TSE[_TSE_INDEX_CODE]
        snapshots = api.snapshots([contract])
        if not snapshots:
            return None
        rate = getattr(snapshots[0], "change_rate", None)
        # 欄位缺漏視同這個來源失敗。不可當成 0%——那正是本次修正要杜絕的誤判。
        return None if rate is None else round(float(rate), 2)
    except Exception as e:
        log.debug("Shioaji 取指數失敗：%s", e)
        return None


def _from_yfinance() -> Optional[float]:
    try:
        df = yf.Ticker(_TWII).history(period="2d")
        if df.empty or len(df) < 2:
            return None
        prev_close = df["Close"].iloc[-2]
        today_close = df["Close"].iloc[-1]
        if prev_close == 0:
            return None
        return round((today_close - prev_close) / prev_close * 100, 2)
    except Exception as e:
        log.debug("yfinance 取指數失敗：%s", e)
        return None


def fetch_market_index_pct() -> Optional[float]:
    """今日加權指數漲跌幅（%）；兩個來源都取不到時回傳 ``None``。

    ``None`` 與 ``0.0`` 的差別是本函式存在的理由：前者是「不知道大盤怎麼走」，
    後者是「大盤確實收在平盤」。餵給 LLM 時這兩者會導向完全不同的決策，
    呼叫端必須自己區分。
    """
    pct = _from_shioaji()
    if pct is not None:
        return pct
    return _from_yfinance()


def fetch_market_index_change() -> float:
    """回傳今日加權指數漲跌幅 %，失敗回傳 0.0。

    向後相容用的包裝，僅供「純顯示」的呼叫端使用（notion_reporter、
    telegram_bot 的日誌欄位）。**任何會影響交易決策的地方請改用
    :func:`fetch_market_index_pct`**，否則取不到資料會被當成平盤。
    """
    pct = fetch_market_index_pct()
    return 0.0 if pct is None else pct
