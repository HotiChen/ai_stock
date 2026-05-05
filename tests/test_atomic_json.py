"""Tests for utils.atomic_json — atomic read/write helpers."""
from __future__ import annotations

import json

import pytest

from atomic_json import atomic_read_json, atomic_write_json


def test_write_and_read_back(tmp_path):
    path = tmp_path / "data.json"
    atomic_write_json(str(path), {"key": "value"})
    result = atomic_read_json(str(path))
    assert result == {"key": "value"}


def test_no_tmp_file_left_after_write(tmp_path):
    path = tmp_path / "data.json"
    atomic_write_json(str(path), {"x": 1})
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_read_nonexistent_returns_none(tmp_path):
    result = atomic_read_json(str(tmp_path / "missing.json"))
    assert result is None


def test_write_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "deep" / "data.json"
    atomic_write_json(str(path), {"ok": True})
    assert json.loads(path.read_text())["ok"] is True
