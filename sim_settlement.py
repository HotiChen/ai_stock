"""Simulation settlement: calculate P&L and fetch closing prices."""
from __future__ import annotations

import threading
from typing import Any

from sim_position_store import SimPosition


def calc_position_pnl(pos: SimPosition, closing_price: float) -> float:
    """計算單一持倉損益。
    - 零股：(closing - entry) * shares
    - 整張：(closing - entry) * quantity * 1000
    """
    diff = closing_price - pos.entry_price
    if pos.is_fractional:
        return diff * pos.shares
    else:
        return diff * pos.quantity * 1000


def settle_positions(
    positions: list[SimPosition],
    price_map: dict[str, float],
) -> dict:
    """結算所有持倉。

    Returns:
        {
            "total_pnl": float,
            "total_pnl_pct": float,   # total_pnl / 總投入成本
            "per_stock": [
                {"code": ..., "name": ..., "entry_price": ...,
                 "pnl": ..., "closing_price": ...},
                ...
            ],
        }
    """
    per_stock: list[dict] = []
    total_pnl = 0.0
    total_cost = 0.0

    for pos in positions:
        closing = price_map.get(pos.code)
        if closing is None:
            continue

        pnl = calc_position_pnl(pos, closing)

        if pos.is_fractional:
            cost = pos.entry_price * pos.shares
        else:
            cost = pos.entry_price * pos.quantity * 1000

        total_pnl += pnl
        total_cost += cost
        per_stock.append({
            "code": pos.code,
            "name": pos.name,
            "entry_price": pos.entry_price,
            "pnl": pnl,
            "closing_price": closing,
        })

    total_pnl_pct = (total_pnl / total_cost) if total_cost != 0 else 0.0

    return {
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "per_stock": per_stock,
    }


def fetch_closing_prices(api: Any, codes: list[str]) -> dict[str, float]:
    """用 daemon thread 抓收盤價，timeout=6 秒，失敗或 timeout 回傳 {}。"""
    result: dict[str, float] = {}
    error_holder: list[Exception] = []

    def _fetch() -> None:
        try:
            snapshots = api.snapshots(codes)
            for snap in snapshots:
                result[snap.code] = snap.close
        except Exception as exc:  # noqa: BLE001
            error_holder.append(exc)

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(timeout=6)

    if t.is_alive() or error_holder:
        return {}

    return result
