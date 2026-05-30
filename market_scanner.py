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
    min_volume:       int   = 500      # 最低成交量（張，絕對流動性門檻）
    min_volume_ratio: float = 1.5      # 最低量比（相對於均量的倍數）
    min_price:        float = 10.0     # 最低股價（避免地雷股）
    max_price:        float = 5_000.0  # 最高股價（避免流動性差）
    top_n:            int   = 20       # 進入深度分析的候選數


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
    """Composite score. Higher = more interesting.

    若 snapshot 有 volume_ratio 欄位：score = abs(change_rate) * volume_ratio
    否則 fallback：score = abs(change_rate) * log1p(volume)
    """
    import math
    change = abs(snap.get("change_rate", 0.0))
    volume_ratio = snap.get("volume_ratio")
    if volume_ratio is not None:
        return change * float(volume_ratio)
    volume = snap.get("total_volume", 0)
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
        # 相對量能過濾：若 snapshot 有 volume_ratio，強制比對 min_volume_ratio
        volume_ratio = snap.get("volume_ratio")
        if volume_ratio is not None and float(volume_ratio) < criteria.min_volume_ratio:
            continue
        row = dict(snap)
        row["code"] = code
        rows.append(row)

    rows.sort(key=score_snapshot, reverse=True)
    return rows[: criteria.top_n]


# ── API helpers ───────────────────────────────────────────────────────────────

def get_all_stock_codes(api) -> list[str]:
    """Return unique stock codes eligible for day trading from TSE + OTC + OES."""
    from shioaji.constant import DayTrade
    seen: set[str] = set()
    unique: list[str] = []
    for exchange in ("TSE", "OTC", "OES"):
        try:
            for c in getattr(api.Contracts.Stocks, exchange, []):
                if c.code not in seen and c.day_trade != DayTrade.No:
                    seen.add(c.code)
                    unique.append(c.code)
        except Exception:
            pass
    return unique


# ── TWSE simulation-mode scanner ──────────────────────────────────────────────

_TWSE_DAY_ALL_URL   = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
_TWSE_COMPANY_URL   = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_TPEX_COMPANY_URL   = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"

_name_cache: dict[str, str] = {}


