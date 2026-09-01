"""
dt_backfill.py — 歷史當沖預測回填

為什麼需要
----------
正式環境的 dt_prediction_log 幾乎沒有樣本（2026-08-21 起系統靜默停擺）。
等每日累積要好幾個月才有統計意義，而參數校準（停損/停利該設多少）與模擬
損益層都需要先有資料才能驗證。本模組用歷史日線把過去 N 個交易日的預測重建
出來，一次取得上千筆樣本。

回填的是「規則版預測」，不是 AI 預測
------------------------------------
決策走 ``dt_rules.rule_decide_action()``（確定性規則），**不含 LLM**。理由：
重跑數千次 LLM 既慢又貴，而且當時的市場氣氛無法重現，硬跑只會得到「用今天
的語境評論過去」的假資料。

所以回填資料能回答的是「技術評分 + 規則選股賺不賺錢」，**不能**回答「AI 過濾
有沒有加分」。後者要靠每日真實累積的 live 資料去比這條基準線——這正是
dt_counterfactual.py 的策略 B（無 LLM 過濾）。兩者以 ``source`` 欄位區分。

前視偏誤（look-ahead bias）
---------------------------
T 日盤前的預測只能看到 T-1 收盤為止的資料。本模組一律以 ``slice_before(df, T)``
取資料——**嚴格小於** T，而非「小於等於 T-1」——因為後者需要交易日曆才能算出
正確的 T-1，遇到連假就會錯。前者無論假期怎麼排都不可能取到 T 日當天。

若不小心洩漏未來資料，回測績效會漂亮到不真實，而且不會有任何錯誤訊息。
tests/test_dt_backfill.py::TestNoLookAhead 用「餵進未來 K 線，結果必須完全
不變」的方式守住這條線。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Optional, Sequence

log = logging.getLogger(__name__)

#: 回填來源標記，寫進 dt_prediction_log.source
SOURCE = "backfill"


# ══════════════════════════════════════════════════════════════════════════════
# 純函式：切片與指標
# ══════════════════════════════════════════════════════════════════════════════

def slice_before(df, trade_date: date):
    """回傳 ``df`` 中**嚴格早於** trade_date 的所有列。

    這是整個模組防止前視偏誤的唯一入口。用「嚴格小於 T」而不是「小於等於
    T-1」，是因為後者要先算出 T 的前一個交易日，需要交易日曆；遇到連假、
    颱風假、補班日都可能算錯，而算錯的方向剛好是「多看到一天」——也就是
    洩漏。嚴格小於 T 沒有這個風險。
    """
    if df is None or len(df) == 0:
        return df
    return df[[ts.date() < trade_date for ts in df.index]]


def indicators_as_of(df, trade_date: date) -> Optional[dict]:
    """算出 trade_date 盤前（= T-1 收盤後）可得的技術指標。

    資料不足回傳 None 而非拋出例外：一支股票上市未滿 80 天，不該讓整批
    回填中斷、連累其他 1,199 筆。
    """
    from technical_indicators import calculate_indicators

    sliced = slice_before(df, trade_date)
    if sliced is None or len(sliced) < 80:
        return None
    try:
        return calculate_indicators(sliced)
    except Exception as e:
        log.debug("indicators_as_of failed (%s): %s", trade_date, e)
        return None


def market_change_as_of(index_df, trade_date: date) -> float:
    """T 日盤前看到的大盤漲跌幅 = T-1 收盤相對 T-2 收盤的變化（%）。

    index_df 為加權指數（^TWII）日線。歷史不足兩根回傳 0.0——與
    market_index.fetch_market_index_change() 的失敗行為一致，讓
    dt_rules 走「大盤資料缺，不擋但註記」的既有路徑。
    """
    if index_df is None:
        return 0.0
    sliced = slice_before(index_df, trade_date)
    if sliced is None or len(sliced) < 2:
        return 0.0
    prev, last = float(sliced["Close"].iloc[-2]), float(sliced["Close"].iloc[-1])
    if prev <= 0:
        return 0.0
    return round((last - prev) / prev * 100.0, 4)


# ══════════════════════════════════════════════════════════════════════════════
# 單筆預測重建
# ══════════════════════════════════════════════════════════════════════════════

def _price_levels(indicators: dict) -> tuple:
    """規則判 long 時的進場區間 / 目標 / 停損。

    完全沿用正式流程的公式：進場區間走 dt_rules.rule_entry_range（現價與
    VWAP 定錨），目標與停損走 daytrading_analyzer._calc_prices_from_atr
    （ATR 公式），ATR 缺值時以固定百分比保底。與 daytrading_report
    ._advisor_analysis 的行為一致，回填與正式資料才可互相比較。
    """
    import dt_rules
    from daytrading_analyzer import _calc_prices_from_atr

    entry_low, entry_high = dt_rules.rule_entry_range(indicators)
    if entry_low is None or entry_high is None:
        return None, None, None, None

    raw_atr = indicators.get("ATR")
    try:
        atr = float(raw_atr) if raw_atr is not None else None
    except (TypeError, ValueError):
        atr = None

    target_price, stop_loss = _calc_prices_from_atr(
        entry_high, atr,
        resistance=indicators.get("resistance"),
        support=indicators.get("support"),
    )
    if stop_loss is None:
        stop_loss = round(entry_low * 0.97, 2)
    if target_price is None:
        target_price = round(entry_high * 1.03, 2)
    # 停損不得落在進場區間內（否則一進場就觸發）
    if stop_loss >= entry_low:
        stop_loss = round(entry_low * 0.97, 2)

    return entry_low, entry_high, target_price, stop_loss


def build_prediction(
    code: str,
    name: str,
    df,
    trade_date: date,
    market: Optional[dict] = None,
    chip: Optional[dict] = None,
):
    """重建 ``code`` 在 ``trade_date`` 盤前的規則版預測。

    資料不足回傳 None。回傳的 DTPrediction 已標記 source='backfill'。
    """
    import dt_rules
    from daytrading_db import DTPrediction
    from stock_query import _assess_day_trading

    indicators = indicators_as_of(df, trade_date)
    if indicators is None:
        return None

    market = market or {"index_change_pct": 0.0, "futures_premium_pct": 0.0}

    assessment = _assess_day_trading(indicators, chip=chip, market=market)
    if not assessment.get("data_ok", False):
        return None
    dt_score = assessment.get("score", 0)

    rule = dt_rules.rule_decide_action(dt_score, indicators, market)

    if rule.action == "long":
        entry_low, entry_high, target_price, stop_loss = _price_levels(indicators)
    else:
        entry_low = entry_high = target_price = stop_loss = None

    return DTPrediction(
        date=trade_date.isoformat(),
        code=code, name=name,
        dt_score=dt_score,
        action=rule.action,
        entry_low=entry_low, entry_high=entry_high,
        target_price=target_price, stop_loss=stop_loss,
        # 規則理由必須留存，否則事後無法追溯「當時為什麼判 skip」
        ai_summary=f"(回填/規則) {rule.reason}",
        source=SOURCE,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 批次回填
# ══════════════════════════════════════════════════════════════════════════════

def run_backfill(
    codes: Sequence[tuple[str, str]],
    trade_dates: Iterable[date],
    history: dict,
    index_history=None,
    chip_by_date: Optional[dict] = None,
    db_path: Optional[str] = None,
) -> dict:
    """對每個交易日 × 每支股票重建預測並寫入複盤資料庫。

    Args:
        codes:         [(code, name), ...]
        trade_dates:   要回填的日期（非交易日會被跳過並計數）
        history:       {code: OHLCV DataFrame}，缺的 code 計入 no_history
        index_history: 加權指數日線 DataFrame，None 時大盤一律視為 0.0
        chip_by_date:  {"YYYYMMDD": {code: chip_dict}}，None 時不帶籌碼
        db_path:       複盤資料庫路徑；None 用 DaytradingDB 預設

    Returns:
        統計 dict：saved / trade_dates / skipped_non_trading / no_history /
        insufficient_data

    寫入用 INSERT OR IGNORE + UNIQUE(date, code)，所以**已存在的 live 記錄
    不會被回填覆蓋**——真實資料永遠優先。
    """
    from daytrading_db import DaytradingDB
    from tw_trading_calendar import is_twse_holiday

    db = DaytradingDB(db_path) if db_path else DaytradingDB()
    chip_by_date = chip_by_date or {}

    stats = {
        "saved": 0, "trade_dates": 0, "skipped_non_trading": 0,
        "no_history": 0, "insufficient_data": 0,
    }

    for trade_date in trade_dates:
        if trade_date.weekday() >= 5 or is_twse_holiday(trade_date):
            stats["skipped_non_trading"] += 1
            continue
        stats["trade_dates"] += 1

        market = {
            "index_change_pct": market_change_as_of(index_history, trade_date),
            "futures_premium_pct": 0.0,   # 期貨溢價無歷史來源，一律視為缺值
        }
        chips = chip_by_date.get(trade_date.strftime("%Y%m%d"), {})

        predictions = []
        for code, name in codes:
            df = history.get(code)
            if df is None:
                stats["no_history"] += 1
                continue
            pred = build_prediction(
                code, name, df, trade_date,
                market=market, chip=chips.get(code),
            )
            if pred is None:
                stats["insufficient_data"] += 1
                continue
            predictions.append(pred)

        if predictions:
            stats["saved"] += db.save_predictions(predictions)

    log.info(
        "回填完成：%d 個交易日、寫入 %d 筆（跳過非交易日 %d、無歷史 %d、資料不足 %d）→ %s",
        stats["trade_dates"], stats["saved"], stats["skipped_non_trading"],
        stats["no_history"], stats["insufficient_data"],
        db_path or "data/daytrading_review.db",
    )
    return stats


# ══════════════════════════════════════════════════════════════════════════════
# 交易日與股票池
# ══════════════════════════════════════════════════════════════════════════════

def recent_trade_dates(days: int, end: Optional[date] = None) -> list[date]:
    """回傳截至 ``end``（含）為止最近 ``days`` 個交易日，由舊到新。

    交易日 = 週一至週五且不在 TWSE 假日表內，與 main.is_trading_day 同一套判準。
    絕不回傳晚於 ``end`` 的日期——回填產生未來日期的預測，會被之後的每日流程
    當成真實資料，且永遠不會被複盤。
    """
    from datetime import timedelta

    from tw_trading_calendar import is_twse_holiday

    end = end or date.today()
    out: list[date] = []
    cursor = end
    # 上限防呆：連假再長也不會讓 days 個交易日跨越 days*3 個日曆日
    for _ in range(max(days * 3, 30)):
        if len(out) >= days:
            break
        if cursor.weekday() < 5 and not is_twse_holiday(cursor):
            out.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(out)


def default_universe() -> list[tuple[str, str]]:
    """預設回填股票池：config.STOCK_NAMES（約 80 檔台股主要標的）。

    已知偏誤：這是「今天仍在市場上」的清單，期間下市或被處置的個股不在其中
    （倖存者偏誤），統計結果會略偏樂觀。報告需標註此限制。
    """
    import config
    names = getattr(config, "STOCK_NAMES", {}) or {}
    return [(code, name) for code, name in names.items()]


# ══════════════════════════════════════════════════════════════════════════════
# 資料抓取 — 一律走 Shioaji（見 shioaji_history.py 的說明）
# ══════════════════════════════════════════════════════════════════════════════
#
# 為什麼不用 yfinance：
#   1. yfinance 的 1 分鐘 K 只保留最近 7 天。回填 60 個交易日、以及模擬層
#      判斷「當日先觸停利還是先觸停損」，都需要更久以前的分鐘資料。
#   2. 台股在 yfinance 上常缺值、除權息調整不一致；Shioaji 是券商原始資料，
#      與實際成交一致，也與正式交易流程用的是同一個來源。
#
# 需要多少歷史：回填 T 日需要 T 之前 80 根日 K，所以要回填 N 個交易日，
# 至少需要 (N + 80) 個交易日 ≈ (N + 80) * 1.45 個日曆日。HISTORY_PAD_DAYS
# 在此基礎上再留餘裕。

# 回填 T 日需要 T 之前 80 根日 K。80 個交易日約 112 個日曆天，加上農曆年
# 等連假的餘裕取 130。原本設 160 會多抓一個月的分鐘 K——Shioaji 歷史資料
# 有流量上限，多抓的每一天都在消耗它。
HISTORY_PAD_DAYS = 130

#: 抓到的日線快取目錄。程序中斷（逾時、Ctrl-C、token 過期）時已抓到的
#: 部分要留下來，續跑才不必重抓——重抓會再吃一次流量上限。
DEFAULT_CACHE_DIR = "data/history_cache"


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="回填歷史當沖預測（規則版，不含 LLM）到 daytrading_review.db",
    )
    ap.add_argument("--days", type=int, default=60,
                    help="回填最近幾個交易日（預設 60）")
    ap.add_argument("--end", default=None,
                    help="回填截止日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--codes", default=None,
                    help="指定股票代號，逗號分隔（預設用 config.STOCK_NAMES）")
    ap.add_argument("--db", default=None, help="複盤資料庫路徑")
    ap.add_argument("--chunk-days", type=int, default=30,
                    help="kbars 分段抓取的天數（預設 30，調小可降低記憶體）")
    ap.add_argument("--limit", type=int, default=None,
                    help="只回填前 N 檔（Shioaji 歷史資料有流量上限，"
                         "檔數多時建議分批）")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                    help=f"日線快取目錄（預設 {DEFAULT_CACHE_DIR}）")
    ap.add_argument("--no-cache", action="store_true",
                    help="忽略快取，全部重新抓取")
    ap.add_argument("--dry-run", action="store_true",
                    help="只顯示將回填的範圍與檔數，不寫入")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    end = date.fromisoformat(args.end) if args.end else date.today()
    trade_dates = recent_trade_dates(args.days, end=end)
    universe = ([(c.strip(), c.strip()) for c in args.codes.split(",") if c.strip()]
                if args.codes else default_universe())
    if args.limit:
        universe = universe[:args.limit]

    print(f"回填範圍：{trade_dates[0]} ~ {trade_dates[-1]}"
          f"（{len(trade_dates)} 個交易日）× {len(universe)} 檔")
    if args.dry_run:
        print("--dry-run：未寫入任何資料")
        return 0

    import os
    from datetime import timedelta

    import shioaji_history as sh
    from monitor_agent import ensure_connected

    api = ensure_connected(
        os.getenv("SHIOAJI_API_KEY", ""),
        os.getenv("SHIOAJI_SECRET_KEY", ""),
        simulation=os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true",
    )
    if api is None:
        print("❌ Shioaji 連線失敗，無法取得歷史資料。請確認 .env 的 "
              "SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY。")
        return 1

    # Shioaji 歷史資料有每日流量上限。撞到之後的徵狀是「Token is expired」
    # 加上無止盡的「Not ready」，完全看不出真正原因——所以先問一次。
    usage = sh.usage_report(api)
    if usage:
        print(f"Shioaji 歷史資料用量：已用 {usage['used_mb']:,.0f} MB / "
              f"上限 {usage['limit_mb']:,.0f} MB"
              + (f"（剩 {usage['remaining_pct']:.0f}%）"
                 if usage["remaining_pct"] is not None else ""))
        if usage["remaining_pct"] is not None and usage["remaining_pct"] < 20:
            print("⚠️ 剩餘流量不足 20%，建議用 --limit 分批，或明天再跑")

    # 多抓 HISTORY_PAD_DAYS 天，確保最舊的那個交易日前面也有 80 根 K
    hist_start = trade_dates[0] - timedelta(days=HISTORY_PAD_DAYS)
    print(f"抓取歷史（Shioaji）：{hist_start} ~ {end}，分段 {args.chunk_days} 天…")

    # 先讀快取，只抓缺的——重抓會再吃一次流量上限
    history: dict = {}
    todo = []
    for code, _ in universe:
        cached = None if args.no_cache else sh.cache_load(args.cache_dir, code)
        if cached is not None and len(cached) >= 80:
            history[code] = cached
        else:
            todo.append(code)
    if history:
        print(f"快取命中 {len(history)} 檔，需抓取 {len(todo)} 檔")

    if todo:
        import shioaji_session
        fetched = sh.fetch_daily_batch(
            api, todo, hist_start, end,
            chunk_days=args.chunk_days,
            reconnect=shioaji_session.reconnect,
            # 邊抓邊存：中斷時已抓到的不會白費
            on_result=lambda c, df: sh.cache_save(args.cache_dir, c, df),
        )
        history.update(fetched)

    index_history = sh.fetch_index_daily(api, hist_start, end,
                                         chunk_days=args.chunk_days)
    if index_history is None:
        print("⚠️ 加權指數歷史取得失敗，大盤條件一律視為缺值（規則不擋，僅註記）")

    stats = run_backfill(
        codes=universe, trade_dates=trade_dates,
        history=history, index_history=index_history,
        db_path=args.db,
    )

    print(f"\n✅ 寫入 {stats['saved']} 筆"
          f"（{stats['trade_dates']} 個交易日，"
          f"資料不足跳過 {stats['insufficient_data']}，"
          f"無歷史 {stats['no_history']}）")
    print("注意：回填為規則版預測（不含 LLM 判斷），且股票池有倖存者偏誤。")
    print(f"資料來源：Shioaji kbars（{len(history)}/{len(universe)} 檔取得成功）")
    if len(history) < len(universe):
        print(f"提示：{len(universe) - len(history)} 檔未取得。已抓到的存在 "
              f"{args.cache_dir}，直接再跑一次會從中斷處續抓。")
    after = sh.usage_report(api)
    if after and usage:
        print(f"本次消耗流量約 {after['used_mb'] - usage['used_mb']:,.0f} MB"
              + (f"，剩餘 {after['remaining_pct']:.0f}%"
                 if after["remaining_pct"] is not None else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
