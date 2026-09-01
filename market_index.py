"""市場指數工具：取得大盤（加權指數）當日漲跌幅。

資料來源為 Shioaji（Contracts.Indexs.TSE["001"]），不再使用 yfinance——
台股指數在 yfinance 上常延遲、缺值，且與實際交易用的報價來源不一致。

失敗回傳 0.0（而非 None）以維持原有介面：dt_rules 對「大盤 0.0」有既定的
處理路徑（不擋、僅註記），改成 None 會讓所有呼叫端都要判空。
但失敗一定寫 log——8/21 的教訓是降級不能靜默。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def fetch_market_index_change(api=None) -> float:
    """回傳今日加權指數漲跌幅 %，失敗回傳 0.0。

    api=None 時向 shioaji_session 取共用連線；已有連線的呼叫端可直接傳入，
    避免多開 session。
    """
    import shioaji_quotes

    if api is None:
        import shioaji_session
        api = shioaji_session.get_api()

    if api is None:
        log.warning("fetch_market_index_change：無 Shioaji 連線，大盤方向加權暫停")
        return 0.0
    return round(shioaji_quotes.index_change_pct(api), 2)
