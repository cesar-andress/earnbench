"""Tests for SWE-bench pi_verif.v1 execution (mocked harness)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.adapters.swebench_nominal import NominalRunRequest, NominalRunResult
from earnbench.adapters.swebench_patch import extract_prod_patch, sha256_hex
from earnbench.adapters.swebench_pi_verif import (
    PI_VERIF_ARTIFACT_DIR,
    analyze_pi_verif_patch,
    patch_touches_protected_paths,
    resolve_pristine_test_sha256,
    run_pi_verif_grading,
)
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
INSTANCE_ID = "psf__requests-1724"

GRADE_FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "perturbation_id",
    "success",
    "status",
    "protected_paths",
    "stripped_paths",
    "tamper_detected",
    "timeout_seconds",
    "started_at_utc",
    "completed_at_utc",
    "harness_command",
    "log_ref",
)


def _mock_runner(request: NominalRunRequest) -> NominalRunResult:
    return NominalRunResult(
        success=True,
        status=AuditStatus.OK.value,
        harness_command="/bin/bash /eval.sh",
        log_text="mock pi_verif harness log\n",
        tests_run=("tests.test_models.TestCase.test_redirect",),
        warnings=(),
        started_at_utc="2025-06-01T12:00:00+00:00",
        completed_at_utc="2025-06-01T12:05:00+00:00",
        patch_sha256=sha256_hex(request.patch_content),
    )


def test_patch_touches_protected_paths_on_golden_patch() -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    touched = patch_touches_protected_paths(record.golden_patch)
    assert "tests/test_models.py" in touched


def test_analyze_pi_verif_clean_prod_patch() -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    prod = extract_prod_patch(record.golden_patch)
    analysis = analyze_pi_verif_patch(prod.prod_patch, record=record)
    assert analysis.tamper_detected is False
    assert analysis.touched_protected_paths == ()
    assert analysis.pristine_test_sha256 is not None


def test_run_pi_verif_skips_harness_when_tamper_detected(tmp_path: Path) -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    patch_path = tmp_path / "raw.patch"
    patch_path.write_text(record.golden_patch, encoding="utf-8")

    grade = run_pi_verif_grading(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=patch_path,
        output_dir=tmp_path,
        runner=_mock_runner,
    )

    artifact_dir = tmp_path / INSTANCE_ID / PI_VERIF_ARTIFACT_DIR
    assert set(GRADE_FIELDS) <= set(grade.keys())
    assert grade["perturbation_id"] == PI_VERIF_V1_ID
    assert grade["tamper_detected"] is True
    assert grade["success"] is False
    assert grade["status"] == AuditStatus.OK.value
    assert grade["stripped_paths"] == ["tests/test_models.py"]
    assert grade["harness_command"] == "skipped: protected_path_tamper"

    audit = AuditRecord.from_dict(
        json.loads((artifact_dir / "audit.json").read_text(encoding="utf-8"))
    )
    assert audit.perturbation_id == PI_VERIF_V1_ID
    assert audit.status is AuditStatus.OK
    assert audit.success is False
    assert audit.pristine_test_sha256 == resolve_pristine_test_sha256(record)
    assert any("verifier tampering" in warning for warning in audit.warnings)
    assert "provenance" in audit.to_dict()
    assert "mock pi_verif harness log" not in (artifact_dir / "harness.log").read_text(
        encoding="utf-8"
    )


def test_run_pi_verif_runs_harness_for_clean_prod_patch(tmp_path: Path) -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    prod = extract_prod_patch(record.golden_patch)
    patch_path = tmp_path / "prod_only.patch"
    patch_path.write_text(prod.prod_patch, encoding="utf-8")

    grade = run_pi_verif_grading(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=patch_path,
        output_dir=tmp_path,
        runner=_mock_runner,
    )

    artifact_dir = tmp_path / INSTANCE_ID / PI_VERIF_ARTIFACT_DIR
    assert grade["tamper_detected"] is False
    assert grade["success"] is True
    assert grade["status"] == AuditStatus.OK.value
    assert (artifact_dir / "harness.log").read_text(encoding="utf-8") == (
        "mock pi_verif harness log\n"
    )

    audit = AuditRecord.from_dict(
        json.loads((artifact_dir / "audit.json").read_text(encoding="utf-8"))
    )
    assert audit.success is True
    assert audit.status is AuditStatus.OK


def test_run_pi_verif_rejects_empty_patch(tmp_path: Path) -> None:
    patch_path = tmp_path / "empty.patch"
    patch_path.write_text("  \n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        run_pi_verif_grading(
            metadata_path=METADATA_FIXTURE,
            instance_id=INSTANCE_ID,
            patch_path=patch_path,
            output_dir=tmp_path,
            runner=_mock_runner,
        )
