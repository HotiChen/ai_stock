"""
seed_demo_data.py — 用 Shioaji 真實歷史日 K 填充 daytrading_review.db

流程：
  1. 登入 Shioaji（需 .env 設好 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY）
  2. 下載候選股過去 N+60 天日 K（多 60 天供指標計算）
  3. 每個交易日用前一天的 MA/RSI/動能評分，選出當天 top pick
  4. 依當天 Open/High/Low/Close 判斷 hit_target / hit_stop / neutral
  5. 寫入 daytrading_review.db
  6. 對所有歷史日跑 dt_paper_trade，輸出報告

用法：
    python3 seed_demo_data.py              # 預設 30 天
    python3 seed_demo_data.py --days 60
    python3 seed_demo_data.py --reset      # 清除重來

前置：
    .env 需包含：
        SHIOAJI_API_KEY=your_key
        SHIOAJI_SECRET_KEY=your_secret
        SHIOAJI_SIMULATION=true           # simulation 模式不影響歷史 kbars
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# ── 候選股清單 ─────────────────────────────────────────────────────────────────
STOCKS = [
    ("2330", "台積電"),
    ("2454", "聯發科"),
    ("2317", "鴻海"),
    ("2382", "廣達"),
    ("2308", "台達電"),
    ("2303", "聯電"),
    ("2881", "富邦金"),
    ("2882", "國泰金"),
    ("2412", "中華電"),
    ("3711", "日月光投控"),
    ("2345", "智邦"),
    ("6505", "台塑化"),
    ("1301", "台塑"),
    ("2002", "中鋼"),
    ("2379", "瑞昱"),
]

_REVIEW_DB = "data/daytrading_review.db"
_PAPER_DB  = "data/paper_trading.db"
_TP_PCT    = 0.03
_SL_PCT    = 0.03


# ── 載入 .env ─────────────────────────────────────────────────────────────────

def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env", override=False)
        return
    except ImportError:
        pass
    # fallback: 手動解析（處理有無引號）
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


# ── Shioaji 登入 ──────────────────────────────────────────────────────────────

def _login_shioaji():
    import shioaji as sj

    api_key    = os.getenv("SHIOAJI_API_KEY", "")
    secret_key = os.getenv("SHIOAJI_SECRET_KEY", "")
    simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"

    if not api_key or not secret_key:
        log.error("缺少 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY，請設定 .env")
        sys.exit(1)

    log.info("登入 Shioaji（simulation=%s）...", simulation)
    api = sj.Shioaji(simulation=simulation)
    api.login(api_key=api_key, secret_key=secret_key, fetch_contract=True)
    log.info("Shioaji 登入成功")
    return api


# ── 資料庫 ────────────────────────────────────────────────────────────────────

def _init_review_db(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dt_prediction_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT NOT NULL,
            code         TEXT NOT NULL,
            name         TEXT,
            action       TEXT DEFAULT 'long',
            dt_score     INTEGER,
            outcome      TEXT,
            daily_open   REAL,
            daily_high   REAL,
            daily_low    REAL,
            daily_close  REAL,
            target_price REAL,
            stop_loss    REAL,
            ai_summary   TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(date, code)
        )
    """)
    conn.commit()
    return conn


# ── 指標計算 ──────────────────────────────────────────────────────────────────

def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_g  = sum(gains)  / period
    avg_l  = sum(losses) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 2)


def _score_stock(closes: list[float], today_open: float) -> int:
    """
    前一天收盤後可知道的技術分：
      - MA5/MA20 黃金交叉 +2
      - RSI 40-60（健康動能）+1
      - 近 5 天收盤漲勢 +1/+2
      - 開盤相對前收漲 +1
    總分 4-10
    """
    if len(closes) < 20:
        return 5

    ma5  = sum(closes[-5:])  / 5
    ma20 = sum(closes[-20:]) / 20
    rsi  = _rsi(closes)
    mom5 = (closes[-1] - closes[-6]) / closes[-6] if closes[-6] > 0 else 0
    gap  = (today_open - closes[-1]) / closes[-1] if closes[-1] > 0 else 0

    score = 4
    if ma5 > ma20:                   score += 2   # 黃金交叉
    if 40 <= rsi <= 65:              score += 1   # 健康 RSI
    if mom5 > 0.01:                  score += 1   # 5 日動能
    if mom5 > 0.03:                  score += 1   # 強動能
    if 0 < gap < 0.02:               score += 1   # 小幅跳空開高

    return min(10, score)


# ── 下載 kbars ────────────────────────────────────────────────────────────────

def _fetch_kbars(api, code: str, start: str, end: str) -> list[dict]:
    """回傳 [{date, open, high, low, close}]，依日期升序。相容 Shioaji 新舊版本。"""
    try:
        import pandas as pd
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            log.warning("找不到合約：%s", code)
            return []
        kbars = api.kbars(contract, start=start, end=end)
        if not kbars:
            return []

        # Shioaji 1.5+ 回傳 KBars 物件，直接存取屬性
        try:
            df = pd.DataFrame({
                "ts":    kbars.ts,
                "Open":  kbars.Open,
                "High":  kbars.High,
                "Low":   kbars.Low,
                "Close": kbars.Close,
            })
        except AttributeError:
            # 舊版：dict-like，可 unpack
            df = pd.DataFrame({**kbars})
            df.columns = [c.capitalize() for c in df.columns]
            if "Ts" in df.columns:
                df = df.rename(columns={"Ts": "ts"})

        if df.empty:
            return []

        # 時間戳轉日期字串
        if "ts" in df.columns:
            df["date"] = pd.to_datetime(df["ts"], unit="ns").dt.strftime("%Y-%m-%d")
        elif "Date" in df.columns:
            df["date"] = df["Date"].astype(str)
        else:
            return []

        df = df.sort_values("date")
        result = []
        for _, row in df.iterrows():
            o = float(row.get("Open",  0) or 0)
            h = float(row.get("High",  0) or 0)
            l = float(row.get("Low",   0) or 0)
            c = float(row.get("Close", 0) or 0)
            if o <= 0:
                continue
            result.append({"date": row["date"], "open": o, "high": h, "low": l, "close": c})
        return result
    except Exception as e:
        log.warning("_fetch_kbars %s 失敗：%s", code, e)
        return []


