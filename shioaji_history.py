"""
shioaji_history.py — Shioaji kbars 歷史資料層（取代 yfinance）

為什麼不用 yfinance
-------------------
1. **分鐘 K 的保存期限**：yfinance 的 1m interval 只保留最近 7 天。回填 60 個
   交易日、以及模擬層要判斷「當日先觸停利還是先觸停損」，都需要更久以前的
   分鐘資料，yfinance 給不了。只有日 OHLC 時，兩者都碰到就只能保守假設停損
   先觸發，會系統性低估績效。
2. **資料品質**：台股在 yfinance 上常缺值、除權息調整不一致；Shioaji 是券商
   原始資料，與實際成交一致。

Shioaji kbars 格式
------------------
``api.kbars(contract, start="YYYY-MM-DD", end="YYYY-MM-DD")`` 回傳 dict：

    {"ts": [奈秒 epoch, ...], "Open": [...], "High": [...],
     "Low": [...], "Close": [...], "Volume": [...]}

粒度是 **1 分鐘**。日線由分鐘 K 聚合而來（本模組的 resample_daily）。

分段抓取
--------
81 檔 × 400 天 × 每日約 270 根 ≈ 870 萬根分鐘 K，一次抓進記憶體不切實際。
``date_chunks`` 把區間切段，呼叫端抓一段就立刻聚合成日線、丟掉分鐘資料，
記憶體維持在單段的量級。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional, Sequence

log = logging.getLogger(__name__)

_OHLCV = ("Open", "High", "Low", "Close", "Volume")


# ══════════════════════════════════════════════════════════════════════════════
# 純轉換
# ══════════════════════════════════════════════════════════════════════════════

def kbars_to_df(kbars) -> Optional["object"]:
    """Shioaji kbars dict → DataFrame（DatetimeIndex，欄位 Open/High/Low/Close/Volume）。

    缺欄位或空資料一律回 None——半份資料流進指標計算，會算出看似合理但錯誤的
    數字，且不會有任何錯誤訊息。
    """
    import pandas as pd

    if not kbars:
        return None
    ts = kbars.get("ts") if hasattr(kbars, "get") else None
    if not ts:
        return None
    if any(kbars.get(c) is None for c in _OHLCV):
        log.debug("kbars_to_df: 缺少欄位，捨棄整筆")
        return None

    df = pd.DataFrame({c: list(kbars[c]) for c in _OHLCV},
                      index=pd.to_datetime(list(ts)))
    # 不假設 Shioaji 已排序：亂序會讓日線的 open/close 取到錯的那一根
    return df.sort_index()


def resample_daily(minute_df) -> Optional["object"]:
    """分鐘 K → 日線 OHLCV（open=當日首根、close=末根、high/low 全日、volume 加總）。"""
    if minute_df is None or len(minute_df) == 0:
        return None
    daily = minute_df.resample("1D").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna(subset=["Open", "Close"])
    return daily


def bars_for_day(minute_df, day: date) -> list[dict]:
    """取某一交易日的分鐘 K，回傳 [{open, high, low, close}, ...]，依時間排序。

    鍵名刻意用小寫，與 daytrading_review._determine_outcome 既有的 bar 格式
    一致，模擬層才能直接沿用那套「逐根掃描找先觸發者」的判斷。
    """
    if minute_df is None or len(minute_df) == 0:
        return []
    sel = minute_df[[ts.date() == day for ts in minute_df.index]]
    return [
        {"open": float(r["Open"]), "high": float(r["High"]),
         "low": float(r["Low"]), "close": float(r["Close"])}
        for _, r in sel.iterrows()
    ]


def date_chunks(start: date, end: date, chunk_days: int = 30) -> list[tuple[date, date]]:
    """把 [start, end] 切成連續、不重疊、無空隙的區段。

    空隙會讓指標少幾根 K；重疊會聚合出重複的日線。兩者都不會報錯，只會靜默
    污染資料——所以有專門的測試釘住「相鄰段剛好差一天」。
    """
    if end < start:
        return []
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        out.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 網路 I/O（薄層）
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_kbars(api, code: str, start: date, end: date):
    """單次 kbars 呼叫，失敗回 None（不對外拋出，一支失敗不該中斷整批）。"""
    try:
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            log.debug("找不到合約：%s", code)
            return None
        return api.kbars(contract,
                         start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"))
    except Exception as e:
        log.debug("kbars(%s, %s~%s) 失敗: %s", code, start, end, e)
        return None


def fetch_daily(api, code: str, start: date, end: date,
                chunk_days: int = 30) -> Optional["object"]:
    """抓 [start, end] 的分鐘 K 並聚合成日線。分段抓取以控制記憶體。"""
    import pandas as pd

    frames = []
    for c_start, c_end in date_chunks(start, end, chunk_days):
        df = kbars_to_df(_fetch_kbars(api, code, c_start, c_end))
        if df is None:
            continue
        daily = resample_daily(df)     # 立刻聚合，分鐘資料隨即釋放
        if daily is not None and len(daily):
            frames.append(daily)
    if not frames:
        return None
    return pd.concat(frames).sort_index()


def fetch_daily_batch(api, codes: Sequence[str], start: date, end: date,
                      chunk_days: int = 30) -> dict:
    """多檔日線。回傳 {code: DataFrame}；抓不到的 code 不在 dict 裡。"""
    out: dict = {}
    for i, code in enumerate(codes, 1):
        df = fetch_daily(api, code, start, end, chunk_days)
        if df is not None and len(df) >= 80:
            out[code] = df
        if i % 10 == 0:
            log.info("歷史日線進度：%d/%d", i, len(codes))
    log.info("歷史日線：%d/%d 支取得成功（Shioaji）", len(out), len(codes))
    return out


def fetch_minute_bars(api, code: str, day: date) -> list[dict]:
    """取單一交易日的分鐘 K，供模擬層判斷先觸停利或停損。"""
    df = kbars_to_df(_fetch_kbars(api, code, day, day))
    return bars_for_day(df, day)


#: 加權指數在 Shioaji 的代號（Contracts.Indexs.TSE）
TSE_INDEX_CODE = "001"


def resolve_index_contract(api):
    """取得加權指數合約，取不到回 None（絕不拋出）。

    8:30 常見情境：券商已登入但合約尚未下載完成，存取 Contracts 會拋例外。
    大盤資料缺值時 dt_rules 走「不擋、僅註記」的既有路徑，所以回 None 是
    安全的降級，不需要中斷回填。
    """
    if api is None:
        return None
    try:
        tse = api.Contracts.Indexs.TSE
    except Exception as e:
        log.debug("resolve_index_contract: Contracts 不可用: %s", e)
        return None
    if tse is None:
        return None
    try:
        return tse[TSE_INDEX_CODE]
    except Exception:
        pass
    try:
        return tse.get(TSE_INDEX_CODE)
    except Exception:
        return None


def fetch_index_daily(api, start: date, end: date, chunk_days: int = 30):
    """加權指數日線，供 dt_backfill.market_change_as_of 算歷史大盤漲跌幅。"""
    import pandas as pd

    contract = resolve_index_contract(api)
    if contract is None:
        log.warning("取不到加權指數合約，大盤條件將一律視為缺值")
        return None

    frames = []
    for c_start, c_end in date_chunks(start, end, chunk_days):
        try:
            kb = api.kbars(contract,
                           start=c_start.strftime("%Y-%m-%d"),
                           end=c_end.strftime("%Y-%m-%d"))
        except Exception as e:
            log.debug("加權指數 kbars(%s~%s) 失敗: %s", c_start, c_end, e)
            continue
        daily = resample_daily(kbars_to_df(kb))
        if daily is not None and len(daily):
            frames.append(daily)
    return pd.concat(frames).sort_index() if frames else None
