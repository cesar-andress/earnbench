"""Tests for Phase D analyst-facing failure categories."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.agents.schemas import AttemptRecord
from earnbench.outcomes import OutcomeStatus
from earnbench.phase_d_regrade import (
    AGENT_RESULTS_COLUMNS,
    build_agent_result_row,
    build_phase_d_summary,
    build_synthetic_agent_result_row,
    refresh_agent_result_failure_fields,
)
from earnbench.phase_d_diagnostics import (
    CellDiagnostics,
    FAILURE_MALFORMED_PATCH,
    FAILURE_NOMINAL_FAILED,
    FAILURE_PATCH_APPLY_FAILED,
    derive_phase_d_failure_fields,
    infer_nominal_failure_from_instance_dir,
)

INSTANCE_ID = "psf__requests-1724"


def _attempt(**overrides: object) -> AttemptRecord:
    base = {
        "agent": "ollama_devstral",
        "model": "devstral",
        "provider": "ollama",
        "instance_id": INSTANCE_ID,
        "replicate": 0,
        "seed": 1,
        "scaffold_id": "earnbench_phase_c_v1",
        "prompt_sha256": "abc",
        "patch_path": "patches/ollama_devstral/psf__requests-1724/replicate_0.patch",
        "patch_sha256": "def",
        "trajectory_log_ref": "",
        "status": "ok",
        "started_at_utc": "2026-01-01T00:00:00Z",
        "completed_at_utc": "2026-01-01T00:00:01Z",
        "error": "",
    }
    base.update(overrides)
    return AttemptRecord.from_dict(base)


def test_derive_malformed_patch_from_validate(tmp_path: Path) -> None:
    missing = tmp_path / "missing.patch"
    diagnostics = CellDiagnostics()
    diagnostics.record(
        "validate",
        status="failed",
        failure_reason=FAILURE_MALFORMED_PATCH,
        detail=f"patch file not found: {missing}",
    )
    category, reason = derive_phase_d_failure_fields(
        attempt_status="ok",
        patch_path=str(missing),
        diagnostics=diagnostics,
    )
    assert category == "malformed_patch"
    assert "patch file not found" in reason


def test_infer_patch_apply_failure_from_harness_log(tmp_path: Path) -> None:
    instance_dir = tmp_path / INSTANCE_ID
    nominal_dir = instance_dir / "nominal"
    nominal_dir.mkdir(parents=True)
    (nominal_dir / "grade.json").write_text(
        json.dumps({"status": OutcomeStatus.INVALID.value, "success": False}) + "\n",
        encoding="utf-8",
    )
    (nominal_dir / "harness.log").write_text(
        "Failed to apply patch to container: git apply --verbose\n"
        "patch: **** malformed patch at line 51: +\n",
        encoding="utf-8",
    )
    category, reason = infer_nominal_failure_from_instance_dir(instance_dir) or ("", "")
    assert category == "malformed_patch"
    assert "line 51" in reason


def test_infer_patch_apply_failed_no_file_to_patch(tmp_path: Path) -> None:
    instance_dir = tmp_path / INSTANCE_ID
    nominal_dir = instance_dir / "nominal"
    nominal_dir.mkdir(parents=True)
    (nominal_dir / "grade.json").write_text(
        json.dumps({"status": OutcomeStatus.ERROR.value, "message": "boom"}) + "\n",
        encoding="utf-8",
    )
    (nominal_dir / "harness.log").write_text(
        "patch: **** unexpected end of file in patch\n"
        "Failed to apply patch to container: patch --batch\n",
        encoding="utf-8",
    )
    category, reason = infer_nominal_failure_from_instance_dir(instance_dir) or ("", "")
    assert category == "patch_apply_failed"
    assert "patch apply failed" in reason


def test_build_agent_result_row_nominal_failed(tmp_path: Path) -> None:
    attempt = _attempt()
    diagnostics = CellDiagnostics()
    finalize_kwargs = {"aggregated": True, "y0": False}
    from earnbench.phase_d_diagnostics import finalize_cell_diagnostics

    finalize_cell_diagnostics(diagnostics, **finalize_kwargs)
    row = build_synthetic_agent_result_row(
        attempt=attempt,
        run_id="phase_d_test",
        diagnostics=diagnostics,
    )
    assert row["phase_d_failure_category"] == "nominal_failed"
    assert "nominal" in row["phase_d_failure_reason"]


def test_build_agent_result_row_success_has_none_category() -> None:
    attempt = _attempt()
    diagnostics = CellDiagnostics(grade_status="ok")
    csv_row = {
        "y0": True,
        "y_vtest": True,
        "y_verif": True,
        "y_env": True,
        "pi_vtest_status": "ok",
        "pi_verif_status": "ok",
        "pi_env_status": "ok",
        "valid_pi_count": 3,
        "ef_pi": 1.0,
        "ef_exclude_invalid": 1.0,
        "ef_invalid_as_fail": 1.0,
        "invalid_pi_count": 0,
        "invalid_pi_rate": 0.0,
        "ef_sensitivity_gap": 0.0,
        "ef_status": "defined",
        "run_id": "phase_d_test",
        "config_digest": "digest",
    }
    row = build_agent_result_row(
        attempt=attempt,
        csv_row=csv_row,
        report_payload={"failed_mechanisms": []},
        diagnostics=diagnostics,
    )
    assert row["phase_d_failure_category"] == "none"
    assert row["phase_d_failure_reason"] == ""
    assert row["ef_status"] == "defined"


def test_build_agent_result_row_perturbation_invalid() -> None:
    attempt = _attempt()
    diagnostics = CellDiagnostics(grade_status="partial")
    csv_row = {
        "y0": True,
        "y_vtest": "",
        "y_verif": "",
        "y_env": "",
        "pi_vtest_status": "ok",
        "pi_verif_status": "invalid",
        "pi_env_status": "ok",
        "valid_pi_count": 2,
        "ef_pi": 0.6666666666666666,
        "ef_exclude_invalid": 0.6666666666666666,
        "ef_invalid_as_fail": 0.6666666666666666,
        "invalid_pi_count": 1,
        "invalid_pi_rate": 0.3333333333333333,
        "ef_sensitivity_gap": 0.0,
        "ef_status": "defined",
        "run_id": "phase_d_test",
        "config_digest": "digest",
    }
    row = build_agent_result_row(
        attempt=attempt,
        csv_row=csv_row,
        report_payload={"failed_mechanisms": ["verifier_tampering"]},
        diagnostics=diagnostics,
    )
    assert row["phase_d_failure_category"] == "perturbation_invalid"
    assert "pi_verif" in row["phase_d_failure_reason"]


def test_refresh_agent_result_failure_fields_from_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "phase_d"
    instance_dir = output_dir / "cells/ollama_devstral" / INSTANCE_ID
    nominal_dir = instance_dir / "nominal"
    nominal_dir.mkdir(parents=True)
    (nominal_dir / "harness.log").write_text(
        "patch: **** malformed patch at line 12: +\n",
        encoding="utf-8",
    )
    (nominal_dir / "grade.json").write_text(
        json.dumps({"status": OutcomeStatus.INVALID.value}) + "\n",
        encoding="utf-8",
    )
    key = "ollama_devstral:psf__requests-1724:r0"
    rows = {
        key: {
            col: ""
            for col in AGENT_RESULTS_COLUMNS
        }
    }
    rows[key].update(
        {
            "agent": "ollama_devstral",
            "instance_id": INSTANCE_ID,
            "replicate": 0,
            "attempt_status": "ok",
            "patch_path": "p.patch",
            "y0": False,
            "ef_status": "undefined",
        }
    )
    refreshed = refresh_agent_result_failure_fields(output_dir, rows)
    assert refreshed[key]["phase_d_failure_category"] == "malformed_patch"
    assert "line 12" in refreshed[key]["phase_d_failure_reason"]


def test_build_phase_d_summary_counts() -> None:
    rows = {
        "a": {
            "phase_d_failure_category": "malformed_patch",
            "y0": False,
            "ef_status": "undefined",
        },
        "b": {
            "phase_d_failure_category": "none",
            "y0": True,
            "ef_status": "defined",
        },
        "c": {
            "phase_d_failure_category": "nominal_failed",
            "y0": False,
            "ef_status": "undefined",
        },
    }
    summary = build_phase_d_summary(
        rows,
        run_id="phase_d_test",
        graded_count=3,
        failure_count=0,
        skipped_ineligible_count=0,
        by_agent={},
        by_failure_reason={},
        summarized_at_utc="2026-01-01T00:00:00Z",
    )
    payload = summary.to_dict()
    assert payload["counts_by_failure_category"]["malformed_patch"] == 1
    assert payload["y0_pass_count"] == 1
    assert payload["ef_defined_count"] == 1
    assert payload["nominal_failed_count"] == 1
