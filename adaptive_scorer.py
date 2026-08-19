"""
adaptive_scorer.py — 每日學習反饋，讓 AI 預測越來越準

在 13:35 DaytradingReview 完成後執行。
分析 dt_prediction_log 的歷史勝率，按評分區間統計，
找出最佳閾值，生成 learning_summary 注入 Notion 報告。

輸出：data/adaptive_config.json
  {
    "version": 5,
    "updated_at": "2026-05-29",
    "optimal_min_score": 6,
    "score_bucket_winrates": {"4-5": 0.31, "6-7": 0.58, "8-10": 0.72},
    "trend_7d": [0.42, 0.45, 0.48, 0.51, 0.55, 0.58, 0.61],
    "learning_summary": "...",
    "total_predictions": 120,
    "days_accumulated": 45
  }

使用方式：
  python3 adaptive_scorer.py            # 更新今日學習資料
  python3 adaptive_scorer.py --days 90  # 使用 90 日歷史資料
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# 預測資料庫的位置由 daytrading_db 定義（表的擁有者）。
# 這裡曾經硬寫 data/daytrading_review.db，而寫入端走 DB_PATH，
# 兩邊指向不同檔案，複盤與學習因此永遠讀到 0 筆。
from daytrading_db import DEFAULT_DB_PATH as _PREDICTION_DB
_REVIEW_DB_PATH = _PREDICTION_DB
_OUTPUT_PATH    = "data/adaptive_config.json"

# 評分分桶邊界
_BUCKETS: list[tuple[int, int, str]] = [
    (4, 5,  "4-5"),
    (6, 7,  "6-7"),
    (8, 10, "8-10"),
]


# ── 資料收集 ──────────────────────────────────────────────────────────────────

def _query_history(db_path: str, days: int) -> list[sqlite3.Row]:
    """回傳近 N 日所有已審查的 long 預測記錄。"""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT dt_score, outcome, was_correct, date
            FROM   dt_prediction_log
            WHERE  action = 'long'
              AND  outcome IS NOT NULL
              AND  date   >= ?
            ORDER  BY date ASC
        """, (cutoff,)).fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.warning("_query_history failed: %s", e)
        return []


