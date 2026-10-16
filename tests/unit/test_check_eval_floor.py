"""Unit tests for scripts/check_eval_floor.py (CI offline-eval gate)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_eval_floor.py"
_spec = importlib.util.spec_from_file_location("check_eval_floor", _SCRIPT)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _write(tmp_path: Path, correct: int, total: int) -> Path:
    path = tmp_path / "poc_accuracy.json"
    path.write_text(json.dumps({"correct": correct, "total": total}), encoding="utf-8")
    return path


def test_passes_at_floor(tmp_path: Path) -> None:
    path = _write(tmp_path, 21, 31)
    assert gate.main([str(path), "--min-correct", "21", "--total", "31"]) == 0


def test_fails_below_floor(tmp_path: Path) -> None:
    path = _write(tmp_path, 20, 31)
    assert gate.main([str(path), "--min-correct", "21"]) == 1


def test_fails_on_case_count_drift() -> None:
    errors = gate.check({"correct": 25, "total": 30}, min_correct=21, total=31)
    assert errors == ["expected 31 cases, got 30"]
