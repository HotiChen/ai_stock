from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from strategy_planner import PlanSet, StrategyPlan, StockPick

_DEFAULT_DIR = "data/sim_plans"


# ── Serialization ─────────────────────────────────────────────────────────────

def _pick_to_dict(p: StockPick) -> dict:
    return {
        "code": p.code, "name": p.name, "action": p.action,
        "quantity": p.quantity, "hold_days": p.hold_days,
        "expected_return_pct": p.expected_return_pct,
        "reason": p.reason, "confidence": p.confidence,
        "key_catalysts": p.key_catalysts,
        "entry_logic": p.entry_logic, "exit_logic": p.exit_logic,
        "is_fractional": p.is_fractional, "shares": p.shares,
    }


def _plan_to_dict(plan: StrategyPlan) -> dict:
    return {
        "plan_type": plan.plan_type, "description": plan.description,
        "picks": [_pick_to_dict(p) for p in plan.picks],
        "capital_deployed_pct": plan.capital_deployed_pct,
        "expected_return_pct": plan.expected_return_pct,
        "risk_note": plan.risk_note,
        "thesis": plan.thesis, "macro_context": plan.macro_context,
    }


def planset_to_dict(plan_set: PlanSet) -> dict:
    return {
        "aggressive":   _plan_to_dict(plan_set.aggressive),
        "balanced":     _plan_to_dict(plan_set.balanced),
        "conservative": _plan_to_dict(plan_set.conservative),
        "generated_at": plan_set.generated_at.isoformat(),
    }


def _pick_from_dict(d: dict) -> StockPick:
    return StockPick(
        code=d["code"], name=d["name"], action=d["action"],
        quantity=d["quantity"], hold_days=d["hold_days"],
        expected_return_pct=d["expected_return_pct"],
        reason=d["reason"], confidence=d["confidence"],
        key_catalysts=d.get("key_catalysts", []),
        entry_logic=d.get("entry_logic", ""),
        exit_logic=d.get("exit_logic", ""),
        is_fractional=d.get("is_fractional", False),
        shares=d.get("shares", 0),
    )


def _plan_from_dict(d: dict) -> StrategyPlan:
    return StrategyPlan(
        plan_type=d["plan_type"], description=d["description"],
        picks=[_pick_from_dict(p) for p in d["picks"]],
        capital_deployed_pct=d["capital_deployed_pct"],
        expected_return_pct=d["expected_return_pct"],
        risk_note=d["risk_note"],
        thesis=d.get("thesis", ""),
        macro_context=d.get("macro_context", ""),
    )


def planset_from_dict(data: dict) -> PlanSet:
    return PlanSet(
        aggressive=_plan_from_dict(data["aggressive"]),
        balanced=_plan_from_dict(data["balanced"]),
        conservative=_plan_from_dict(data["conservative"]),
        generated_at=date.fromisoformat(data["generated_at"]),
    )


# ── Day label helpers ─────────────────────────────────────────────────────────

def _max_day_number(plan_dir: Path) -> int:
    nums = []
    for f in plan_dir.glob("day*_*.json"):
        try:
            nums.append(int(f.stem.split("_")[0].replace("day", "")))
        except Exception:
            pass
    return max(nums) if nums else 0


# ── Public API ────────────────────────────────────────────────────────────────

def save_sim_plan(
    plan_set: PlanSet,
    plan_dir: str = _DEFAULT_DIR,
    day_label: Optional[str] = None,
) -> Path:
    """Save PlanSet as JSON. Auto-assigns day label if not given. Returns the saved path."""
    dirpath = Path(plan_dir)
    dirpath.mkdir(parents=True, exist_ok=True)
    if day_label is None:
        day_label = f"day{_max_day_number(dirpath) + 1}"
    filename = f"{day_label}_{plan_set.generated_at.isoformat()}.json"
    fpath = dirpath / filename
    fpath.write_text(
        json.dumps(planset_to_dict(plan_set), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return fpath


def load_sim_plan(filename: str, plan_dir: str = _DEFAULT_DIR) -> Optional[PlanSet]:
    """Load a PlanSet from a saved JSON file. Returns None on any error."""
    try:
        data = json.loads((Path(plan_dir) / filename).read_text(encoding="utf-8"))
        return planset_from_dict(data)
    except Exception:
        return None


def list_sim_plans(plan_dir: str = _DEFAULT_DIR) -> list[dict]:
    """Return saved plans sorted newest-first (by day number).
    Each entry: {filename, day_label, plan_date}
    """
    dirpath = Path(plan_dir)
    if not dirpath.exists():
        return []

    results = []
    for f in dirpath.glob("day*_*.json"):
        parts = f.stem.split("_", 1)
        try:
            day_num = int(parts[0].replace("day", ""))
        except Exception:
            day_num = 0
        results.append({
            "filename":  f.name,
            "day_label": parts[0],
            "plan_date": parts[1] if len(parts) >= 2 else "",
            "_day_num":  day_num,
        })

    results.sort(key=lambda x: x["_day_num"], reverse=True)
    for r in results:
        del r["_day_num"]
    return results


def delete_sim_plan(filename: str, plan_dir: str = _DEFAULT_DIR) -> None:
    """Delete a saved plan JSON. Raises FileNotFoundError if not found."""
    fpath = Path(plan_dir) / filename
    fpath.unlink()  # raises FileNotFoundError if missing
