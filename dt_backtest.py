"""
dt_backtest.py — 當沖出場規則歷史回測

目的：把「進場 → 四規則出場」在歷史分鐘 / 5 分 K 上重放，估計不同參數組合
（停損 / 停利 / 移動停利）的期望值，取代「模擬倉一天一筆」的緩慢驗證
（dt_paper_trade.py 一天才能累積一筆樣本，回測可一次跑數十天 × 數檔）。

出場規則與優先序，完全對齊 daytrading_monitor.check_trailing_stop：
  1. 停損：跌幅 >= stop_loss_pct
  2. 天花板停利：漲幅 >= take_profit_pct
  3. 強制平倉：時間 >= force_close_time
  4. 移動停利：峰值漲幅 >= trailing_start_pct 且峰值回落 >= trailing_gap_pct

成本模型對齊 dt_paper_trade._calc_pnl：買/賣手續費各一次 + 當沖交易稅（賣方），
以及出場滑價（不利方向，賣出偏低）。
"""
from __future__ import annotations

import dataclasses
import itertools
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 資料模型
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class BacktestParams:
    """回測參數。百分比欄位皆以「正數幅度」表示（如 3.0 = 3%），與
    daytrading_config.DaytradingConfig 的慣例一致。"""

    stop_loss_pct:       float
    take_profit_pct:     float
    trailing_start_pct:  float
    trailing_gap_pct:    float
    force_close_time:    str   = "13:00"
    commission:          float = 0.001425   # 單邊手續費（比例）
    tax:                  float = 0.0015     # 當沖交易稅（賣方，比例）
    slippage_pct:         float = 0.05       # 出場滑價（%，不利方向）


@dataclass
class TradeResult:
    """單一交易日「進場 → 出場」重放結果。"""

    exit_price:   float
    exit_time:    str
    exit_reason:  str    # "stop_loss" | "take_profit_ceiling" | "force_close" |
                          # "trailing_stop" | "eod" | "no_data"
    gross_ret:    float  # 出場價相對進場價的毛報酬率（未扣費用）
    net_ret:      float  # 扣手續費 + 交易稅後的淨報酬率


@dataclass
class BacktestReport:
    """多筆交易彙總統計。"""

    n:               int
    win_rate:        float
    avg_win:         float
    avg_loss:        float
    expectancy_net:  float
    max_drawdown:    float
    exit_reason_dist: dict
    trades:          list = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# 核心：單日重放
# ══════════════════════════════════════════════════════════════════════════════

def simulate_day(bars: list[dict], entry_price: float, params: BacktestParams) -> TradeResult:
    """在單一交易日的 bars 序列上，重放四規則出場邏輯。

    參數
    ----
    bars        : list[dict{time, open, high, low, close}]，time 為 "HH:MM"，
                  依時間先後排序。
    entry_price : 當日進場價。
    params      : BacktestParams。

    規則優先序（逐 bar 檢查，與 check_trailing_stop 一致）：
      1. 停損：本 bar 低點跌幅 >= stop_loss_pct
      2. 天花板停利：本 bar 高點漲幅 >= take_profit_pct
      3. 強制平倉：本 bar 時間 >= force_close_time（未觸價則以該 bar 收盤出場）
      4. 移動停利：「前一 bar 為止」已確立的峰值漲幅 >= trailing_start_pct，
         且本 bar 低點跌破「峰值 × (1 - trailing_gap_pct%)」

    保守判定：
      - 同一 bar 同時觸及規則 1、2，視為停損（規則 1 優先，不檢查規則 2）。
      - 峰值只在每根 bar 檢查完畢後才用該 bar 高點更新，避免同一根 bar
        用自己剛創的新高反推觸發移動停利（look-ahead）。
      - 出場價一律以「觸發價」（停損線 / 停利線 / 移動停利線 / 強平 bar 收盤）
        計算，再套用不利方向（賣出偏低）滑價成交。
    """
    if not bars:
        return TradeResult(
            exit_price=entry_price, exit_time="", exit_reason="no_data",
            gross_ret=0.0, net_ret=0.0,
        )

    fc_h, fc_m = map(int, params.force_close_time.split(":"))
    peak = entry_price

    def _fill(trigger_price: float) -> float:
        """觸價 → 套用不利方向滑價後的實際成交價。"""
        return trigger_price * (1 - params.slippage_pct / 100)

    def _finalize(trigger_price: float, exit_time: str, reason: str) -> TradeResult:
        exit_price = _fill(trigger_price)
        gross_ret = (exit_price - entry_price) / entry_price
        net_ret = gross_ret - 2 * params.commission - params.tax
        return TradeResult(
            exit_price=exit_price, exit_time=exit_time, exit_reason=reason,
            gross_ret=gross_ret, net_ret=net_ret,
        )

    for bar in bars:
        t = bar["time"]
        low = bar["low"]
        high = bar["high"]
        close = bar["close"]

        low_gain_pct = (low - entry_price) / entry_price * 100
        high_gain_pct = (high - entry_price) / entry_price * 100

        # 1. 停損（優先於天花板停利）
        if low_gain_pct <= -params.stop_loss_pct:
            trigger = entry_price * (1 - params.stop_loss_pct / 100)
            return _finalize(trigger, t, "stop_loss")

        # 2. 天花板停利
        if high_gain_pct >= params.take_profit_pct:
            trigger = entry_price * (1 + params.take_profit_pct / 100)
            return _finalize(trigger, t, "take_profit_ceiling")

        # 3. 強制平倉時間
        bar_h, bar_m = map(int, t.split(":"))
        if (bar_h, bar_m) >= (fc_h, fc_m):
            return _finalize(close, t, "force_close")

        # 4. 移動停利（用前一 bar 為止的峰值判定）
        peak_gain_pct = (peak - entry_price) / entry_price * 100
        if peak_gain_pct >= params.trailing_start_pct:
            trailing_stop_price = peak * (1 - params.trailing_gap_pct / 100)
            if low <= trailing_stop_price:
                return _finalize(trailing_stop_price, t, "trailing_stop")

        # 更新峰值供下一根 bar 使用
        if high > peak:
            peak = high

    # 所有 bar 都未觸發任何規則（資料在強平時間之前就結束）：以最後一根收盤出場
    last = bars[-1]
    return _finalize(last["close"], last["time"], "eod")


