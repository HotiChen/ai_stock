from __future__ import annotations

import logging
import requests
from dataclasses import dataclass, field

from market_scan import batch_fetch_snapshots
from stock_research import (
    StockFundamentals,
    analyze_stock_ai,
    fetch_stock_fundamentals,
    fetch_stock_news,
)

log = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ScanCriteria:
    min_volume:  int   = 5_000    # 最低成交量（張）
    min_price:   float = 10.0     # 最低股價（避免地雷股）
    max_price:   float = 5_000.0  # 最高股價（避免流動性差）
    top_n:       int   = 20       # 進入深度分析的候選數


@dataclass
class ScanResult:
    code:         str
    name:         str
    close:        float
    change_rate:  float
    total_volume: int
    analysis:     dict | None     # None = 深度分析未執行或失敗


# ── Scoring & screening ───────────────────────────────────────────────────────

def score_snapshot(snap: dict) -> float:
    """Composite score = abs(change_rate) × log1p(volume). Higher = more interesting."""
    import math
    volume = snap.get("total_volume", 0)
    change = abs(snap.get("change_rate", 0.0))
    return change * math.log1p(max(volume, 0))


def screen_candidates(snapshots: dict[str, dict], criteria: ScanCriteria) -> list[dict]:
    """Filter snapshots by criteria, return top_n sorted by score."""
    rows = []
    for code, snap in snapshots.items():
        volume = snap.get("total_volume", 0)
        price = snap.get("close", 0.0)
        if volume < criteria.min_volume:
            continue
        if price < criteria.min_price or price > criteria.max_price:
            continue
        row = dict(snap)
        row["code"] = code
        rows.append(row)

    rows.sort(key=score_snapshot, reverse=True)
    return rows[: criteria.top_n]


# ── API helpers ───────────────────────────────────────────────────────────────

def get_all_stock_codes(api) -> list[str]:
    """Return all unique stock codes from TSE + OTC + OES exchanges."""
    codes: list[str] = []
    for exchange in ("TSE", "OTC", "OES"):
        try:
            for c in getattr(api.Contracts.Stocks, exchange, []):
                codes.append(c.code)
        except Exception:
            pass
    seen: set[str] = set()
    unique = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


# ── TWSE simulation-mode scanner ──────────────────────────────────────────────

_TWSE_DAY_ALL_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
)


def fetch_twse_sim_candidates(
    criteria: ScanCriteria | None = None,
    timeout: int = 15,
) -> list[dict]:
    """模擬模式（api=None）時，從 TWSE OpenAPI 取得當日交易資料並篩選候選股。

    回傳格式與 screen_candidates() 相同，可直接交給 PremarketJob 使用。
    TWSE API 僅包含上市股票（TSE），不含上櫃（OTC）。
    """
    import requests as _req

    criteria = criteria or ScanCriteria()
    try:
        resp = requests.get(_TWSE_DAY_ALL_URL, timeout=timeout)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        log.warning("fetch_twse_sim_candidates: TWSE API failed: %s", e)
        return []

    snapshots: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("Code", "")).strip()
        # 只保留 4 位純數字股票（排除 ETF 如 0050、特別股）
        if not code.isdigit() or len(code) != 4:
            continue
        try:
            close  = float(row.get("ClosingPrice", 0) or 0)
            prev   = float(row.get("OpeningPrice",  0) or 0)   # TWSE 無前日收盤；用開盤近似
            change = float(row.get("Change", 0) or 0)
            volume = float(str(row.get("TradeVolume", "0")).replace(",", "") or 0) / 1000
            if close <= 0:
                continue
            prev_close = close - change if change != 0 else close
            change_pct = change / prev_close * 100 if prev_close > 0 else 0.0
            snapshots[code] = {
                "code":         code,
                "name":         str(row.get("Name", code)).strip(),
                "close":        close,
                "change_rate":  change_pct,
                "total_volume": int(volume),
                "open":         prev,
                "high":         float(row.get("HighestPrice", close) or close),
                "low":          float(row.get("LowestPrice",  close) or close),
            }
        except (ValueError, TypeError):
            continue

    candidates = screen_candidates(snapshots, criteria)
    log.info("fetch_twse_sim_candidates: %d 候選股（TWSE OpenAPI）", len(candidates))
    return candidates


# ── Deep analysis ─────────────────────────────────────────────────────────────

