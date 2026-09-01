"""
dt_fees.py — 台股當沖交易成本（單一真相來源）

為什麼要獨立一個模組
--------------------
手續費常數原本散在 dt_paper_trade（_COMMISSION / _TAX_INTRADAY）與
dt_backtest（BacktestParams）兩處。模擬損益層若再寫第三份，三邊算出的
數字就無法互相比較——而「AI 過濾值不值得」這個問題正是靠比較不同策略的
損益來回答的，分母不一致等於沒有答案。

費用規則（現股當沖）
--------------------
  買進手續費 = 買進金額 × 0.1425% × 折扣，最低 20 元
  賣出手續費 = **賣出金額** × 0.1425% × 折扣，最低 20 元
  證交稅     = **賣出金額** × 0.15%（當沖減半，一般為 0.3%），無最低

注意賣方費用按**賣出金額**計收。原 dt_paper_trade._calc_pnl 把三筆都算在
買進金額上，賺錢時低估費用、賠錢時高估。金額不大（成交額的 0.3% 級距），
但沒有理由算錯。

環境變數
--------
  DT_COMMISSION_DISCOUNT  券商折扣，如 0.6 代表 6 折（預設 1.0 不打折）
  DT_MIN_COMMISSION       最低手續費（預設 20）
"""
from __future__ import annotations

import os

#: 單邊手續費率（未打折）
COMMISSION_RATE = 0.001425
#: 當沖證交稅率（賣方，一般 0.3% 的一半）
TAX_RATE_INTRADAY = 0.0015
#: 手續費最低金額
MIN_COMMISSION = 20.0


def _discount() -> float:
    try:
        return float(os.getenv("DT_COMMISSION_DISCOUNT", "1.0"))
    except ValueError:
        return 1.0


def _min_fee() -> float:
    try:
        return float(os.getenv("DT_MIN_COMMISSION", str(MIN_COMMISSION)))
    except ValueError:
        return MIN_COMMISSION


def commission(amount: float, discount: float | None = None,
               min_fee: float | None = None) -> float:
    """單邊手續費。amount 為該邊的成交金額。"""
    if amount <= 0:
        return 0.0
    d = _discount() if discount is None else discount
    m = _min_fee() if min_fee is None else min_fee
    return max(amount * COMMISSION_RATE * d, m)


def tax(exit_amount: float) -> float:
    """當沖證交稅（賣方，無最低金額）。"""
    return max(exit_amount, 0.0) * TAX_RATE_INTRADAY


def net_pnl(capital: float, entry: float,
            exit_price: float) -> tuple[float, float]:
    """扣除所有費用後的損益。

    Returns:
        (pnl, pnl_pct) — pnl 四捨五入到元，pnl_pct 為相對投入本金的比例。

    entry <= 0 代表沒有有效進場價（報價未就緒），回傳 (0.0, 0.0) 而非
    除以零——0 不是價格，是缺值。
    """
    if entry is None or entry <= 0 or capital <= 0:
        return 0.0, 0.0

    exit_amount = capital * (exit_price / entry)
    total_fees = (commission(capital)
                  + commission(exit_amount)
                  + tax(exit_amount))
    pnl = exit_amount - capital - total_fees
    return round(pnl, 0), round(pnl / capital, 4)