# ══════════════════════════════════════════════════════════════════════════════
# 彙總 / 參數掃描
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest(
    daily_bars_by_stock: dict,
    entries: list[dict],
    params: BacktestParams,
) -> BacktestReport:
    """對多筆「進場」重放出場規則，彙總統計。

    daily_bars_by_stock : {(date, code): [bar, ...]}
    entries              : [{"date":..., "code":..., "entry_price":...}, ...]
    """
    trades: list[TradeResult] = []
    exit_reason_dist: dict = {}

    # 依日期排序，確保逐筆複利的權益曲線時間順序正確
    sorted_entries = sorted(entries, key=lambda e: (e["date"], e["code"]))

    for e in sorted_entries:
        key = (e["date"], e["code"])
        bars = daily_bars_by_stock.get(key)
        if not bars:
            log.debug("run_backtest: 找不到 %s 的 K 線資料，略過", key)
            continue
        result = simulate_day(bars, e["entry_price"], params)
        trades.append(result)
        exit_reason_dist[result.exit_reason] = exit_reason_dist.get(result.exit_reason, 0) + 1

    n = len(trades)
    if n == 0:
        return BacktestReport(
            n=0, win_rate=0.0, avg_win=0.0, avg_loss=0.0,
            expectancy_net=0.0, max_drawdown=0.0, exit_reason_dist={}, trades=[],
        )

    wins = [t.net_ret for t in trades if t.net_ret > 0]
    losses = [t.net_ret for t in trades if t.net_ret <= 0]
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy_net = sum(t.net_ret for t in trades) / n

    # 逐筆複利計算最大回撤
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for t in trades:
        equity *= (1 + t.net_ret)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd

    return BacktestReport(
        n=n, win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss,
        expectancy_net=expectancy_net, max_drawdown=max_dd,
        exit_reason_dist=exit_reason_dist, trades=trades,
    )


def sweep(
    daily_bars_by_stock: dict,
    entries: list[dict],
    grid: dict,
) -> list[tuple]:
    """參數網格掃描，依 expectancy_net 由高至低排序。

    grid 只需列出要掃描的欄位（如 {"stop_loss_pct": [2.0, 3.0]}），
    其餘欄位使用內建基準值（3 / 9 / 3 / 2）。
    """
    base = BacktestParams(
        stop_loss_pct=3.0, take_profit_pct=9.0,
        trailing_start_pct=3.0, trailing_gap_pct=2.0,
    )
    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys])) if keys else [()]

    results = []
    for combo in combos:
        overrides = dict(zip(keys, combo))
        params = dataclasses.replace(base, **overrides)
        report = run_backtest(daily_bars_by_stock, entries, params)
        results.append((params, report))

    results.sort(key=lambda pr: pr[1].expectancy_net, reverse=True)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 歷史資料抓取（CLI 用，測試不呼叫）
# ══════════════════════════════════════════════════════════════════════════════

