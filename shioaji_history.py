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

    另附 "time"（HH:MM）供模擬層判斷強制平倉時點——台股 13:30 收盤，但系統
    設定的強平時間更早（預設 13:15），沒有時間戳就只能用收盤價結算，會高估
    那些「尾盤才拉回來」的部位。
    """
    if minute_df is None or len(minute_df) == 0:
        return []
    sel = minute_df[[ts.date() == day for ts in minute_df.index]]
    return [
        {"time": ts.strftime("%H:%M"),
         "open": float(r["Open"]), "high": float(r["High"]),
         "low": float(r["Low"]), "close": float(r["Close"])}
        for ts, r in sel.iterrows()
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

#: _call_with_timeout 逾時的哨兵值（與「呼叫成功但回 None」區分）
TIMEOUT = object()

#: 單次 kbars 呼叫的逾時秒數。Shioaji 的 C++ 層自己每 30 秒重試一次且不會
#: 放棄，沒有外層逾時就是永遠卡住——2026-09-02 的回填就是這樣卡死的。
CALL_TIMEOUT_SEC = 45


def _call_with_timeout(fn, timeout: float = CALL_TIMEOUT_SEC):
    """在背景執行緒呼叫 fn，逾時回傳 TIMEOUT，例外回傳 None。

    底層的 C++ 呼叫無法真的取消，逾時後那條執行緒會繼續卡著；但只要在
    連續失敗達門檻時中止整批，洩漏的執行緒數量是有限的，遠好過整個程序
    卡死到只能 Ctrl-C（而且已抓到的資料全部丟掉）。
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as _FTimeout

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except _FTimeout:
            log.warning("Shioaji 呼叫逾時（%.0f 秒），放棄等待", timeout)
            return TIMEOUT
        except Exception as e:
            log.debug("Shioaji 呼叫失敗: %s", e)
            return None
    finally:
        ex.shutdown(wait=False)


def _fetch_kbars(api, code: str, start: date, end: date):
    """單次 kbars 呼叫，失敗回 None（不對外拋出，一支失敗不該中斷整批）。"""
    try:
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            log.debug("找不到合約：%s", code)
            return None
    except Exception as e:
        log.debug("取合約失敗 %s: %s", code, e)
        return None

    out = _call_with_timeout(
        lambda: api.kbars(contract,
                          start=start.strftime("%Y-%m-%d"),
                          end=end.strftime("%Y-%m-%d"))
    )
    if out is TIMEOUT:
        return None
    return out


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
                      chunk_days: int = 30,
                      abort_after_failures: int = 5,
                      reconnect=None,
                      on_result=None) -> dict:
    """多檔日線。回傳 {code: DataFrame}；抓不到的 code 不在 dict 裡。

    韌性設計（2026-09-02 正式環境事故的修補）：
      * 連續失敗達 abort_after_failures 就**中止並回傳已取得的部分**。
        原本的行為是一路跑到底，token 過期後每支都卡 45 秒，81 支要卡一小時，
        而且中止時已抓到的 40 檔全部丟掉。
      * 中止前先呼叫 reconnect()（若有提供）試一次重新登入——token 過期是
        可復原的。重連成功則失敗計數歸零繼續。
      * on_result(code, df) 讓呼叫端可以邊抓邊存，程序中斷也不會全部白做。

    只有**連續**失敗才計數：零星的個股查無資料（下市、暫停交易）不代表連線壞了。
    """
    out: dict = {}
    streak = 0
    reconnected = False

    for i, code in enumerate(codes, 1):
        df = fetch_daily(api, code, start, end, chunk_days)

        if df is not None and len(df) >= 80:
            out[code] = df
            streak = 0
            if on_result is not None:
                try:
                    on_result(code, df)
                except Exception as e:
                    log.warning("on_result(%s) 失敗（不影響抓取）: %s", code, e)
        else:
            streak += 1
            if streak >= abort_after_failures:
                if reconnect is not None and not reconnected:
                    log.warning("連續 %d 次取不到資料，嘗試重新登入…", streak)
                    new_api = reconnect()
                    reconnected = True
                    if new_api is not None:
                        api = new_api
                        streak = 0
                        continue
                log.error(
                    "連續 %d 次取不到歷史資料，中止（已取得 %d/%d 檔）。"
                    "常見原因：Shioaji 歷史資料用量已達上限，或連線異常。",
                    streak, len(out), len(codes),
                )
                break

        if i % 10 == 0:
            log.info("歷史日線進度：%d/%d（成功 %d）", i, len(codes), len(out))

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


