"""Tests for Phase C′ pilot manifest validation."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from earnbench.cli import main
from earnbench.phase_c_prime import validate_phase_c_prime_manifest

HEADER = (
    "agent",
    "model",
    "provider",
    "instance_id",
    "replicate_count",
    "temperature",
    "seed_policy",
    "difficulty_bin",
    "patch_loc",
    "files_touched",
    "notes",
)


def _write_manifest(path: Path, rows: list[tuple[str, ...]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)


def _valid_row(instance_id: str = "i1") -> tuple[str, ...]:
    return (
        "pilot",
        "m1",
        "p1",
        instance_id,
        "30",
        "0.2",
        "replicate_index_sha256",
        "easy",
        "src/",
        "1",
        "ok",
    )


def test_valid_manifest_passes(tmp_path: Path) -> None:
    path = tmp_path / "manifest.csv"
    _write_manifest(path, [_valid_row("i1"), _valid_row("i2")])
    result = validate_phase_c_prime_manifest(path)
    assert result.ok
    assert result.row_count == 2
    assert result.agent_count == 1
    assert result.instance_count == 2
    assert result.total_attempts == 60


def test_duplicate_agent_instance_rejected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.csv"
    _write_manifest(path, [_valid_row("i1"), _valid_row("i1")])
    result = validate_phase_c_prime_manifest(path)
    assert not result.ok
    assert any("duplicate agent-instance" in error for error in result.errors)


def test_bad_replicate_count_rejected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.csv"
    row = list(_valid_row())
    row[4] = "0"
    _write_manifest(path, [tuple(row)])
    result = validate_phase_c_prime_manifest(path)
    assert not result.ok
    assert any("replicate_count" in error for error in result.errors)


def test_missing_seed_policy_rejected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.csv"
    row = list(_valid_row())
    row[6] = ""
    _write_manifest(path, [tuple(row)])
    result = validate_phase_c_prime_manifest(path)
    assert not result.ok
    assert any("seed_policy" in error for error in result.errors)


def test_cli_validate_manifest(capsys, tmp_path: Path) -> None:
    path = tmp_path / "manifest.csv"
    _write_manifest(path, [_valid_row("i1")])
    exit_code = main(["phase-c-prime", "validate-manifest", str(path)])
    assert exit_code == 0
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["total_attempts"] == 30
