from __future__ import annotations

"""
Risk guard: validates stock picks before execution.
- Ex-dividend filtering via TWSE OpenAPI
- Limit-up/down detection
- Blacklist
- Single position ≤5% capital
- Sector exposure ≤30% capital
"""

import requests
from typing import Optional

# ── Blacklist ─────────────────────────────────────────────────────────────────

_BLACKLIST: set[str] = {"BLACKTEST"}

TWSE_EX_DIVIDEND_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/TWT49U"
)

LIMIT_PCT = 0.10          # TWSE daily limit ±10%
MAX_POSITION_RATIO = 0.05  # single pick ≤5% of capital
MAX_SECTOR_RATIO   = 0.30  # sector total ≤30% of capital


# ── check_ex_dividend ─────────────────────────────────────────────────────────

def check_ex_dividend(stock_id: str) -> bool:
    """Return True if the stock has an upcoming ex-dividend event from TWSE OpenAPI."""
    try:
        resp = requests.get(TWSE_EX_DIVIDEND_URL, timeout=5)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return any(row.get("Code") == stock_id for row in data)
    except Exception:
        return False


# ── check_limit_up_down ───────────────────────────────────────────────────────

def check_limit_up_down(
    stock_id: str,
    current_price: float,
    prev_close: float,
) -> str:
    """Return 'limit_up', 'limit_down', or 'normal'."""
    if prev_close <= 0:
        return "normal"
    change_pct = (current_price - prev_close) / prev_close
    if change_pct >= LIMIT_PCT:
        return "limit_up"
    if change_pct <= -LIMIT_PCT:
        return "limit_down"
    return "normal"


# ── is_blacklisted ────────────────────────────────────────────────────────────

def is_blacklisted(stock_id: str) -> bool:
    return stock_id in _BLACKLIST


# ── validate_plan ─────────────────────────────────────────────────────────────

def validate_plan(
    picks: list[dict],
    capital: float,
    current_positions: list[dict],
) -> dict:
    """
    Validate and adjust a list of stock picks against risk rules.

    Each pick dict: {code, name, budget, sector, signal, confidence,
                     current_price (optional), prev_close (optional)}
    Each position dict: {code, sector, value}

    Returns: {approved: [...], rejected: [...]}
    Each approved pick may have budget reduced and a 'reason' note.
    Each rejected pick has a 'reason' field.
    """
    max_position = capital * MAX_POSITION_RATIO
    max_sector   = capital * MAX_SECTOR_RATIO

    # Build current sector exposure from existing positions
    sector_exposure: dict[str, float] = {}
    for pos in current_positions:
        s = pos.get("sector", "未知")
        sector_exposure[s] = sector_exposure.get(s, 0.0) + pos.get("value", 0.0)

    approved: list[dict] = []
    rejected: list[dict] = []

    for pick in picks:
        code   = pick["code"]
        sector = pick.get("sector", "未知")
        budget = pick.get("budget", 0.0)
        result = dict(pick)

        # 1. Blacklist
        if is_blacklisted(code):
            result["reason"] = "blacklisted"
            rejected.append(result)
            continue

        # 2. Ex-dividend
        if check_ex_dividend(code):
            result["reason"] = "ex_dividend"
            rejected.append(result)
            continue

        # 3. Limit-up/down (only if price fields present)
        current_price = pick.get("current_price")
        prev_close    = pick.get("prev_close")
        if current_price is not None and prev_close is not None:
            status = check_limit_up_down(code, current_price, prev_close)
            if status != "normal":
                result["reason"] = status
                rejected.append(result)
                continue

        # 4. Single position cap (reduce, not reject)
        if budget > max_position:
            result["budget"] = max_position
            result["reason"] = f"reduced: single position capped at {MAX_POSITION_RATIO*100:.0f}%"

        # 5. Sector cap (reduce or reject)
        current_sector = sector_exposure.get(sector, 0.0)
        remaining_room = max_sector - current_sector
        if remaining_room <= 0:
            result["reason"] = f"rejected: sector {sector} already at {MAX_SECTOR_RATIO*100:.0f}% limit"
            rejected.append(result)
            continue
        if result["budget"] > remaining_room:
            result["budget"] = remaining_room
            existing_reason = result.get("reason", "")
            sep = "; " if existing_reason else ""
            result["reason"] = existing_reason + sep + f"reduced: sector {sector} cap"

        # Update running sector total so subsequent picks respect it
        sector_exposure[sector] = current_sector + result["budget"]

        approved.append(result)

    return {"approved": approved, "rejected": rejected}