def fetch_bars(code: str, days: int = 55, api=None) -> Optional[dict]:
    """抓分鐘 K，回傳 {(date, code): [bar, ...]}。資料來源 Shioaji kbars。

    原本走 yfinance 5 分 K，已移除。兩個理由：
      1. Yahoo 對 5m 級資料只保留約 60 天，days 超過就被靜默截斷——回測
         範圍縮水了卻不會有任何提示。
      2. Shioaji 給的是 1 分 K，粒度更細，判斷「先觸停利還是先觸停損」
         更準確；而且與正式交易用的是同一份資料。

    查無資料一律回傳 None（呼叫方需自行處理）。
    """
    from datetime import date as _date, timedelta as _timedelta

    import shioaji_history as sh

    if api is None:
        import shioaji_session
        api = shioaji_session.get_api()
    if api is None:
        log.warning("fetch_bars(%s)：無 Shioaji 連線", code)
        return None

    end = _date.today()
    start = end - _timedelta(days=days)
    minute_df = None
    try:
        frames = []
        for c_start, c_end in sh.date_chunks(start, end, chunk_days=30):
            df = sh.kbars_to_df(sh._fetch_kbars(api, code, c_start, c_end))
            if df is not None and len(df):
                frames.append(df)
        if frames:
            import pandas as pd
            minute_df = pd.concat(frames).sort_index()
    except Exception as e:
        log.warning("fetch_bars(%s) failed: %s", code, e)
        return None

    if minute_df is None or len(minute_df) == 0:
        return None

    result: dict = {}
    for ts, row in minute_df.iterrows():
        key = (ts.strftime("%Y-%m-%d"), code)
        result.setdefault(key, []).append({
            "time":  ts.strftime("%H:%M"),
            "open":  float(row["Open"]),
            "high":  float(row["High"]),
            "low":   float(row["Low"]),
            "close": float(row["Close"]),
        })
    return result or None


#: 舊名稱別名（名字裡的 yf 已不符實際來源）
fetch_bars_yf = fetch_bars


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="當沖出場規則歷史回測")
    parser.add_argument("--codes", required=True, help="股票代碼，逗號分隔，如 2330,2454")
    parser.add_argument("--days", type=int, default=55, help="回溯天數")
    parser.add_argument("--sweep", action="store_true", help="執行參數網格掃描")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    daily_bars_by_stock: dict = {}
    for code in codes:
        fetched = fetch_bars(code, days=args.days)
        if fetched:
            daily_bars_by_stock.update(fetched)
        else:
            print(f"⚠ {code} 抓取失敗或無資料，略過。")

    if not daily_bars_by_stock:
        print("找不到任何歷史 K 線資料，請檢查股票代碼或網路連線後再試。")
        raise SystemExit(0)

    entries = [
        {"date": d, "code": c, "entry_price": bars[0]["open"]}
        for (d, c), bars in daily_bars_by_stock.items()
        if bars
    ]
    print(f"共 {len(entries)} 筆進場樣本（{len(codes)} 檔 × 最多 {args.days} 天）")

    if args.sweep:
        grid = {
            "stop_loss_pct":      [2.0, 3.0, 4.0],
            "take_profit_pct":    [6.0, 9.0, 12.0],
            "trailing_start_pct": [2.0, 3.0],
            "trailing_gap_pct":   [1.0, 2.0],
        }
        results = sweep(daily_bars_by_stock, entries, grid)
        print("\n排名  停損%  停利%  移停啟動%  移停間距%   n  勝率   期望值(net)  最大回撤")
        for i, (p, r) in enumerate(results[:15], 1):
            print(
                f"{i:>3}   {p.stop_loss_pct:>4.1f}  {p.take_profit_pct:>4.1f}   "
                f"{p.trailing_start_pct:>6.1f}   {p.trailing_gap_pct:>6.1f}  "
                f"{r.n:>3}  {r.win_rate*100:>5.1f}%  {r.expectancy_net*100:>+7.3f}%   "
                f"{r.max_drawdown*100:>5.2f}%"
            )
    else:
        params = BacktestParams(
            stop_loss_pct=3.0, take_profit_pct=9.0,
            trailing_start_pct=3.0, trailing_gap_pct=2.0,
        )
        report = run_backtest(daily_bars_by_stock, entries, params)
        print(f"\nn={report.n}  勝率={report.win_rate*100:.1f}%  "
              f"平均獲利={report.avg_win*100:+.2f}%  平均虧損={report.avg_loss*100:+.2f}%")
        print(f"期望值(net)={report.expectancy_net*100:+.3f}%  最大回撤={report.max_drawdown*100:.2f}%")
        print(f"出場原因分佈：{report.exit_reason_dist}")
