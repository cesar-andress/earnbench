"""Tests for Phase D diagnostics taxonomy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.outcomes import OutcomeStatus
from earnbench.phase_d_diagnostics import (
    FAILURE_BUILD_FAILED,
    FAILURE_EMPTY_PATCH,
    FAILURE_HARNESS_ERROR,
    FAILURE_PATCH_FILE_NOT_FOUND,
    FAILURE_NOMINAL_FAILED,
    FAILURE_PATCH_APPLY_FAILED,
    FAILURE_PERTURBATION_FAILED,
    FAILURE_TIMEOUT,
    CellDiagnostics,
    classify_grade_artifact,
    classify_post_stage,
    classify_preflight_artifact,
    classify_prepare_error,
    classify_validate_patch,
    finalize_cell_diagnostics,
    summarize_failure_reasons,
)


def test_classify_validate_patch_empty_and_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.patch"
    reason, detail = classify_validate_patch(patch_path=missing) or ("", "")
    assert reason == FAILURE_PATCH_FILE_NOT_FOUND
    assert "not found" in detail

    empty = tmp_path / "empty.patch"
    empty.write_text("\n", encoding="utf-8")
    reason, detail = classify_validate_patch(patch_path=empty) or ("", "")
    assert reason == FAILURE_EMPTY_PATCH


def test_classify_prepare_error_empty_patch() -> None:
    reason, _ = classify_prepare_error(ValueError("patch file is empty"))
    assert reason == FAILURE_EMPTY_PATCH


def test_classify_preflight_build_failed(tmp_path: Path) -> None:
    instance_dir = tmp_path / "inst"
    instance_dir.mkdir()
    (instance_dir / "preflight.json").write_text(
        json.dumps({"status": "build_failed", "message": "docker build failed"}) + "\n",
        encoding="utf-8",
    )
    reason, detail = classify_preflight_artifact(instance_dir) or ("", "")
    assert reason == FAILURE_BUILD_FAILED
    assert "docker build failed" in detail


def test_classify_nominal_grade_outcomes(tmp_path: Path) -> None:
    instance_dir = tmp_path / "inst"
    nominal_dir = instance_dir / "nominal"
    nominal_dir.mkdir(parents=True)

    (nominal_dir / "grade.json").write_text(
        json.dumps(
            {
                "status": OutcomeStatus.OK.value,
                "success": False,
                "message": "tests failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    reason, _ = classify_grade_artifact(
        stage="nominal",
        instance_dir=instance_dir,
        artifact_subdir="nominal",
    ) or ("", "")
    assert reason == FAILURE_NOMINAL_FAILED

    (nominal_dir / "grade.json").write_text(
        json.dumps({"status": OutcomeStatus.ERROR.value, "message": "timed out"}) + "\n",
        encoding="utf-8",
    )
    (nominal_dir / "harness.log").write_text("Process timed out\n", encoding="utf-8")
    reason, _ = classify_grade_artifact(
        stage="nominal",
        instance_dir=instance_dir,
        artifact_subdir="nominal",
    ) or ("", "")
    assert reason == FAILURE_TIMEOUT

    (nominal_dir / "grade.json").write_text(
        json.dumps({"status": OutcomeStatus.ERROR.value, "message": "boom"}) + "\n",
        encoding="utf-8",
    )
    (nominal_dir / "harness.log").write_text(
        "Failed to apply patch to repository\n",
        encoding="utf-8",
    )
    reason, _ = classify_grade_artifact(
        stage="nominal",
        instance_dir=instance_dir,
        artifact_subdir="nominal",
    ) or ("", "")
    assert reason == FAILURE_PATCH_APPLY_FAILED


def test_classify_pi_stage_maps_to_perturbation_failed(tmp_path: Path) -> None:
    instance_dir = tmp_path / "inst"
    pi_dir = instance_dir / "pi_verif.v1"
    pi_dir.mkdir(parents=True)
    (pi_dir / "grade.json").write_text(
        json.dumps({"status": OutcomeStatus.ERROR.value, "message": "executor error"}) + "\n",
        encoding="utf-8",
    )
    reason, _ = classify_post_stage(stage="pi_verif.v1", instance_dir=instance_dir) or ("", "")
    assert reason == FAILURE_PERTURBATION_FAILED


def test_finalize_cell_diagnostics_success_and_nominal_failed() -> None:
    diagnostics = CellDiagnostics()
    finalize_cell_diagnostics(diagnostics, aggregated=True, y0=True)
    assert diagnostics.grade_status == "ok"
    assert diagnostics.failure_reason == ""

    diagnostics = CellDiagnostics()
    finalize_cell_diagnostics(diagnostics, aggregated=True, y0=False)
    assert diagnostics.grade_status == "ok"
    assert diagnostics.failure_reason == FAILURE_NOMINAL_FAILED


def test_summarize_failure_reasons_counts() -> None:
    rows = {
        "a": {"failure_reason": ""},
        "b": {"failure_reason": FAILURE_HARNESS_ERROR},
        "c": {"failure_reason": FAILURE_HARNESS_ERROR},
    }
    counts = summarize_failure_reasons(rows)
    assert counts[FAILURE_HARNESS_ERROR] == 2
    assert counts[""] == 1
