"""
shioaji_quotes.py — Shioaji 報價層（取代 yfinance 的即時價 / 股名 / 指數）

設計原則：**沒有報價就回 None，絕不回 0.0**
------------------------------------------
Shioaji 報價 session 未暖機時，snapshot 的 close 會是 0。那不是「這支股票
值 0 元」，是「現在沒有報價」。若原樣回傳，下游會拿 0 去算損益、算部位、
算停損，得到看似合理但完全錯誤的數字，而且不會有任何錯誤訊息。

這正是 8/21 那次事故的模式（詳見 LESSONS.md 錯誤 3、錯誤 8）：外部資料的
「空值」被當成「真實值」，靜默污染整條流程。所以本模組一律把 0 視為缺值。

指數的漲跌幅失敗時回 0.0（而非 None），是為了與 market_index
.fetch_market_index_change 原本的行為一致——dt_rules 對「大盤 0.0」有
既定的處理路徑（不擋、僅註記），改成 None 反而會讓呼叫端到處要判空。
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

log = logging.getLogger(__name__)

TSE_INDEX_CODE = "001"


def _contract(api, code: str):
    if api is None:
        return None
    try:
        return api.Contracts.Stocks.get(code)
    except Exception as e:
        # 8:30 券商剛登入、合約尚未下載完成是常態，不得拋出
        log.debug("取合約失敗 %s: %s", code, e)
        return None


def stock_name(api, code: str) -> str:
    """中文股名。取不到回傳 code 本身（與原 yfinance 版本行為一致）。"""
    c = _contract(api, code)
    name = getattr(c, "name", None) if c is not None else None
    return name or code


def latest_price(api, code: str) -> Optional[float]:
    """最新成交價。無報價或報價為 0 一律回 None。"""
    c = _contract(api, code)
    if c is None:
        return None
    try:
        snaps = api.snapshots([c])
    except Exception as e:
        log.debug("snapshots(%s) 失敗: %s", code, e)
        return None
    if not snaps:
        return None
    close = getattr(snaps[0], "close", None)
    if not close or close <= 0:
        return None
    return float(close)


def batch_prices(api, codes: Sequence[str]) -> dict:
    """多檔最新價 {code: close}。取不到或為 0 的 code **不會出現在結果裡**。

    刻意不填 0.0 佔位：缺一筆讓呼叫端自己決定怎麼辦，比塞一個假價格安全。
    """
    if api is None or not codes:
        return {}
    from market_scan import batch_fetch_snapshots

    try:
        snaps = batch_fetch_snapshots(api, list(codes))
    except Exception as e:
        log.warning("batch_fetch_snapshots 失敗: %s", e)
        return {}

    out = {}
    for code, row in (snaps or {}).items():
        close = row.get("close") if isinstance(row, dict) else None
        if close and close > 0:
            out[code] = float(close)
    missing = len(codes) - len(out)
    if missing:
        log.warning("批次報價：%d/%d 檔無報價（Shioaji）", missing, len(codes))
    return out


def _index_contract(api):
    """加權指數合約。

    Shioaji SDK 版本間路徑不一致：新版是 Contracts.Indexs.TSE["001"]，
    舊版程式碼裡也出現過 Contracts.Indices["TAIEX"]。兩種都試，取不到回 None。
    """
    if api is None:
        return None

    # 路徑 1：Contracts.Indexs.TSE["001"]
    try:
        tse = api.Contracts.Indexs.TSE
        if tse is not None:
            try:
                return tse[TSE_INDEX_CODE]
            except Exception:
                got = tse.get(TSE_INDEX_CODE) if hasattr(tse, "get") else None
                if got is not None:
                    return got
    except Exception:
        pass

    # 路徑 2：Contracts.Indices["TAIEX"]（舊 SDK）
    try:
        return api.Contracts.Indices["TAIEX"]
    except Exception:
        return None


def _index_snapshot(api):
    c = _index_contract(api)
    if c is None:
        return None
    try:
        snaps = api.snapshots([c])
    except Exception as e:
        log.debug("加權指數 snapshot 失敗: %s", e)
        return None
    return snaps[0] if snaps else None


def index_price(api) -> Optional[float]:
    """加權指數現值，取不到回 None。"""
    s = _index_snapshot(api)
    close = getattr(s, "close", None) if s is not None else None
    return float(close) if close and close > 0 else None


def index_change_pct(api) -> float:
    """加權指數漲跌幅 %，取不到回 0.0（與 market_index 原行為一致）。"""
    s = _index_snapshot(api)
    if s is None:
        log.warning("取不到加權指數報價，大盤方向加權暫停")
        return 0.0
    rate = getattr(s, "change_rate", None)
    return float(rate) if rate is not None else 0.0
