from __future__ import annotations


def normalize_snapshot_row(code: str, snap: dict) -> dict:
    """Standardise a snapshot dict into a flat row."""
    return {
        "code":         code,
        "name":         snap.get("name", code),
        "close":        snap.get("close", 0.0),
        "change_rate":  snap.get("change_rate", 0.0),
        "change_price": snap.get("change_price", 0.0),
        "total_volume": snap.get("total_volume", 0),
    }


def _to_rows(snapshots: dict[str, dict]) -> list[dict]:
    return [normalize_snapshot_row(code, snap) for code, snap in snapshots.items()]


def get_top_gainers(snapshots: dict[str, dict], n: int = 10) -> list[dict]:
    rows = _to_rows(snapshots)
    return sorted(rows, key=lambda r: r["change_rate"], reverse=True)[:n]


def get_top_losers(snapshots: dict[str, dict], n: int = 10) -> list[dict]:
    rows = _to_rows(snapshots)
    return sorted(rows, key=lambda r: r["change_rate"])[:n]


def get_top_volume(snapshots: dict[str, dict], n: int = 10) -> list[dict]:
    rows = _to_rows(snapshots)
    return sorted(rows, key=lambda r: r["total_volume"], reverse=True)[:n]


def search_stocks(snapshots: dict[str, dict], query: str) -> list[dict]:
    rows = _to_rows(snapshots)
    if not query:
        return rows
    q = query.strip().lower()
    return [r for r in rows if q in r["code"].lower() or q in r["name"].lower()]


def batch_fetch_snapshots(api, codes: list[str], batch_size: int = 100) -> dict[str, dict]:
    """Fetch snapshots for a large list of codes in batches."""
    result = {}
    for i in range(0, len(codes), batch_size):
        batch_codes = codes[i: i + batch_size]
        contracts = [api.Contracts.Stocks.get(c) for c in batch_codes]
        contracts = [c for c in contracts if c is not None]
        if not contracts:
            continue
        # 從合約物件取名稱（contract.name 就是中文股名）
        name_map = {c.code: getattr(c, "name", c.code) for c in contracts}
        try:
            snaps = api.snapshots(contracts)
            for snap in snaps:
                result[snap.code] = {
                    "name":         name_map.get(snap.code, snap.code),
                    "close":        snap.close,
                    "change_rate":  snap.change_rate,
                    "change_price": snap.change_price,
                    "total_volume": snap.total_volume,
                    "open":         snap.open,
                    "high":         snap.high,
                    "low":          snap.low,
                }
        except Exception:
            pass
    return result
