"""Simulation position store: save/load/clear SimPosition records."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class SimPosition:
    code: str
    name: str
    entry_price: float
    shares: int          # 零股用（股數）
    quantity: int        # 整張用（張數）
    is_fractional: bool
    plan_type: str       # "aggressive" | "balanced" | "conservative"
    entry_date: str      # ISO format


def save_sim_positions(
    positions: list[SimPosition],
    path: str = "data/sim_positions.json",
) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = [asdict(pos) for pos in positions]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sim_positions(
    path: str = "data/sim_positions.json",
) -> list[SimPosition]:
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [SimPosition(**item) for item in data]


def clear_sim_positions(
    path: str = "data/sim_positions.json",
) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
