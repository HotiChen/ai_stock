from __future__ import annotations

from dataclasses import dataclass, field

# ── 題材分組（預定義，可自行擴充）────────────────────────────────────────────

THEME_GROUPS: dict[str, list[str]] = {
    "AI 人工智慧":   ["2330", "2317", "2454", "3711", "6770", "2379", "3034"],
    "半導體製造":    ["2330", "2303", "2338", "3711", "5347", "2408", "2049"],
    "IC 設計":       ["2454", "3034", "2379", "6415", "3533", "2344", "3532"],
    "電動車":        ["2303", "6285", "1590", "2049", "6269", "3017", "2023"],
    "伺服器 / 雲端": ["2317", "3231", "6669", "3008", "2356", "3706", "6533"],
    "網通 / 5G":     ["2345", "3257", "4736", "6515", "3413", "2342", "3047"],
    "面板":          ["2409", "3481", "8150", "6491", "2426", "5007"],
    "被動元件":      ["2327", "2330", "2351", "6239", "5285", "3189"],
    "金融保險":      ["2881", "2882", "2883", "2884", "2886", "2887", "2891"],
    "航運":          ["2603", "2609", "2615", "2618", "2610", "5608", "2605"],
    "鋼鐵":          ["2002", "2006", "2008", "2015", "9910", "2007"],
    "生技醫療":      ["4966", "6547", "1786", "4174", "6509", "4126", "1760"],
    "ETF 熱門":      ["0050", "0056", "00631L", "00878", "00919", "00929"],
    "台積電供應鏈":  ["3711", "6770", "2379", "3034", "6415", "5347", "2049"],
    "電子零組件":    ["2308", "3008", "2382", "6239", "3189", "2327", "2357"],
}


# ── 資料結構 ──────────────────────────────────────────────────────────────────

@dataclass
class ThemeStats:
    name: str
    stocks: list[str]
    avg_change_pct: float
    advance_count: int
    decline_count: int
    total_volume: int
    strongest_stock: str
    weakest_stock: str
    stock_count: int


# ── 核心邏輯 ──────────────────────────────────────────────────────────────────

def calc_theme_stats(
    theme_name: str,
    stock_codes: list[str],
    snapshots: dict[str, dict],
) -> ThemeStats:
    """Aggregate snapshot data across stocks in a theme."""
    valid = {code: snapshots[code] for code in stock_codes if code in snapshots}

    if not valid:
        return ThemeStats(
            name=theme_name, stocks=stock_codes,
            avg_change_pct=0.0, advance_count=0, decline_count=0,
            total_volume=0, strongest_stock="", weakest_stock="",
            stock_count=0,
        )

    changes = {code: snap["change_rate"] for code, snap in valid.items()}
    avg_change = sum(changes.values()) / len(changes)
    advance = sum(1 for r in changes.values() if r > 0)
    decline = sum(1 for r in changes.values() if r < 0)
    total_vol = sum(snap["total_volume"] for snap in valid.values())
    strongest = max(changes, key=changes.get)
    weakest = min(changes, key=changes.get)

    return ThemeStats(
        name=theme_name,
        stocks=stock_codes,
        avg_change_pct=round(avg_change, 2),
        advance_count=advance,
        decline_count=decline,
        total_volume=total_vol,
        strongest_stock=strongest,
        weakest_stock=weakest,
        stock_count=len(valid),
    )


def get_theme_detail_rows(
    theme_name: str,
    stock_codes: list[str],
    snapshots: dict[str, dict],
) -> list[dict]:
    """Return per-stock rows for a theme.

    Rows with snapshot data come first, sorted by change_rate desc.
    Rows without data follow at the end (change_rate = 0, has_data = False).
    """
    with_data = []
    without_data = []
    for code in stock_codes:
        snap = snapshots.get(code)
        if snap:
            with_data.append({
                "code":         code,
                "name":         snap.get("name", code),
                "close":        snap.get("close", 0.0),
                "change_rate":  snap.get("change_rate", 0.0),
                "change_price": snap.get("change_price", 0.0),
                "total_volume": snap.get("total_volume", 0),
                "has_data":     True,
            })
        else:
            without_data.append({
                "code":         code,
                "name":         code,
                "close":        0.0,
                "change_rate":  0.0,
                "change_price": 0.0,
                "total_volume": 0,
                "has_data":     False,
            })
    with_data.sort(key=lambda r: r["change_rate"], reverse=True)
    return with_data + without_data


def rank_themes(theme_stats: list[ThemeStats]) -> list[ThemeStats]:
    """Sort themes by avg_change_pct descending (hottest first)."""
    return sorted(theme_stats, key=lambda t: t.avg_change_pct, reverse=True)


def get_theme_momentum_label(avg_change_pct: float) -> str:
    """Return emoji + text label based on theme's average change."""
    if avg_change_pct >= 3:
        return "🔥 強漲"
    elif avg_change_pct >= 1:
        return "↑ 上漲"
    elif avg_change_pct >= -0.5:
        return "→ 持平"
    elif avg_change_pct >= -2:
        return "↓ 下跌"
    else:
        return "❄️ 重跌"
