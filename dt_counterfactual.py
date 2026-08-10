"""
dt_counterfactual.py — LLM 過濾有效性反事實分析

回答的問題：「LLM 過濾（action=long vs skip）到底加不加分？」

資料來源：data/daytrading_review.db 的 dt_prediction_log（daytrading_db.py），
只使用已回填 OHLC + outcome 的記錄。以 daily_open 進場、outcome 對應出場價
（hit_target → target_price、hit_stop → stop_loss、neutral → daily_close），
與 dt_paper_trade.py 的假設完全一致，方便互相對照。

比較三個策略變體：
  A. AI 實際策略  — 只取 action='long'（LLM 真正選出來下單的）
  B. 無 LLM 過濾  — 每日 dt_score 前 N 名（N = 每日 long 檔數的平均，至少 1），
                    不看 action，模擬「如果完全不做 LLM 過濾，只憑分數選股」
  C. 反向檢查    — 只取 action='skip' 且有 OHLC 的（LLM 說不要的，實際表現如何）

A vs B 的差異即「LLM 過濾的邊際貢獻」：B 是「只憑技術分數」的基準線，
A 是「技術分數 + LLM 過濾」，兩者平均報酬的差距就是過濾動作本身創造
（或摧毀）的價值。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_DB = "data/daytrading_review.db"


# ══════════════════════════════════════════════════════════════════════════════
# 資料模型
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class StrategyStats:
    label:     str
    n:         int
    win_rate:  Optional[float]   # None = 無樣本
    avg_ret:   float
    total_ret: float


@dataclass
class CounterfactualReport:
    days:            int
    n_trading_days:  int
    filter_n:        int            # 策略 B 每日取前幾名
    strategy_a:      StrategyStats  # AI 實際策略
    strategy_b:      StrategyStats  # 無 LLM 過濾
    strategy_c:      StrategyStats  # 反向檢查
    avg_ret_delta:   float          # strategy_a.avg_ret - strategy_b.avg_ret
    conclusion:      str


# ══════════════════════════════════════════════════════════════════════════════
# 核心計算
# ══════════════════════════════════════════════════════════════════════════════

def _calc_ret(row: sqlite3.Row) -> Optional[float]:
    """單筆記錄的報酬率（與 dt_paper_trade 相同假設：daily_open 進場，
    outcome 對應出場價）。daily_open 缺漏則回傳 None（不可用）。"""
    entry = row["daily_open"]
    if entry is None or entry == 0:
        return None

    outcome = row["outcome"]

    # untestable：當日振幅過小，這筆預測根本無法被驗證（多半是報價來源異常
    # 時產生的記錄）。不可退回 daily_close——那會把它算成一筆 ~0% 的真實報酬，
    # 用假資料稀釋整份反事實分析的分母。
    if outcome == "untestable":
        return None

    if outcome == "hit_target" and row["target_price"]:
        exit_price = row["target_price"]
    elif outcome == "hit_stop" and row["stop_loss"]:
        exit_price = row["stop_loss"]
    else:
        exit_price = row["daily_close"] if row["daily_close"] is not None else entry

    return (exit_price - entry) / entry


def _stats(label: str, rets: list[float]) -> StrategyStats:
    n = len(rets)
    if n == 0:
        return StrategyStats(label=label, n=0, win_rate=None, avg_ret=0.0, total_ret=0.0)
    wins = sum(1 for r in rets if r > 0)
    return StrategyStats(
        label=label, n=n, win_rate=wins / n,
        avg_ret=sum(rets) / n, total_ret=sum(rets),
    )


def _conclusion(a: StrategyStats, b: StrategyStats) -> tuple[str, float]:
    if a.n == 0 or b.n == 0:
        return (
            "資料不足，無法比較 LLM 過濾效果（AI 實際策略或無過濾基準沒有足夠"
            "已回填記錄）。",
            0.0,
        )

    delta = a.avg_ret - b.avg_ret
    if delta > 1e-9:
        text = (
            f"LLM 過濾使平均報酬提升 {delta * 100:.2f} 個百分點"
            f"（AI 實際 {a.avg_ret * 100:+.2f}% vs 無過濾基準 {b.avg_ret * 100:+.2f}%）。"
        )
    elif delta < -1e-9:
        text = (
            f"LLM 過濾使平均報酬降低 {abs(delta) * 100:.2f} 個百分點"
            f"（AI 實際 {a.avg_ret * 100:+.2f}% vs 無過濾基準 {b.avg_ret * 100:+.2f}%）。"
        )
    else:
        text = "LLM 過濾與無過濾基準的平均報酬幾乎無差異。"
    return text, delta


def analyze(db_path: str = _DEFAULT_DB, days: int = 90) -> CounterfactualReport:
    """比較「AI 實際策略」「無 LLM 過濾」「反向檢查」三組報酬表現。"""
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT * FROM dt_prediction_log
            WHERE outcome IS NOT NULL AND date >= ?
            ORDER BY date ASC, dt_score DESC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    pool = [r for r in rows if r["daily_open"] is not None]
    n_trading_days = len({r["date"] for r in pool})

    # 依日期分組，計算策略 B 的每日取樣數 N
    by_date: dict = {}
    for r in pool:
        by_date.setdefault(r["date"], []).append(r)

    long_counts = [sum(1 for r in rs if r["action"] == "long") for rs in by_date.values()]
    filter_n = max(1, round(sum(long_counts) / len(long_counts))) if long_counts else 1

    group_b_rows = []
    for rs in by_date.values():
        top = sorted(rs, key=lambda r: (-(r["dt_score"] or 0), r["code"]))[:filter_n]
        group_b_rows.extend(top)

    rets_a = [ret for r in pool if r["action"] == "long" if (ret := _calc_ret(r)) is not None]
    rets_b = [ret for r in group_b_rows if (ret := _calc_ret(r)) is not None]
    rets_c = [ret for r in pool if r["action"] == "skip" if (ret := _calc_ret(r)) is not None]

    strategy_a = _stats("AI 實際策略", rets_a)
    strategy_b = _stats("無 LLM 過濾", rets_b)
    strategy_c = _stats("反向檢查", rets_c)

    conclusion, delta = _conclusion(strategy_a, strategy_b)

    return CounterfactualReport(
        days=days, n_trading_days=n_trading_days, filter_n=filter_n,
        strategy_a=strategy_a, strategy_b=strategy_b, strategy_c=strategy_c,
        avg_ret_delta=delta, conclusion=conclusion,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Telegram 格式化
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_pct(v: Optional[float]) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


def format_report(report: CounterfactualReport) -> str:
    lines = [
        f"🔍 <b>LLM 過濾反事實分析</b>（近 {report.days} 天 · {report.n_trading_days} 個交易日）",
        "━━━━━━━━━━━━━━━━",
        f"<b>A. AI 實際策略</b>（action=long）",
        f"　筆數 {report.strategy_a.n}　勝率 {_fmt_pct(report.strategy_a.win_rate)}　"
        f"平均報酬 {report.strategy_a.avg_ret * 100:+.2f}%",
        "",
        f"<b>B. 無 LLM 過濾</b>（每日 dt_score 前 {report.filter_n} 名）",
        f"　筆數 {report.strategy_b.n}　勝率 {_fmt_pct(report.strategy_b.win_rate)}　"
        f"平均報酬 {report.strategy_b.avg_ret * 100:+.2f}%",
        "",
        f"<b>C. 反向檢查</b>（action=skip）",
        f"　筆數 {report.strategy_c.n}　勝率 {_fmt_pct(report.strategy_c.win_rate)}　"
        f"平均報酬 {report.strategy_c.avg_ret * 100:+.2f}%",
        "━━━━━━━━━━━━━━━━",
        report.conclusion,
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="LLM 過濾有效性反事實分析")
    parser.add_argument("--days", type=int, default=90, help="回溯天數")
    parser.add_argument("--db", default=_DEFAULT_DB, help="daytrading_review.db 路徑")
    args = parser.parse_args()

    report = analyze(args.db, days=args.days)

    print(f"近 {report.days} 天（{report.n_trading_days} 個交易日，B 組每日取前 {report.filter_n} 名）\n")
    for s in (report.strategy_a, report.strategy_b, report.strategy_c):
        wr = _fmt_pct(s.win_rate)
        print(
            f"  {s.label:10s}  n={s.n:>3d}  win_rate={wr:>6s}  "
            f"avg_ret={s.avg_ret * 100:>+6.2f}%  total_ret={s.total_ret * 100:>+7.2f}%"
        )
    print()
    print(report.conclusion)
