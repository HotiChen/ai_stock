"""
dt_simulate.py — 每筆預測的虛擬損益

回答的問題：「如果我對每一筆預測都買固定金額、漲 X% 停利、跌 Y% 停損，
到底賺多少賠多少？」

三個關鍵設計
------------
1. **long 與 skip 用同一套規則**
   skip 的預測沒有 target_price / stop_loss（AI 沒給），若沿用「預測自己的
   目標價」就只有 long 算得出損益，無從回答「AI 說不要的那些，如果我買了
   會怎樣」——而那正是衡量 AI 過濾價值的唯一方法。所以一律用固定百分比。

2. **固定本金，不滾動**
   每筆獨立 capital 元。滾動本金（複利）會讓早期幾筆的運氣主導後面所有
   數字，看不出單筆策略本身的期望值。dt_paper_trade 走的是滾動本金的
   #1 Pick 模式，兩者互補。

3. **逐根掃描分鐘 K**
   只有日 OHLC 時，當日同時觸及停利與停損就無法判斷先後，只能保守假設
   停損先觸發，系統性低估績效。分鐘 K 才知道真正先發生的是哪一個。

已知簡化
--------
  * 以成交金額計算，不處理整張／零股（台股 1 張 = 1000 股）。30,000 元
    通常買不滿一張高價股，實務上要走盤中零股，流動性較差。
  * 假設停利／停損價都能成交在觸價當下，未計滑價。實際掛單可能成交在
    更差的價位，所以本模組的數字是**樂觀上界**。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

import dt_fees

log = logging.getLogger(__name__)

#: 當日振幅低於此百分比視為「報價異常」，不產生模擬交易。
#: 與 daytrading_review._MIN_TESTABLE_RANGE_PCT 同一個理由：券商登入失敗
#: 期間股價會退化成幾乎不動，記成一筆 ~0% 的交易會稀釋統計。
MIN_TESTABLE_RANGE_PCT = 0.5


@dataclass(frozen=True)
class SimResult:
    entry:       float
    exit:        float
    exit_reason: str     # take_profit | stop_loss | force_close | close
    pnl:         float
    pnl_pct:     float


def price_levels(entry: float, take_profit_pct: float,
                 stop_loss_pct: float) -> tuple[float, float]:
    """由進場價與百分比推出停利／停損價。"""
    return (round(entry * (1 + take_profit_pct / 100.0), 2),
            round(entry * (1 - stop_loss_pct / 100.0), 2))


def find_exit(bars: Sequence[dict], tp_price: float, sl_price: float,
              force_close_time: Optional[str] = None):
    """逐根掃描找出場點，回傳 (exit_price, reason) 或 None。

    優先序：
      1. 同一根同時觸及停利與停損 → 保守取停損（無法確認先後）
      2. 先觸及者勝
      3. 到達 force_close_time → 該根收盤價出場
      4. 都沒觸發 → 最後一根收盤價

    force_close_time 在 bars 中找不到對應時間（暫停交易、資料缺漏）時，
    退回最後一根收盤，reason 為 "close"。
    """
    if not bars:
        return None

    for bar in bars:
        hit_tp = tp_price is not None and bar["high"] >= tp_price
        hit_sl = sl_price is not None and bar["low"] <= sl_price

        if hit_sl:
            # 同一根都碰到時也走這裡：保守假設停損先觸發。假設停利先
            # 觸發會讓回測績效系統性偏高。
            return sl_price, "stop_loss"
        if hit_tp:
            return tp_price, "take_profit"

        if force_close_time is not None and bar.get("time") == force_close_time:
            return bar["close"], "force_close"

    return bars[-1]["close"], "close"


def simulate(bars: Sequence[dict], capital: float,
             take_profit_pct: float, stop_loss_pct: float,
             force_close_time: Optional[str] = None) -> Optional[SimResult]:
    """模擬單筆交易。無法模擬時回傳 None（不產生假紀錄）。

    回傳 None 的情形：
      * 沒有分鐘 K
      * 開盤價 <= 0（沒有報價，不是「股價 0 元」）
      * 當日振幅 < MIN_TESTABLE_RANGE_PCT（報價異常）
    """
    if not bars:
        return None

    entry = bars[0].get("open") or 0.0
    if entry <= 0:
        log.debug("simulate：開盤價無效（%s），略過", entry)
        return None

    day_high = max(b["high"] for b in bars)
    day_low = min(b["low"] for b in bars)
    if (day_high - day_low) / entry * 100.0 < MIN_TESTABLE_RANGE_PCT:
        log.debug("simulate：當日振幅過小，視為報價異常，略過")
        return None

    tp_price, sl_price = price_levels(entry, take_profit_pct, stop_loss_pct)
    found = find_exit(bars, tp_price, sl_price, force_close_time)
    if found is None:
        return None
    exit_price, reason = found

    pnl, pnl_pct = dt_fees.net_pnl(capital, entry, exit_price)
    return SimResult(entry=round(entry, 2), exit=round(exit_price, 2),
                     exit_reason=reason, pnl=pnl, pnl_pct=pnl_pct)
