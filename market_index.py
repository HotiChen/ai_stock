"""市場指數工具：取得大盤（加權指數）當日漲跌幅。

資料來源優先序：Shioaji → yfinance → 無。

為什麼是這個順序：yfinance 在這台機器上長期取不到台股資料（`^TWII` 與個股的
`.TW`／`.TWO` 都常回空），而 Shioaji 連線本來就已經為了報價與下單而建立，
拿指數只是順便。2026-08-12 09:05 就是因為只走 yfinance 而讀到 0.0，
導致當日 8 檔當沖候選全部被判定「大盤無方向」而放棄——當天大盤實際漲 0.63%。
"""
from __future__ import annotations

import os
from typing import Optional

import yfinance as yf

from logger import get_logger

# 用專案 logger 而非 logging.getLogger：後者不會寫進 logs/ai_stock.log，
# 2026-08-14 排查「大盤為何取不到」時，昨天特地加的診斷訊息完全找不到，
# 只能靠猜。診斷訊息看不到，等於沒有加。
log = get_logger("market_index")

_TWII = "^TWII"

#: 加權指數在 Shioaji 的合約代碼（api.Contracts.Indexs.TSE）。
_TSE_INDEX_CODE = "001"

_index_api = None


def _build_index_api():
    """實際建立一條 Shioaji 連線。抽出來是為了可測試地注入失敗。"""
    import shioaji as sj

    api = sj.Shioaji(
        simulation=os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"
    )
    api.login(
        os.getenv("SHIOAJI_API_KEY", ""),
        os.getenv("SHIOAJI_SECRET_KEY", ""),
    )
    return api


def _get_index_api():
    """取得（或建立）供查指數用的 Shioaji 連線。

    自己維護 singleton 而不是共用 telegram_bot 的那個，是因為 telegram_bot
    已經 import 本模組，反向 import 會造成循環相依。

    **失敗不快取。** 先前用一個 _index_api_tried 旗標做「試一次就永久放棄」，
    理由是怕盤中反覆重登拖慢流程。代價是：main.py 從 08:25 就常駐，只要開盤前
    某一次登入撞上 Shioaji 尚未就緒（多個 session 同時登入時很常見），
    這個 process 接下來**整天**都拿不到大盤——2026-08-14 全部 25 檔
    action=skip、AI 理由寫「大盤方向未知」，就是這樣來的。
    成功後才快取連線，失敗則下次呼叫再試。
    """
    global _index_api
    if _index_api is not None:
        return _index_api
    try:
        _index_api = _build_index_api()
    except Exception as e:
        log.warning("指數用 Shioaji 連線建立失敗（下次呼叫會重試），改用 yfinance：%s", e)
        _index_api = None
    return _index_api


def get_session():
    """對外的共用 Shioaji session。

    大盤指數與台指期溢貼水都要一條 Shioaji 連線，且應該共用同一條。
    先前 daytrading_report 只有指數走這裡，期貨那邊呼叫
    ``fetch_futures_premium()`` 沒帶 api，於是落到 TWSE MIS 退路——
    而 MIS 不供期貨報價（tse_/otc_ 各種寫法都回 z='-'），
    溢貼水因此從 2026-08-12 起一直是「資料不可用」。

    回傳 ``None`` 代表連不上；呼叫端要照常標示為資料不可用，不可代成 0。
    """
    return _get_index_api()


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