def _fetch_name_map(timeout: int = 10) -> dict[str, str]:
    """一次拿 TWSE + TPEX 全市場股票代號→名稱對照表（有快取）。"""
    global _name_cache
    if _name_cache:
        return _name_cache
    result: dict[str, str] = {}
    try:
        resp = requests.get(_TWSE_COMPANY_URL, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.ok:
            for item in resp.json():
                code = str(item.get("公司代號") or item.get("Code") or "").strip()
                name = str(item.get("公司簡稱") or item.get("Name") or "").strip()
                if code and name:
                    result[code] = name
    except Exception as e:
        log.debug("_fetch_name_map TWSE failed: %s", e)
    try:
        resp = requests.get(_TPEX_COMPANY_URL, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.ok:
            for item in resp.json():
                code = str(item.get("SecuritiesCode") or item.get("Code") or "").strip()
                name = str(item.get("CompanyName") or item.get("Name") or "").strip()
                if code and name:
                    result[code] = name
    except Exception as e:
        log.debug("_fetch_name_map TPEX failed: %s", e)
    if result:
        _name_cache = result
        log.info("_fetch_name_map: 取得 %d 支股票名稱", len(result))
    return result

# 當 TWSE API 與 yfinance 均不可用時的保底股票清單（高流動性台股）
_FALLBACK_STOCKS: list[tuple[str, str]] = [
    ("2330", "台積電"),
    ("2317", "鴻海"),
    ("2454", "聯發科"),
    ("2382", "廣達"),
    ("2603", "長榮"),
    ("2308", "台達電"),
    ("2881", "富邦金"),
    ("2882", "國泰金"),
    ("2886", "兆豐金"),
    ("3008", "大立光"),
    ("2303", "聯電"),
    ("2412", "中華電"),
    ("2891", "中信金"),
    ("2884", "玉山金"),
    ("2357", "華碩"),
]


def _fetch_yfinance_snapshots(codes_names: list[tuple[str, str]]) -> dict[str, dict]:
    """用 yfinance 補抓快照；失敗個別略過。"""
    snapshots: dict[str, dict] = {}
    try:
        import yfinance as yf
    except ImportError:
        return snapshots
    for code, name in codes_names:
        try:
            ticker = yf.Ticker(f"{code}.TW")
            info = ticker.fast_info
            close = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
            if not close:
                hist = ticker.history(period="2d")
                if hist is not None and not hist.empty:
                    close = float(hist["Close"].iloc[-1])
            if not close:
                continue
            vol = getattr(info, "three_month_average_volume", 0) or 0
            snapshots[code] = {
                "code":         code,
                "name":         name,
                "close":        float(close),
                "change_rate":  0.0,
                "total_volume": int(vol // 1000),
            }
        except Exception:
            continue
    return snapshots


def _fallback_candidates(criteria: ScanCriteria) -> list[dict]:
    """TWSE 與 yfinance 均失敗時，用保底清單讓流程繼續。"""
    snaps = _fetch_yfinance_snapshots(_FALLBACK_STOCKS)
    if not snaps:
        # yfinance 也失敗：直接回傳保底清單（close=0，讓 AI 仍能分析）
        result = [
            {"code": c, "name": n, "close": 0.0, "change_rate": 0.0, "total_volume": 99999}
            for c, n in _FALLBACK_STOCKS[: criteria.top_n]
        ]
        log.warning("fetch_twse_sim_candidates: 使用保底股票清單（無價格資料）")
        return result
    candidates = screen_candidates(snaps, ScanCriteria(
        min_volume=0, min_price=0.0, max_price=999999.0, top_n=criteria.top_n
    ))
    log.warning("fetch_twse_sim_candidates: 使用 yfinance 保底清單 %d 支", len(candidates))
    return candidates


def fetch_twse_sim_candidates(
    criteria: ScanCriteria | None = None,
    timeout: int = 15,
) -> list[dict]:
    """模擬模式（api=None）時，從 TWSE OpenAPI 取得當日交易資料並篩選候選股。

    回傳格式與 screen_candidates() 相同，可直接交給 PremarketJob 使用。
    TWSE API 不可用時依序嘗試 yfinance → 保底清單，確保流程不中斷。
    """
    criteria = criteria or ScanCriteria()
    try:
        resp = requests.get(
            _TWSE_DAY_ALL_URL,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        log.warning("fetch_twse_sim_candidates: TWSE API failed: %s — 嘗試 fallback", e)
        return _fallback_candidates(criteria)

    if not rows:
        log.warning("fetch_twse_sim_candidates: TWSE 回傳空資料 — 嘗試 fallback")
        return _fallback_candidates(criteria)

    name_map = _fetch_name_map()
    snapshots: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("Code", row.get("SecuritiesCode", ""))).strip()
        # 只保留 4 位純數字股票（排除 ETF 如 0050、特別股）
        if not code.isdigit() or len(code) != 4:
            continue
        try:
            _name_raw = (row.get("Name") or row.get("CompanyName") or row.get("name") or "")
            name = str(_name_raw).strip() or name_map.get(code) or code
            close  = float(row.get("ClosingPrice", 0) or 0)
            prev   = float(row.get("OpeningPrice",  0) or 0)
            change = float(row.get("Change", 0) or 0)
            volume = float(str(row.get("TradeVolume", "0")).replace(",", "") or 0) / 1000
            if close <= 0:
                continue
            prev_close = close - change if change != 0 else close
            change_pct = change / prev_close * 100 if prev_close > 0 else 0.0
            snapshots[code] = {
                "code":         code,
                "name":         name,
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
    if not candidates:
        log.warning("fetch_twse_sim_candidates: TWSE 篩選後無候選股 — 嘗試 fallback")
        return _fallback_candidates(criteria)

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
