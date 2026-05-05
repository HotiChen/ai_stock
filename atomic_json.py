"""Atomic JSON read/write helpers.

atomic_write_json writes to a .tmp file then os.replace() to avoid partial writes.
Placed at project root so Streamlit and all scripts can import without CWD issues.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path: str, data, indent: int = 2) -> None:
    """Write *data* to *path* atomically (via a .tmp file)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, str(p))


def atomic_read_json(path: str):
    """Read and parse JSON from *path*. Returns None if file missing or corrupt."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
