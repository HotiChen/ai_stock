from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from utils.atomic_json import atomic_write_json

_DEFAULT_DIR = "data/daily_tracking"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class PlanResult:
    plan_type:       str
    pnl:             Optional[float]   # None = not yet settled
    pnl_pct:         Optional[float]
    closing_capital: Optional[float]
    win_trades:      int = 0
    loss_trades:     int = 0
    is_actual:       bool = False      # True = user chose this plan


@dataclass
class DailyTrackRecord:
    date:              date
    starting_capital:  float
    chosen_plan_type:  str             # "aggressive" | "balanced" | "conservative"
    results:           list[PlanResult] = field(default_factory=list)


# ── Serialization ─────────────────────────────────────────────────────────────

def _result_to_dict(r: PlanResult) -> dict:
    return {
        "plan_type":       r.plan_type,
        "pnl":             r.pnl,
        "pnl_pct":         r.pnl_pct,
        "closing_capital": r.closing_capital,
        "win_trades":      r.win_trades,
        "loss_trades":     r.loss_trades,
        "is_actual":       r.is_actual,
    }


def _result_from_dict(d: dict) -> PlanResult:
    return PlanResult(
        plan_type=d["plan_type"],
        pnl=d.get("pnl"),
        pnl_pct=d.get("pnl_pct"),
        closing_capital=d.get("closing_capital"),
        win_trades=d.get("win_trades", 0),
        loss_trades=d.get("loss_trades", 0),
        is_actual=d.get("is_actual", False),
    )


def record_to_dict(r: DailyTrackRecord) -> dict:
    return {
        "date":             r.date.isoformat(),
        "starting_capital": r.starting_capital,
        "chosen_plan_type": r.chosen_plan_type,
        "results":          [_result_to_dict(res) for res in r.results],
    }


def record_from_dict(d: dict) -> DailyTrackRecord:
    return DailyTrackRecord(
        date=date.fromisoformat(d["date"]),
        starting_capital=d["starting_capital"],
        chosen_plan_type=d["chosen_plan_type"],
        results=[_result_from_dict(r) for r in d.get("results", [])],
    )


# ── Public API ────────────────────────────────────────────────────────────────

def _fpath(d: date, track_dir: str) -> Path:
    return Path(track_dir) / f"{d.isoformat()}.json"


def save_day_record(
    record: DailyTrackRecord,
    track_dir: str = _DEFAULT_DIR,
) -> Path:
    """Save (or overwrite) today's tracking record. Returns the saved path."""
    fpath = _fpath(record.date, track_dir)
    atomic_write_json(str(fpath), record_to_dict(record))
    return fpath


def load_day_record(
    day: date,
    track_dir: str = _DEFAULT_DIR,
) -> Optional[DailyTrackRecord]:
    """Load a day's record. Returns None if missing or corrupt."""
    try:
        data = json.loads(_fpath(day, track_dir).read_text(encoding="utf-8"))
        return record_from_dict(data)
    except Exception:
        return None


def list_day_records(track_dir: str = _DEFAULT_DIR) -> list[DailyTrackRecord]:
    """Return all records sorted newest-first."""
    dirpath = Path(track_dir)
    if not dirpath.exists():
        return []
    records = []
    for f in dirpath.glob("????-??-??.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            records.append(record_from_dict(data))
        except Exception:
            pass
    records.sort(key=lambda r: r.date, reverse=True)
    return records


def update_plan_result(
    day: date,
    plan_type: str,
    pnl: float,
    pnl_pct: float,
    closing_capital: float,
    win_trades: int,
    loss_trades: int,
    track_dir: str = _DEFAULT_DIR,
) -> None:
    """Update one plan's result in an existing day record. No-op if record missing."""
    record = load_day_record(day, track_dir)
    if record is None:
        return
    for res in record.results:
        if res.plan_type == plan_type:
            res.pnl             = pnl
            res.pnl_pct         = pnl_pct
            res.closing_capital = closing_capital
            res.win_trades      = win_trades
            res.loss_trades     = loss_trades
            break
    save_day_record(record, track_dir)


def delete_day_record(day: date, track_dir: str = _DEFAULT_DIR) -> None:
    """Delete a day's tracking record. Raises FileNotFoundError if not found."""
    _fpath(day, track_dir).unlink()  # raises FileNotFoundError if missing