# ══════════════════════════════════════════════════════════════════════════════
# 用量與快取
# ══════════════════════════════════════════════════════════════════════════════

def usage_report(api) -> Optional[dict]:
    """Shioaji 歷史資料用量。取不到回 None（不同 SDK 版本欄位不一）。

    為什麼重要：Shioaji 對歷史資料有每日流量上限。回填 81 檔 × 8 個月的
    分鐘 K 是數百萬根 K 線，很容易撞上限；撞到之後的徵狀是
    「Token is expired」加上無止盡的「Not ready」——完全看不出真正原因。
    抓之前先問一次，把剩餘量印出來，才不會又debug 半天。
    """
    if api is None or not hasattr(api, "usage"):
        return None
    try:
        u = api.usage()
    except Exception as e:
        log.debug("usage() 失敗: %s", e)
        return None
    if u is None:
        return None

    def _get(name):
        if isinstance(u, dict):
            return u.get(name)
        return getattr(u, name, None)

    used = _get("bytes") or 0
    limit = _get("limit_bytes") or 0
    remaining = _get("remaining_bytes")
    if remaining is None:
        remaining = max(limit - used, 0)
    mb = 1024 * 1024
    return {
        "connections":   _get("connections"),
        "used_mb":       round(used / mb, 2),
        "limit_mb":      round(limit / mb, 2),
        "remaining_mb":  round(remaining / mb, 2),
        "remaining_pct": round(remaining / limit * 100, 1) if limit else None,
    }


