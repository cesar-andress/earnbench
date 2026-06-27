"""Tests for SWE-bench pi_vtest.v1 execution (mocked harness)."""

from __future__ import annotations

import json
from pathlib import Path

from earnbench.adapters.swebench import holdout_partition
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.adapters.swebench_nominal import NominalRunRequest, NominalRunResult
from earnbench.adapters.swebench_patch import extract_prod_patch, sha256_hex
from earnbench.adapters.swebench_pi_vtest import (
    PI_VTEST_ARTIFACT_DIR,
    resolve_pi_vtest_partition,
    run_pi_vtest_grading,
)
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1_ID

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
    "outcome",
    "holdout_salt",
    "holdout_k",
    "holdout_f2p",
    "visible_f2p",
    "graded_f2p",
    "pass_to_pass",
    "include_visible_f2p",
    "pi_vtest_viable",
    "timeout_seconds",
    "started_at_utc",
    "completed_at_utc",
    "harness_command",
    "log_ref",
)


def _mock_runner(request: NominalRunRequest) -> NominalRunResult:
    row = request.instance_row
    fail_to_pass = json.loads(row["FAIL_TO_PASS"])
    pass_to_pass = json.loads(row.get("PASS_TO_PASS") or "[]")
    return NominalRunResult(
        success=True,
        status=AuditStatus.OK.value,
        harness_command="/bin/bash /eval.sh",
        log_text="mock pi_vtest harness log\n",
        tests_run=tuple(fail_to_pass) + tuple(pass_to_pass),
        warnings=(),
        started_at_utc="2025-06-01T12:00:00+00:00",
        completed_at_utc="2025-06-01T12:05:00+00:00",
        patch_sha256=sha256_hex(request.patch_content),
    )


def test_resolve_pi_vtest_partition_for_fixture() -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    partition = resolve_pi_vtest_partition(record)
    assert partition.viable is True
    assert partition.holdout_f2p
    assert partition.visible_f2p
    assert set(partition.holdout_f2p).isdisjoint(partition.visible_f2p)
    assert set(partition.graded_f2p) == set(record.fail_to_pass)


def test_run_pi_vtest_grading_writes_artifacts(tmp_path: Path) -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    prod = extract_prod_patch(record.golden_patch)
    patch_path = tmp_path / "prod_only.patch"
    patch_path.write_text(prod.prod_patch, encoding="utf-8")

    grade = run_pi_vtest_grading(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=patch_path,
        output_dir=tmp_path,
        runner=_mock_runner,
    )

    artifact_dir = tmp_path / INSTANCE_ID / PI_VTEST_ARTIFACT_DIR
    assert set(GRADE_FIELDS) <= set(grade.keys())
    assert grade["perturbation_id"] == PI_VTEST_V1_ID
    assert grade["success"] is True
    assert grade["status"] == AuditStatus.OK.value
    assert grade["outcome"] == "success"
    holdout, visible = holdout_partition(INSTANCE_ID, record.fail_to_pass)
    assert grade["holdout_f2p"] == list(holdout)
    assert grade["visible_f2p"] == list(visible)
    assert (artifact_dir / "harness.log").read_text(encoding="utf-8") == (
        "mock pi_vtest harness log\n"
    )

    audit = AuditRecord.from_dict(
        json.loads((artifact_dir / "audit.json").read_text(encoding="utf-8"))
    )
    assert audit.perturbation_id == PI_VTEST_V1_ID
    assert audit.status is AuditStatus.OK
    assert audit.success is True
    assert audit.outcome is not None
    assert audit.outcome.value == "success"
    assert set(audit.tests_run) >= set(holdout)


def test_run_pi_vtest_not_viable_writes_invalid(tmp_path: Path) -> None:
    patch_path = tmp_path / "prod_only.patch"
    patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "instance_id": "single_f2p",
                    "repo": "org/repo",
                    "base_commit": "deadbeef1234567890abcdef1234567890abcdef12",
                    "patch": "diff --git a/x b/x\n",
                    "FAIL_TO_PASS": '["tests.test_one"]',
                    "PASS_TO_PASS": "[]",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    grade = run_pi_vtest_grading(
        metadata_path=metadata_path,
        instance_id="single_f2p",
        patch_path=patch_path,
        output_dir=tmp_path,
    )

    assert grade["status"] == AuditStatus.INVALID.value
    assert grade["outcome"] == "invalid"
    assert grade["success"] is None
    assert grade["pi_vtest_viable"] is False