def run_deep_analysis(
    candidates: list[dict],
    indicators_map: dict[str, dict],
    progress_cb=None,
) -> list[ScanResult]:
    """Run news + fundamentals + AI for each candidate.

    progress_cb(current, total, code) is called after each stock if provided.
    """
    results: list[ScanResult] = []
    total = len(candidates)

    for i, snap in enumerate(candidates):
        code = snap.get("code", "")
        name = snap.get("name", code)

        try:
            news         = fetch_stock_news(code, name)
            fundamentals = fetch_stock_fundamentals(code)
            indicators   = indicators_map.get(code, {})
            analysis     = analyze_stock_ai(code, name, news, fundamentals, indicators)
        except Exception as e:
            log.warning(f"深度分析失敗 {code}: {e}")
            analysis = None

        results.append(ScanResult(
            code=code,
            name=name,
            close=snap.get("close", 0.0),
            change_rate=snap.get("change_rate", 0.0),
            total_volume=snap.get("total_volume", 0),
            analysis=analysis,
        ))

        if progress_cb:
            progress_cb(i + 1, total, code)

    return results


# ── Full pipeline ─────────────────────────────────────────────────────────────

def run_full_market_scan(
    api,
    name_map: dict[str, str],
    criteria: ScanCriteria | None = None,
    progress_cb=None,
) -> list[ScanResult]:
    """
    Full pipeline:
    1. Get all stock codes from Shioaji
    2. Batch fetch snapshots (fast)
    3. Screen to top candidates
    4. Run deep analysis (news + fundamentals + AI)
    """
    if criteria is None:
        criteria = ScanCriteria()

    log.info("取得所有股票代碼...")
    all_codes = get_all_stock_codes(api)
    log.info(f"共 {len(all_codes)} 支股票")

    log.info("批次抓取快照...")
    snapshots = batch_fetch_snapshots(api, all_codes)
    for code, snap in snapshots.items():
        snap["name"] = name_map.get(code, code)
    log.info(f"取得 {len(snapshots)} 支快照")

    candidates = screen_candidates(snapshots, criteria)
    log.info(f"篩選後候選：{len(candidates)} 支 → 進行深度分析")

    return run_deep_analysis(candidates, indicators_map={}, progress_cb=progress_cb)


# ── Candidate builder (lightweight, no deep analysis) ─────────────────────────

def build_market_candidates(
    api=None,
    name_map: dict[str, str] = None,
    manual_codes: list[str] = None,
    criteria: ScanCriteria = None,
) -> list[dict]:
    """Build a flat list of candidate dicts from scan and/or manual codes.

    Fields guaranteed on every row:
        code, name, close, change_rate, total_volume, source, analysis

    Rules:
    - api=None and no manual_codes → []
    - api=None and manual_codes → manual entries with close=0.0
    - api provided → scan path; manual_codes also included but scan wins on dedup
    """
    if name_map is None:
        name_map = {}
    if criteria is None:
        criteria = ScanCriteria()

    scan_rows: list[dict] = []
    manual_rows: list[dict] = []

    # ── Scan path ──────────────────────────────────────────────────────────────
    if api is not None:
        all_codes   = get_all_stock_codes(api)
        snapshots   = batch_fetch_snapshots(api, all_codes)
        # Enrich snapshots with name
        for code, snap in snapshots.items():
            snap["name"] = name_map.get(code, code)
        candidates = screen_candidates(snapshots, criteria)
        for row in candidates:
            code = row.get("code", "")
            scan_rows.append({
                "code":         code,
                "name":         name_map.get(code, row.get("name", code)),
                "close":        row.get("close", 0.0),
                "change_rate":  row.get("change_rate", 0.0),
                "total_volume": row.get("total_volume", 0),
                "source":       "scan",
                "analysis":     "",
            })

    # ── Manual path ────────────────────────────────────────────────────────────
    if manual_codes:
        for code in manual_codes:
            manual_rows.append({
                "code":         code,
                "name":         name_map.get(code, code),
                "close":        0.0,
                "change_rate":  0.0,
                "total_volume": 0,
                "source":       "manual",
                "analysis":     "manual",
            })

    # ── Dedup: scan wins over manual ───────────────────────────────────────────
    scan_codes = {r["code"] for r in scan_rows}
    combined = scan_rows + [r for r in manual_rows if r["code"] not in scan_codes]
    return combined