# ── 判斷出場結果 ──────────────────────────────────────────────────────────────

def _outcome(open_p: float, high: float, low: float, close: float) -> tuple[str, float]:
    tp = open_p * (1 + _TP_PCT)
    sl = open_p * (1 - _SL_PCT)
    if high >= tp:
        return "hit_target", round(tp, 2)
    if low <= sl:
        return "hit_stop",   round(sl, 2)
    return "neutral", round(close, 2)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def seed(days: int = 30, reset: bool = False) -> None:
    _load_env()

    if reset:
        Path(_REVIEW_DB).unlink(missing_ok=True)
        Path(_PAPER_DB).unlink(missing_ok=True)
        log.info("已清除舊資料")

    api  = _login_shioaji()
    conn = _init_review_db(_REVIEW_DB)

    # 日期範圍（多抓 60 天供指標計算）
    end_dt   = date.today().isoformat()
    start_dt = (date.today() - timedelta(days=days + 60)).isoformat()

    # 最近 N 個交易日（回填對象）
    target_days: list[str] = []
    d = date.today() - timedelta(days=1)
    while len(target_days) < days:
        if d.weekday() < 5:
            target_days.append(d.isoformat())
        d -= timedelta(days=1)
    target_days.reverse()
    target_set = set(target_days)

    # 下載所有股票 kbars
    log.info("下載 %d 檔股票日 K（%s ~ %s）...", len(STOCKS), start_dt, end_dt)
    stock_kbars: dict[str, list[dict]] = {}
    for code, name in STOCKS:
        bars = _fetch_kbars(api, code, start_dt, end_dt)
        if bars:
            stock_kbars[code] = bars
            log.info("  %s %s：%d 根 K 棒", code, name, len(bars))
        else:
            log.warning("  %s %s：無資料，跳過", code, name)

    if not stock_kbars:
        log.error("所有股票都沒有資料，請確認 Shioaji 連線")
        return

    inserted = 0
    skipped  = 0

    for target_date in target_days:
        # 每支股票在這天的評分
        candidates = []

        for code, name in STOCKS:
            bars = stock_kbars.get(code, [])
            # 取今日棒
            today_bar = next((b for b in bars if b["date"] == target_date), None)
            if not today_bar or today_bar["open"] <= 0:
                continue

            # 取截至前一日的收盤序列（供指標計算）
            prev_closes = [
                b["close"] for b in bars
                if b["date"] < target_date and b["close"] > 0
            ]
            if len(prev_closes) < 5:
                continue

            score = _score_stock(prev_closes, today_bar["open"])
            candidates.append({
                "code": code, "name": name, "score": score,
                "bar": today_bar,
            })

        if not candidates:
            skipped += 1
            continue

        # 選最高分（同分取第一個）
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[0]
        bar = top["bar"]
        outcome, exit_price = _outcome(bar["open"], bar["high"], bar["low"], bar["close"])

        summary = (
            f"{top['name']} 開{bar['open']:.1f} 高{bar['high']:.1f} "
            f"低{bar['low']:.1f} 收{bar['close']:.1f} | "
            f"評分{top['score']} | {outcome}"
        )

        try:
            conn.execute("""
                INSERT OR IGNORE INTO dt_prediction_log
                    (date, code, name, action, dt_score, outcome,
                     daily_open, daily_high, daily_low, daily_close,
                     target_price, stop_loss, ai_summary)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                target_date, top["code"], top["name"], "long", top["score"],
                outcome,
                bar["open"], bar["high"], bar["low"], bar["close"],
                round(bar["open"] * (1 + _TP_PCT), 2),
                round(bar["open"] * (1 - _SL_PCT), 2),
                summary,
            ))
            conn.commit()
            inserted += 1
        except Exception as e:
            log.warning("insert %s 失敗：%s", target_date, e)

    log.info("寫入 %d 筆記錄（%d 天無資料跳過）", inserted, skipped)

    # 跑模擬倉
    log.info("\n開始跑 %d 天模擬倉...", len(target_days))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dt_paper_trade import run_daily_paper_trade, generate_report

    for target_date in target_days:
        result = run_daily_paper_trade(today=target_date)
        if result:
            o   = result.get("outcome", "—")
            pnl = result.get("pnl", 0) or 0
            cap = result.get("capital_after", 0) or 0
            c   = result.get("code") or "—"
            icon = {"hit_target": "✅", "hit_stop": "❌", "neutral": "⬜"}.get(o, "—")
            log.info("  %s  %s %s  %+.0f 元  本金 %,.0f", target_date, icon, c, pnl, cap)

    print()
    print(generate_report())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="用 Shioaji 真實資料填充模擬倉")
    parser.add_argument("--days",  type=int, default=30, help="回填天數（預設 30）")
    parser.add_argument("--reset", action="store_true",  help="清除舊資料重新開始")
    args = parser.parse_args()
    seed(days=args.days, reset=args.reset)
