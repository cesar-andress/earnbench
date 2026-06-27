"""Tests for pi_env.v1 failure diagnosis (synthetic logs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.adapters.swebench_pi_env_diagnosis import (
    FAILURE_CATEGORIES,
    diagnose_pi_env,
    write_pi_env_diagnosis,
)
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
INSTANCE_ID = "psf__requests-1724"
PATCH_SHA = "sha256:abc123"

DIAGNOSIS_KEYS = (
    "instance_id",
    "nominal_success",
    "pi_env_success",
    "likely_failure_category",
    "evidence",
    "differing_fields",
    "log_excerpt_nominal",
    "log_excerpt_pi_env",
    "recommended_action",
    "should_pi_env_be_marked_invalid",
    "should_pi_env_be_excluded_from_EF",
    "protocol_implication",
)


def _write_audit(
    path: Path,
    *,
    perturbation_id: str,
    success: bool,
    patch_sha256: str = PATCH_SHA,
    image_digest: str | None = "sha256:image",
    tests_run: tuple[str, ...] = ("tests.test_models.TestCase.test_redirect",),
    warnings: tuple[str, ...] = (),
) -> None:
    record = AuditRecord(
        instance_id=INSTANCE_ID,
        perturbation_id=perturbation_id,
        config_digest="sha256:config",
        patch_sha256=patch_sha256,
        status=AuditStatus.OK,
        success=success,
        tests_run=tests_run,
        warnings=warnings,
        image_digest=image_digest,
        log_ref=f"{INSTANCE_ID}/artifact/harness.log",
    )
    path.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")


def _write_nominal_artifacts(
    nominal_dir: Path,
    *,
    success: bool = True,
    log_text: str = "APPLY_PATCH_PASS\nall tests passed\n",
) -> None:
    nominal_dir.mkdir(parents=True, exist_ok=True)
    grade = {
        "instance_id": INSTANCE_ID,
        "repo": "psf/requests",
        "base_commit": "deadbeef1234567890abcdef1234567890abcdef12",
        "success": success,
        "status": "ok",
        "fail_to_pass": [
            "tests.test_models.TestCase.test_redirect",
            "tests.test_models.TestCase.test_other",
            "tests.test_utils.TestCase.test_ok",
        ],
        "pass_to_pass": ["tests.test_utils.TestCase.test_ok"],
        "harness_command": "/bin/bash /eval.sh",
        "log_ref": f"{INSTANCE_ID}/nominal/harness.log",
    }
    (nominal_dir / "grade.json").write_text(
        json.dumps(grade, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_audit(
        nominal_dir / "audit.json",
        perturbation_id="nominal.v1",
        success=success,
    )
    (nominal_dir / "harness.log").write_text(log_text, encoding="utf-8")


def _write_pi_env_artifacts(
    pi_env_dir: Path,
    *,
    success: bool = False,
    log_text: str = "",
    hardening_enforced: list[str] | None = None,
    hardening_not_enforced: list[str] | None = None,
) -> None:
    pi_env_dir.mkdir(parents=True, exist_ok=True)
    enforced = hardening_enforced or [
        "network_disabled",
        "python_nousersite",
        "pip_no_index",
    ]
    not_enforced = hardening_not_enforced or ["tests_mount_readonly"]
    grade = {
        "instance_id": INSTANCE_ID,
        "repo": "psf/requests",
        "base_commit": "deadbeef1234567890abcdef1234567890abcdef12",
        "perturbation_id": PI_ENV_V1_ID,
        "success": success,
        "status": "ok",
        "hardening_flags_requested": enforced + not_enforced,
        "hardening_flags_enforced": enforced,
        "hardening_flags_not_enforced": not_enforced,
        "harness_command": "/bin/bash /eval.sh",
        "log_ref": f"{INSTANCE_ID}/pi_env.v1/harness.log",
    }
    (pi_env_dir / "grade.json").write_text(
        json.dumps(grade, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_audit(
        pi_env_dir / "audit.json",
        perturbation_id=PI_ENV_V1_ID,
        success=success,
    )
    (pi_env_dir / "harness.log").write_text(log_text, encoding="utf-8")


def _patch_file(tmp_path: Path) -> Path:
    patch_path = tmp_path / "prod_only.patch"
    patch_path.write_text(
        "diff --git a/requests/models.py b/requests/models.py\n+fix\n",
        encoding="utf-8",
    )
    return patch_path


def test_diagnose_pip_no_index_failure(tmp_path: Path) -> None:
    nominal_dir = tmp_path / "nominal"
    pi_env_dir = tmp_path / "pi_env.v1"
    _write_nominal_artifacts(nominal_dir)
    _write_pi_env_artifacts(
        pi_env_dir,
        log_text=(
            "APPLY_PATCH_PASS\n"
            "PIP_NO_INDEX=1\n"
            "ERROR: Could not find a version that satisfies the requirement urllib3\n"
            "FAILED tests/test_models.py::TestCase::test_redirect\n"
        ),
    )

    diagnosis = diagnose_pi_env(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=_patch_file(tmp_path),
        nominal_dir=nominal_dir,
        pi_env_dir=pi_env_dir,
    )

    assert set(DIAGNOSIS_KEYS) <= set(diagnosis.keys())
    assert diagnosis["nominal_success"] is True
    assert diagnosis["pi_env_success"] is False
    assert diagnosis["likely_failure_category"] == "dependency_blocked_by_pip_no_index"
    assert diagnosis["should_pi_env_be_marked_invalid"] is True
    assert diagnosis["should_pi_env_be_excluded_from_EF"] is True
    assert "invalid" in diagnosis["recommended_action"]
    assert "PIP_NO_INDEX" in diagnosis["log_excerpt_pi_env"]


def test_diagnose_patch_application_difference(tmp_path: Path) -> None:
    nominal_dir = tmp_path / "nominal"
    pi_env_dir = tmp_path / "pi_env.v1"
    _write_nominal_artifacts(nominal_dir, log_text="APPLY_PATCH_PASS\n")
    _write_pi_env_artifacts(
        pi_env_dir,
        log_text="APPLY_PATCH_FAIL\nFailed to apply patch\n",
    )

    diagnosis = diagnose_pi_env(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=_patch_file(tmp_path),
        nominal_dir=nominal_dir,
        pi_env_dir=pi_env_dir,
    )

    assert diagnosis["likely_failure_category"] == "patch_application_difference"
    assert diagnosis["should_pi_env_be_marked_invalid"] is False


def test_diagnose_network_blocked_failure(tmp_path: Path) -> None:
    nominal_dir = tmp_path / "nominal"
    pi_env_dir = tmp_path / "pi_env.v1"
    _write_nominal_artifacts(nominal_dir)
    _write_pi_env_artifacts(
        pi_env_dir,
        log_text=(
            "network_mode=none\n"
            "Temporary failure in name resolution\n"
            "Connection timed out while fetching fixture\n"
        ),
    )

    diagnosis = diagnose_pi_env(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=_patch_file(tmp_path),
        nominal_dir=nominal_dir,
        pi_env_dir=pi_env_dir,
    )

    assert diagnosis["likely_failure_category"] == "network_blocked_required_test"
    assert diagnosis["should_pi_env_be_marked_invalid"] is True


def test_write_pi_env_diagnosis_writes_files(tmp_path: Path) -> None:
    nominal_dir = tmp_path / "nominal"
    pi_env_dir = tmp_path / "pi_env.v1"
    output_dir = tmp_path / "batch"
    _write_nominal_artifacts(nominal_dir)
    _write_pi_env_artifacts(
        pi_env_dir,
        log_text="PYTHONNOUSERSITE=1\nImportError: user site-packages disabled\n",
    )

    result = write_pi_env_diagnosis(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=_patch_file(tmp_path),
        nominal_dir=nominal_dir,
        pi_env_dir=pi_env_dir,
        output_dir=output_dir,
    )

    json_path = output_dir / INSTANCE_ID / "pi_env_diagnosis.json"
    md_path = output_dir / INSTANCE_ID / "pi_env_diagnosis.md"
    assert json_path.is_file()
    assert md_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["likely_failure_category"] == "python_nousersite_changed_runtime"
    assert result["diagnosis_json_path"] == str(json_path)


def test_all_failure_categories_are_documented() -> None:
    assert "unknown" in FAILURE_CATEGORIES
    assert len(FAILURE_CATEGORIES) == 9


def test_diagnose_missing_nominal_grade_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="nominal grade"):
        diagnose_pi_env(
            metadata_path=METADATA_FIXTURE,
            instance_id=INSTANCE_ID,
            patch_path=_patch_file(tmp_path),
            nominal_dir=tmp_path / "missing_nominal",
            pi_env_dir=tmp_path / "pi_env.v1",
        )
