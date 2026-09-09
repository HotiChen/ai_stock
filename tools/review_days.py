#!/usr/bin/env python3
"""
tools/review_days.py — 最近幾天到底發生了什麼

唯讀。不連 Shioaji、不打任何網路、不寫任何檔案。純粹把已經記錄下來的東西
攤開來看。

用法
----
    python3 tools/review_days.py            # 最近 5 個有紀錄的日子
    python3 tools/review_days.py --days 10
    python3 tools/review_days.py --date 2026-09-08

為什麼不重用 daytrading_review.py
---------------------------------
那支是**當日**複盤：抓分鐘 K、判斷 outcome、寫回 DB，由 main.py 在 13:35
觸發。這支只讀不寫，回答的是另一個問題——「這幾天累積下來的結果是什麼」。

設計原則
--------
資料表不存在、或某個 DB 檔案根本沒有，一律**明說**而不是顯示 0。
「今天 0 筆交易」和「這張表從來沒被建立過」是完全不同的兩件事，前者代表
策略沒進場，後者代表整條記錄鏈是斷的——這幾天反覆踩到的就是這個差別。
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

REVIEW_DB = "data/daytrading_review.db"
RESEARCH_DB = "data/research.db"
POSITIONS_DB = "data/daytrading_positions.db"

W = 78


def _rule(ch: str = "─") -> str:
    return ch * W


def _open(path: str):
    """回 (conn, 說明)。開不了就回 (None, 原因)。"""
    p = Path(path)
    if not p.exists():
        return None, f"{path} 不存在"
    if p.stat().st_size == 0:
        return None, f"{path} 是空檔案（0 bytes）"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn, ""
    except Exception as e:
        return None, f"{path} 開啟失敗：{e}"


def _has_table(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _cols(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _q(conn, sql: str, args=()) -> list:
    try:
        return conn.execute(sql, args).fetchall()
    except Exception as e:
        print(f"  ⚠️  查詢失敗：{e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════

def collect_dates(days: int, only: str | None) -> list[str]:
    """從預測表挑出最近幾個有紀錄的日子。"""
    conn, err = _open(REVIEW_DB)
    if conn is None:
        print(f"❌ 複盤資料庫：{err}")
        print("   → 從來沒有預測被寫進去。main.py 的 08:30 有跑到嗎？")
        return []
    if not _has_table(conn, "dt_prediction_log"):
        print("❌ 複盤資料庫裡沒有 dt_prediction_log 這張表")
        print("   → 這張表由 daytrading_report 在 08:30 建立並寫入。")
        return []
    if only:
        return [only]
    rows = _q(conn, "SELECT DISTINCT date FROM dt_prediction_log"
                    " ORDER BY date DESC LIMIT ?", (days,))
    return [r["date"] for r in reversed(rows)]


def predictions_for(conn, day: str) -> list:
    return _q(conn, "SELECT * FROM dt_prediction_log WHERE date=? ORDER BY dt_score DESC", (day,))


def decisions_for(conn, day: str) -> list:
    if not _has_table(conn, "ai_decision_log"):
        return []
    return _q(conn, "SELECT * FROM ai_decision_log WHERE date=? ORDER BY time, code", (day,))


def trades_for(day: str) -> tuple[list, str]:
    conn, err = _open(RESEARCH_DB)
    if conn is None:
        return [], err
    if not _has_table(conn, "daily_trades"):
        return [], "research.db 裡沒有 daily_trades 這張表"
    cols = _cols(conn, "daily_trades")
    sel = "*"
    rows = _q(conn, f"SELECT {sel} FROM daily_trades WHERE trade_date=? ORDER BY id", (day,))
    if "strategy_type" not in cols:
        return rows, "（此 DB 尚無 strategy_type 欄位，無法區分當沖／波段）"
    return rows, ""


def positions_for(day: str) -> tuple[list, str]:
    conn, err = _open(POSITIONS_DB)
    if conn is None:
        return [], err
    if not _has_table(conn, "dt_positions"):
        return [], "positions DB 裡沒有 dt_positions 這張表"
    return _q(conn, "SELECT * FROM dt_positions WHERE trade_date=? ORDER BY code", (day,)), ""


# ══════════════════════════════════════════════════════════════════════════════

def report_day(rconn, day: str, totals: dict) -> None:
    print()
    print(_rule("━"))
    print(f"  {day}")
    print(_rule("━"))

    preds = predictions_for(rconn, day)
    longs = [p for p in preds if (p["action"] or "") == "long"]
    print(f"\n【08:30 預測】{len(preds)} 檔進資料庫，其中 action=long {len(longs)} 檔")
    if preds:
        top = preds[:8]
        print(f"  {'代號':<7}{'名稱':<9}{'評分':>4}  {'動作':<6}"
              f"{'進場區間':<18}{'目標／停損':<20}{'結果'}")
        for p in top:
            k = p.keys()
            rng = (f"{p['entry_low']:.2f}–{p['entry_high']:.2f}"
                   if p["entry_low"] and p["entry_high"] else "—")
            tp = (f"{p['target_price']:.2f} / {p['stop_loss']:.2f}"
                  if p["target_price"] and p["stop_loss"] else "—")
            outcome = p["outcome"] if "outcome" in k and p["outcome"] else "未複盤"
            name = (p["name"] or "")[:4]
            print(f"  {p['code']:<7}{name:<9}{p['dt_score']:>4}  "
                  f"{(p['action'] or ''):<6}{rng:<18}{tp:<20}{outcome}")
        if len(preds) > 8:
            print(f"  …另有 {len(preds) - 8} 檔")

    # 複盤結果
    reviewed = [p for p in preds if p.keys() and "outcome" in p.keys() and p["outcome"]]
    if reviewed:
        by = defaultdict(int)
        for p in reviewed:
            by[p["outcome"]] += 1
        print(f"\n【複盤】{len(reviewed)} / {len(preds)} 檔已驗證：",
              "、".join(f"{k} {v}" for k, v in sorted(by.items())))
        totals["outcomes"].update(by)
    elif preds:
        print(f"\n【複盤】0 / {len(preds)} 檔已驗證"
              "　← 13:35 的 DaytradingReview 沒跑到，或沒有 action=long 的標的")

    # 模擬損益（dt_backfill / dt_simulate 寫的）
    sims = [p for p in preds
            if "sim_pnl" in p.keys() and p["sim_pnl"] is not None]
    if sims:
        tot = sum(p["sim_pnl"] for p in sims)
        wins = sum(1 for p in sims if p["sim_pnl"] > 0)
        print(f"\n【模擬損益】{len(sims)} 筆　勝 {wins} / 負 {len(sims) - wins}"
              f"　合計 {tot:+,.0f} 元")
        totals["sim_pnl"] += tot
        totals["sim_n"] += len(sims)
        totals["sim_wins"] += wins

    # 9:05 LLM 是否翻掉規則
    decs = decisions_for(rconn, day)
    if decs:
        flips = [d for d in decs
                 if d["stage"] == "reconfirm"
                 and (d["rule_action"] or "") == "skip"
                 and (d["final_action"] or "") == "proceed"]
        blocks = [d for d in decs
                  if d["stage"] == "reconfirm"
                  and (d["rule_action"] or "") == "proceed"
                  and (d["final_action"] or "") == "skip"]
        mode = decs[0]["llm_mode"]
        print(f"\n【9:05 再確認】llm_mode={mode}　共 {len([d for d in decs if d['stage']=='reconfirm'])} 檔")
        if flips:
            print(f"  ⚠️  LLM 翻掉規則的否決 {len(flips)} 檔："
                  + "、".join(d["code"] for d in flips))
            totals["flips"] += len(flips)
        if blocks:
            print(f"  LLM 比規則更保守 {len(blocks)} 檔："
                  + "、".join(d["code"] for d in blocks))

    # 實際持倉狀態
    pos, perr = positions_for(day)
    if perr:
        print(f"\n【持倉】{perr}")
    elif pos:
        by = defaultdict(list)
        for p in pos:
            by[p["status"] or "?"].append(p["code"])
        print("\n【持倉狀態】" + "　".join(
            f"{k} {len(v)}" for k, v in sorted(by.items())))
        held = [p for p in pos if (p["status"] or "") in ("active", "sell_submitted", "buy_submitted")]
        for p in held:
            qty = p["quantity"] or 0
            lot = p["lot_type"] or "common"
            shares = qty * 1000 if lot == "common" else qty
            px = p["entry_price"] or 0
            print(f"  ⚠️  {p['code']} {p['name'] or ''} 仍為 {p['status']}"
                  f"（{qty} {'張' if lot=='common' else '股'}"
                  + (f"／約 {shares*px:,.0f} 元" if px else "") + "）")
            totals["stuck"].append(f"{day} {p['code']}")

    # 實際成交
    trades, terr = trades_for(day)
    if terr and not trades:
        print(f"\n【成交】{terr}")
    else:
        buys = [t for t in trades if t["action"] == "buy"]
        sells = [t for t in trades if t["action"] == "sell"]
        others = [t for t in trades if t["action"] not in ("buy", "sell")]
        pnl = sum((t["pnl"] or 0) for t in trades)
        print(f"\n【實際成交】買 {len(buys)}　賣 {len(sells)}"
              + (f"　其他 {len(others)}" if others else "")
              + f"　已實現 {pnl:+,.0f} 元")
        for t in sells:
            reason = (t["exit_reason"] if "exit_reason" in t.keys() else "") or "—"
            print(f"  賣出 {t['code']} {t['name'] or ''} "
                  f"{(t['price'] or 0):,.2f} × {t['quantity'] or 0}"
                  f"　{reason}　{(t['pnl'] or 0):+,.0f}")
        totals["real_pnl"] += pnl
        totals["buys"] += len(buys)
        totals["sells"] += len(sells)
        if terr:
            print(f"  {terr}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="最近幾天的當沖結果（唯讀）")
    ap.add_argument("--days", type=int, default=5, help="看最近幾個有紀錄的日子")
    ap.add_argument("--date", help="只看指定日期 YYYY-MM-DD")
    args = ap.parse_args(argv)

    print(_rule("═"))
    print("  QUANT·AI 近期結果彙總（唯讀，不連券商）")
    print(_rule("═"))

    dates = collect_dates(args.days, args.date)
    if not dates:
        print("\n沒有任何可看的日子。")
        return 1

    rconn, _ = _open(REVIEW_DB)
    totals = {
        "outcomes": defaultdict(int), "sim_pnl": 0.0, "sim_n": 0, "sim_wins": 0,
        "real_pnl": 0.0, "buys": 0, "sells": 0, "flips": 0, "stuck": [],
    }
    for d in dates:
        report_day(rconn, d, totals)

    print()
    print(_rule("═"))
    print(f"  合計（{dates[0]} – {dates[-1]}，{len(dates)} 個交易日）")
    print(_rule("═"))
    if totals["outcomes"]:
        print("  複盤結果　" + "、".join(
            f"{k} {v}" for k, v in sorted(totals["outcomes"].items())))
    if totals["sim_n"]:
        wr = totals["sim_wins"] / totals["sim_n"] * 100
        print(f"  模擬損益　{totals['sim_n']} 筆　勝率 {wr:.1f}%"
              f"　合計 {totals['sim_pnl']:+,.0f} 元")
    print(f"  實際成交　買 {totals['buys']}　賣 {totals['sells']}"
          f"　已實現 {totals['real_pnl']:+,.0f} 元")
    if totals["flips"]:
        print(f"  ⚠️  9:05 LLM 翻掉規則否決　共 {totals['flips']} 次")
    if totals["stuck"]:
        print(f"  ⚠️  未平倉殘留　{len(totals['stuck'])} 筆："
              + "、".join(totals["stuck"]))
    if not totals["buys"] and not totals["sells"]:
        print("\n  這段期間沒有任何成交。若預測有出來但沒進場，"
              "看上面每天的【9:05 再確認】與【持倉狀態】找原因。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
