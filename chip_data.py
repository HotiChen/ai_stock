"""chip_data.py - TWSE 三大法人買賣超資料模組

提供以下功能：
- fetch_institutional_investors: 從 TWSE API 取得單日三大法人資料
- get_continuous_buy_days: 計算外資/投信連續買超天數
- filter_institutional_strong: 篩選外資或投信強力買超的股票
- fetch_margin_trading: 從 TWSE API 取得單日融資融券餘額資料
- filter_margin_risk: 篩選融資大增（高風險）的股票
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable

import requests

TWSE_API_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"

# TWSE data 欄位索引
_IDX_CODE = 0
_IDX_FOREIGN_NET = 6
_IDX_TRUST_NET = 12
_IDX_DEALER_NET = 13
_IDX_TOTAL_NET = 17


def _parse_number(s: str) -> float:
    """去掉逗號後轉 float。"""
    return float(s.replace(",", ""))


def _normalize_date(date: str) -> str:
    """接受 YYYYMMDD 或 YYYY-MM-DD，統一轉成 YYYYMMDD。"""
    if "-" in date:
        return date.replace("-", "")
    return date


def fetch_institutional_investors(date: str) -> dict[str, dict]:
    """取得指定日期的三大法人買賣超資料。

    Args:
        date: 日期字串，接受 "YYYYMMDD" 或 "YYYY-MM-DD" 格式。

    Returns:
        以股票代號為 key 的 dict，每筆包含：
        - foreign_net: 外資買賣超（張）
        - investment_trust_net: 投信買賣超（張）
        - dealer_net: 自營商買賣超（張）
        - total_net: 三大法人合計（張）
        API 異常或網路錯誤時回傳空 dict。
    """
    date_str = _normalize_date(date)
    url = f"{TWSE_API_URL}?response=json&date={date_str}&selectType=ALL"

    try:
        resp = requests.get(url)
        payload = resp.json()
    except Exception:
        return {}

    if payload.get("stat") != "OK":
        return {}

    result: dict[str, dict] = {}
    for row in payload.get("data", []):
        try:
            code = row[_IDX_CODE].strip()
            result[code] = {
                "foreign_net": _parse_number(row[_IDX_FOREIGN_NET]),
                "investment_trust_net": _parse_number(row[_IDX_TRUST_NET]),
                "dealer_net": _parse_number(row[_IDX_DEALER_NET]),
                "total_net": _parse_number(row[_IDX_TOTAL_NET]),
            }
        except (IndexError, ValueError, AttributeError):
            continue

    return result


def _prev_trading_dates(end_date: str, days: int) -> list[str]:
    """從 end_date 往前產生 days 個日期字串（YYYYMMDD），不跳過任何日期。

    實際上 TWSE 非交易日會回傳空資料，由呼叫端處理。
    """
    dt = datetime.strptime(_normalize_date(end_date), "%Y%m%d")
    dates = []
    for i in range(days):
        dates.append((dt - timedelta(days=i)).strftime("%Y%m%d"))
    return dates


def get_continuous_buy_days(
    code: str,
    end_date: str,
    data_fetcher: Callable[[str], dict] | None = None,
    days: int = 5,
) -> dict:
    """計算指定股票最近 N 個交易日的外資與投信連續買超天數。

    Args:
        code: 股票代號。
        end_date: 最近的日期（YYYYMMDD 或 YYYY-MM-DD）。
        data_fetcher: callable(date_str) -> dict，預設用 fetch_institutional_investors。
        days: 往前查詢天數，預設 5。

    Returns:
        dict 包含：
        - foreign_continuous_buy: 外資連續買超天數（int）
        - investment_trust_continuous_buy: 投信連續買超天數（int）
    """
    if data_fetcher is None:
        data_fetcher = fetch_institutional_investors

    dates = _prev_trading_dates(end_date, days)

    foreign_count = 0
    trust_count = 0
    foreign_done = False
    trust_done = False

    for date_str in dates:
        day_data = data_fetcher(date_str)
        stock = day_data.get(code)

        if stock is None:
            # 該日期找不到此股票，計數停止
            foreign_done = True
            trust_done = True
        else:
            if not foreign_done:
                if stock.get("foreign_net", 0) > 0:
                    foreign_count += 1
                else:
                    foreign_done = True

            if not trust_done:
                if stock.get("investment_trust_net", 0) > 0:
                    trust_count += 1
                else:
                    trust_done = True

        if foreign_done and trust_done:
            break

    return {
        "foreign_continuous_buy": foreign_count,
        "investment_trust_continuous_buy": trust_count,
    }


def filter_institutional_strong(
    data: dict,
    min_foreign_net: float = 0,
    min_trust_net: float = 0,
) -> list[str]:
    """篩選外資或投信買超達到門檻的股票代號。

    條件為 OR：foreign_net >= min_foreign_net 或 investment_trust_net >= min_trust_net。

    Args:
        data: fetch_institutional_investors 的回傳值。
        min_foreign_net: 外資買超門檻（張），預設 0。
        min_trust_net: 投信買超門檻（張），預設 0。

    Returns:
        符合條件的股票代號清單。
    """
    if not data:
        return []

    result = []
    for code, info in data.items():
        foreign = info.get("foreign_net", 0)
        trust = info.get("investment_trust_net", 0)
        if foreign >= min_foreign_net or trust >= min_trust_net:
            result.append(code)

    return result


# ---------------------------------------------------------------------------
# 融資融券餘額
# ---------------------------------------------------------------------------

TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"

# 欄位索引
_MARGIN_IDX_CODE = 0
_MARGIN_IDX_MARGIN_PREV = 5    # 融資前日餘額
_MARGIN_IDX_MARGIN_TODAY = 6   # 融資今日餘額
_MARGIN_IDX_SHORT_PREV = 11    # 融券前日餘額
_MARGIN_IDX_SHORT_TODAY = 12   # 融券今日餘額


def fetch_margin_trading(date: str) -> dict[str, dict]:
    """取得指定日期的融資融券餘額資料。

    Args:
        date: 日期字串，接受 "YYYYMMDD" 或 "YYYY-MM-DD" 格式。

    Returns:
        以股票代號為 key 的 dict，每筆包含：
        - margin_balance: 融資今日餘額（張）
        - margin_prev: 融資前日餘額（張）
        - margin_change: 融資增減（今日 - 前日）
        - short_balance: 融券今日餘額（張）
        - short_prev: 融券前日餘額（張）
        - short_change: 融券增減（今日 - 前日）
        API 異常或網路錯誤時回傳空 dict。
    """
    date_str = _normalize_date(date)
    url = f"{TWSE_MARGIN_URL}?response=json&date={date_str}&selectType=ALL"

    try:
        resp = requests.get(url)
        payload = resp.json()
    except Exception:
        return {}

    if payload.get("stat") != "OK":
        return {}

    result: dict[str, dict] = {}
    for row in payload.get("data", []):
        try:
            code = row[_MARGIN_IDX_CODE].strip()
            margin_today = _parse_number(row[_MARGIN_IDX_MARGIN_TODAY])
            margin_prev = _parse_number(row[_MARGIN_IDX_MARGIN_PREV])
            short_today = _parse_number(row[_MARGIN_IDX_SHORT_TODAY])
            short_prev = _parse_number(row[_MARGIN_IDX_SHORT_PREV])
            result[code] = {
                "margin_balance": margin_today,
                "margin_prev": margin_prev,
                "margin_change": margin_today - margin_prev,
                "short_balance": short_today,
                "short_prev": short_prev,
                "short_change": short_today - short_prev,
            }
        except (IndexError, ValueError, AttributeError):
            continue

    return result


def filter_margin_risk(
    data: dict,
    max_margin_change_pct: float = 10.0,
) -> list[str]:
    """篩選融資大增（高風險）的股票代號。

    條件：margin_change / margin_prev * 100 > max_margin_change_pct。
    若 margin_prev == 0 則跳過，避免除以零。

    Args:
        data: fetch_margin_trading 的回傳值。
        max_margin_change_pct: 融資增幅門檻（%），預設 10.0。

    Returns:
        符合高融資風險條件的股票代號清單。
    """
    if not data:
        return []

    result = []
    for code, info in data.items():
        margin_prev = info.get("margin_prev", 0)
        if margin_prev == 0:
            continue
        margin_change = info.get("margin_change", 0)
        change_pct = margin_change / margin_prev * 100
        if change_pct > max_margin_change_pct:
            result.append(code)

    return result


# ---------------------------------------------------------------------------
# 月營收 (MOPS)
# ---------------------------------------------------------------------------

MOPS_REVENUE_URL = "https://mops.twse.com.tw/mops/web/ajax_t05st10_ifrs"


def fetch_monthly_revenue(year: int, month: int) -> dict[str, dict]:
    """取得指定年月的上市公司月營收資料（MOPS）。

    Args:
        year: 西元年（例如 2025）。
        month: 月份（1-12）。

    Returns:
        以股票代號為 key 的 dict，每筆包含：
        - revenue: 當月營收（千元）
        - prev_month_revenue: 上月營收（千元）
        - prev_year_revenue: 去年同月營收（千元）
        - mom_pct: 月增率 %
        - yoy_pct: 年增率 %
        網路錯誤或解析失敗時回傳空 dict。
    """
    roc_year = str(year - 1911)
    month_str = f"{month:02d}"

    try:
        resp = requests.post(
            MOPS_REVENUE_URL,
            data={
                "encodeURIComponent": 1,
                "step": 1,
                "firstin": 1,
                "off": 1,
                "TYPEK": "sii",
                "year": roc_year,
                "month": month_str,
            },
            timeout=15,
        )
        html = resp.text
    except Exception:
        return {}

    result: dict[str, dict] = {}
    # 找出每一個 <tr>...</tr> 區塊
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    for row in rows:
        # 抽出每個 <td> 的文字內容
        cells = re.findall(r"<td[^>]*>\s*([^<]*?)\s*</td>", row)
        if len(cells) < 7:
            continue
        try:
            code = cells[0].strip()
            # 股票代號必須為純數字
            if not code.isdigit():
                continue
            revenue = _parse_number(cells[2])
            prev_month_revenue = _parse_number(cells[3])
            prev_year_revenue = _parse_number(cells[4])
            mom_pct = float(cells[5].replace(",", ""))
            yoy_pct = float(cells[6].replace(",", ""))
            result[code] = {
                "revenue": revenue,
                "prev_month_revenue": prev_month_revenue,
                "prev_year_revenue": prev_year_revenue,
                "mom_pct": mom_pct,
                "yoy_pct": yoy_pct,
            }
        except (ValueError, IndexError, AttributeError):
            continue

    return result


def filter_revenue_growth(
    data: dict,
    min_yoy_pct: float = 10.0,
) -> list[str]:
    """篩選年增率達到門檻的股票代號。

    Args:
        data: fetch_monthly_revenue 的回傳值。
        min_yoy_pct: 年增率門檻（%），預設 10.0。

    Returns:
        yoy_pct >= min_yoy_pct 的股票代號清單。
    """
    if not data:
        return []
    return [
        code for code, info in data.items()
        if info.get("yoy_pct", 0) >= min_yoy_pct
    ]