def _query_daily_winrates(db_path: str, days: int) -> list[float]:
    """回傳最近 N 個有資料交易日的每日勝率（最舊→最新）。"""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT date,
                   AVG(CAST(was_correct AS REAL)) AS daily_wr
            FROM   dt_prediction_log
            WHERE  action = 'long'
              AND  outcome IS NOT NULL
              AND  date   >= ?
            GROUP  BY date
            HAVING COUNT(*) >= 1
            ORDER  BY date ASC
        """, (cutoff,)).fetchall()
        conn.close()
        return [r["daily_wr"] for r in rows if r["daily_wr"] is not None]
    except Exception as e:
        log.warning("_query_daily_winrates failed: %s", e)
        return []


# ── 計算 ─────────────────────────────────────────────────────────────────────

def _bucket_winrates(rows: list[sqlite3.Row]) -> dict[str, float]:
    """計算各評分區間的勝率。"""
    buckets: dict[str, list[int]] = {label: [] for _, _, label in _BUCKETS}

    for r in rows:
        # was_correct 為 None 代表這筆預測「沒有分出勝負」（neutral：當日既沒
        # 碰到目標也沒碰到停損，或資料不足以驗證）。它不是一次失敗，必須排除
        # 在樣本外。先前寫成 `r["was_correct"] or 0`，Python 會把 None 轉成 0，
        # 等於把每一筆未觸發都記成敗績，系統性低估勝率。
        if r["was_correct"] is None:
            continue

        score = r["dt_score"]
        for lo, hi, label in _BUCKETS:
            if lo <= score <= hi:
                buckets[label].append(int(r["was_correct"]))
                break

    result: dict[str, float] = {}
    for label, vals in buckets.items():
        if vals:
            result[label] = round(sum(vals) / len(vals), 3)
    return result


def _optimal_threshold(bucket_wr: dict[str, float]) -> int:
    """
    根據各區間勝率推算建議最低評分閾值。
    邏輯：找第一個勝率 >= 50% 的區間下界。
    若所有區間都 < 50%，維持原始門檻 4。
    """
    thresholds = {
        "4-5":  4,
        "6-7":  6,
        "8-10": 8,
    }
    for lo, hi, label in _BUCKETS:
        wr = bucket_wr.get(label, 0)
        if wr >= 0.50:
            return thresholds[label]
    return 4


def _moving_avg(values: list[float], window: int = 7) -> list[float]:
    """計算移動平均（用於趨勢）。"""
    result = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        chunk = values[start : i + 1]
        result.append(round(sum(chunk) / len(chunk), 3))
    return result


def _build_summary(
    bucket_wr: dict[str, float],
    optimal: int,
    total: int,
    days_acc: int,
    trend: list[float],
) -> str:
    """生成人類可讀的學習摘要（也會出現在 Notion 報告正文）。"""
    parts: list[str] = []

    # 各區間勝率描述
    for lo, hi, label in _BUCKETS:
        wr = bucket_wr.get(label)
        if wr is not None:
            pct = f"{wr * 100:.1f}%"
            parts.append(f"評分 {label} 的歷史勝率：{pct}")
        else:
            parts.append(f"評分 {label}：資料不足")

    # 趨勢方向
    if len(trend) >= 7:
        recent  = sum(trend[-3:]) / 3
        earlier = sum(trend[-7:-4]) / 3
        delta   = recent - earlier
        if delta > 0.03:
            trend_desc = f"近期勝率呈上升趨勢（近3日均 {recent*100:.1f}%）"
        elif delta < -0.03:
            trend_desc = f"近期勝率呈下降趨勢（近3日均 {recent*100:.1f}%）"
        else:
            trend_desc = f"近期勝率平穩（近3日均 {recent*100:.1f}%）"
        parts.append(trend_desc)

    # 建議閾值
    if optimal >= 6:
        parts.append(f"建議：只操作評分 ≥ {optimal} 的標的（低分區間表現不佳）")
    else:
        parts.append("資料量仍少，維持現行評分門檻（≥4），持續累積學習資料")

    parts.append(f"累積分析：{total} 筆預測 / {days_acc} 個交易日")
    return "。".join(parts) + "。"


# ── 主入口 ────────────────────────────────────────────────────────────────────

def update_adaptive_config(
    db_path:  str = _REVIEW_DB_PATH,
    out_path: str = _OUTPUT_PATH,
    days:     int = 90,
) -> dict:
    """
    計算學習指標，更新 adaptive_config.json，回傳新 config dict。
    即使 DB 不存在或資料為空，仍會寫入版本號，不會拋出例外。
    """
    # 讀取現有版本號
    existing: dict = {}
    try:
        p = Path(out_path)
        if p.exists():
            existing = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass

    version = (existing.get("version") or 0) + 1

    # 查詢歷史資料
    rows         = _query_history(db_path, days=days)
    daily_rates  = _query_daily_winrates(db_path, days=days)
    trend_7d     = _moving_avg(daily_rates, window=7)[-14:]   # 最近 14 個移動平均點

    total        = len(rows)
    days_unique  = len(set(r["date"] for r in rows))

    bucket_wr    = _bucket_winrates(rows)
    optimal      = _optimal_threshold(bucket_wr)
    summary      = _build_summary(bucket_wr, optimal, total, days_unique, daily_rates)

    config: dict = {
        "version":               version,
        "updated_at":            date.today().isoformat(),
        "optimal_min_score":     optimal,
        "score_bucket_winrates": bucket_wr,
        "trend_7d":              trend_7d,
        "learning_summary":      summary,
        "total_predictions":     total,
        "days_accumulated":      days_unique,
    }

    # 寫入 JSON
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(
            "adaptive_config updated: v%d, %d predictions, optimal_min=%d",
            version, total, optimal,
        )
    except Exception as e:
        log.error("failed to write adaptive_config: %s", e)

    return config


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="更新 AI 學習配置")
    parser.add_argument("--days",   type=int, default=90, help="使用幾天的歷史資料（預設 90）")
    parser.add_argument("--db",     default=_REVIEW_DB_PATH, help="daytrading_review.db 路徑")
    parser.add_argument("--out",    default=_OUTPUT_PATH,    help="輸出 adaptive_config.json 路徑")
    args = parser.parse_args()

    cfg = update_adaptive_config(db_path=args.db, out_path=args.out, days=args.days)

    print(f"\n✅ 學習版本 v{cfg['version']} 已更新")
    print(f"   累積預測：{cfg['total_predictions']} 筆 / {cfg['days_accumulated']} 個交易日")
    print(f"   建議最低評分閾值：≥ {cfg['optimal_min_score']}")
    print()
    print("各評分區間勝率：")
    for label, wr in cfg.get("score_bucket_winrates", {}).items():
        bar = "█" * int(wr * 20) + "░" * (20 - int(wr * 20))
        print(f"  評分 {label}：{bar}  {wr*100:.1f}%")
    print()
    print("學習摘要：")
    print(f"  {cfg['learning_summary']}")
