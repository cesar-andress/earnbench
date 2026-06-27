"""Ensure bundled examples execute successfully."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "synthetic_visible_test_overfitting.py"
REPORT = EXAMPLE.with_name("synthetic_visible_test_overfitting.report.json")
ENV = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}


def test_synthetic_visible_test_overfitting_example_runs() -> None:
    if REPORT.exists():
        REPORT.unlink()

    result = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=ENV,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "earned_fraction:" in result.stdout

    assert REPORT.is_file()
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    assert payload["nominal_success"] is True
    assert payload["earned_fraction"] == pytest.approx(2 / 3)
    assert payload["valid_count"] == 3
    assert payload["successful_count"] == 2
    assert payload["failed_mechanisms"] == ["visible_test_removed"]
    assert set(payload["survived_mechanisms"]) == {
        "metadata_removed",
        "verifier_hardened",
    }
    assert "provenance" in payload
    assert payload["provenance"]["execution_uuid"]