def _cache_path(cache_dir: str, code: str):
    from pathlib import Path
    p = Path(cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{code}.pkl"


def cache_save(cache_dir: str, code: str, df) -> None:
    """把抓到的日線存到本機，供下次續跑。

    Shioaji 的歷史資料有流量上限，重抓是有成本的；程序中斷（逾時、
    Ctrl-C、token 過期）時已抓到的部分必須留下來。
    """
    try:
        df.to_pickle(_cache_path(cache_dir, code))
    except Exception as e:
        log.debug("cache_save(%s) 失敗: %s", code, e)


def cache_load(cache_dir: str, code: str):
    """讀取快取的日線，沒有或壞掉回 None（壞掉就重抓，不得讓整批爆掉）。"""
    import pandas as pd

    path = _cache_path(cache_dir, code)
    if not path.exists():
        return None
    try:
        return pd.read_pickle(path)
    except Exception as e:
        log.warning("快取檔損壞，將重新抓取 %s: %s", code, e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 增量快取：只抓缺的那幾天
# ══════════════════════════════════════════════════════════════════════════════
#
# 為什麼需要：8:30 選股對每支候選呼叫 fetch_indicators，它抓 150 天的分鐘 K
# 來算日線指標。50 支候選 = 7,500 個股票日的分鐘資料，而且**每天重抓一次**
# ——其中 149 天昨天就抓過了，只有最後一根是新的。
#
# Shioaji 歷史資料每日上限 500 MB。2026-09-02 就是這樣被燒光的，之後 8:30
# 選股完全拿不到指標。穩態下改成每天每支只抓 1 天，用量降到約 1/150。

def merge_daily(old, new):
    """合併兩份日線，重疊日期**保留新的那份**。

    保留新的原因：舊快取可能是盤中抓的，當天的收盤價還沒定案。
    """
    if old is None:
        return new
    if new is None:
        return old
    import pandas as pd

    merged = pd.concat([old, new])
    # keep="last" → 後面的（new）勝出
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()


def missing_range(cached, start: date, end: date):
    """回傳還需要抓的 (start, end)，或 None 代表快取已足夠。

    只處理「尾端缺口」這一種情形：快取涵蓋 start 之後、但還沒到 end。
    若快取起點晚於 start（前面缺一段），回傳完整區間重抓——kbars 無法只
    抓中間的一段，而缺頭會讓移動平均等指標算錯。
    """
    if cached is None or len(cached) == 0:
        return start, end

    first = cached.index[0].date()
    last = cached.index[-1].date()

    if first > start:
        return start, end          # 前面缺一段，只能整段重抓
    if last >= end:
        return None                # 已涵蓋
    # 從快取最後一天開始（含），避免當天資料是盤中抓的而不完整
    return last, end


def fetch_daily_cached(api, code: str, start: date, end: date,
                       cache_dir: str = "data/history_cache",
                       chunk_days: int = 30):
    """帶增量快取的日線抓取。

    流程：讀快取 → 算缺口 → 只抓缺口 → 合併 → 存回。

    抓取失敗時**回傳快取裡的舊資料**而非 None：額度用完或連線異常時，
    有點舊的指標仍遠好過完全沒有指標（2026-09-02 的 8:30 選股就是因為
    額度歸零而一支都算不出來）。
    """
    cached = cache_load(cache_dir, code)
    gap = missing_range(cached, start, end)

    if gap is None:
        return cached

    fetched = fetch_daily(api, code, gap[0], gap[1], chunk_days)
    if fetched is None:
        if cached is not None:
            log.warning("抓取失敗，改用快取的舊日線 %s（最後 %s）",
                        code, cached.index[-1].date())
        return cached

    merged = merge_daily(cached, fetched)
    cache_save(cache_dir, code, merged)
    return merged


# ══════════════════════════════════════════════════════════════════════════════
# 增量抓取：只補快取缺的那一段
# ══════════════════════════════════════════════════════════════════════════════
#
# 為什麼需要：8:30 選股對 50 支候選各呼叫一次 fetch_indicators，它抓 150 天的
# 分鐘 K 來算日線指標——每支約 12 MB，50 支就 600 MB，超過 Shioaji 每日
# 500 MB 上限。而其中 149 天的資料昨天就抓過了，只有最後一根是新的。
#
# 2026-09-02 的額度就是這樣被燒光的（一次回填 506/500 MB），之後 8:30 選股
# 完全拿不到指標。改成增量之後，穩態下每支每天只抓 1 天，用量降到 1/150。


def merge_daily(old, new):
    """合併兩份日線，重疊日期以 new 為準（舊快取可能是盤中抓的，收盤價未定）。"""
    import pandas as pd

    if old is None:
        return new
    if new is None:
        return old
    merged = pd.concat([old, new])
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()


def missing_range(cached, start: date, end: date):
    """回傳還需要抓的 (start, end)，快取已足夠則回 None。

    只處理「尾端缺一段」這個常見情形。若快取的起點晚於需求起點（前面缺一段），
    整段重抓——kbars 無法只抓中間，而缺頭會讓 MA/RSI 這類需要暖機期的指標算錯。
    """
    if cached is None or len(cached) == 0:
        return start, end

    idx = [ts.date() if hasattr(ts, "date") else ts for ts in cached.index]
    first, last = min(idx), max(idx)

    if first > start:
        return start, end          # 前面缺，整段重抓
    if last >= end:
        return None                # 已涵蓋，完全不用抓
    return last, end               # 只補尾巴（含 last 當天，避免當日資料不完整）


def fetch_daily_cached(api, code: str, start: date, end: date,
                       cache_dir: str = "data/history_cache",
                       chunk_days: int = 30):
    """帶增量快取的日線抓取。

    抓取失敗時**回傳快取裡的舊資料**而非 None——額度用完或連線異常時，
    有點舊的指標仍遠好過完全沒有（2026-09-02 的 8:30 就是因為完全拿不到
    而一支候選都產不出來）。
    """
    cached = cache_load(cache_dir, code)
    gap = missing_range(cached, start, end)

    if gap is None:
        return cached

    fresh = fetch_daily(api, code, gap[0], gap[1], chunk_days)
    if fresh is None:
        if cached is not None and len(cached):
            log.warning("%s 增量抓取失敗，改用快取（最後日期 %s）",
                        code, max(cached.index).date())
            return cached
        return None

    merged = merge_daily(cached, fresh)
    cache_save(cache_dir, code, merged)
    return merged
