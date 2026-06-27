"""Tests for SWE-bench nominal grading (mocked harness, no Docker)."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from earnbench.adapters.swebench_nominal import (
    NOMINAL_PERTURBATION_ID,
    HarnessNotInstalledError,
    NominalRunRequest,
    NominalRunResult,
    require_swebench_harness,
    run_nominal_grading,
)
from earnbench.adapters.swebench_patch import sha256_hex
from earnbench.audit import AuditRecord

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
INSTANCE_ID = "psf__requests-1724"

GRADE_FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "success",
    "status",
    "fail_to_pass",
    "pass_to_pass",
    "timeout_seconds",
    "started_at_utc",
    "completed_at_utc",
    "harness_command",
    "log_ref",
)


def _mock_runner(request: NominalRunRequest) -> NominalRunResult:
    return NominalRunResult(
        success=True,
        status="ok",
        harness_command="/bin/bash /eval.sh",
        log_text="mock harness log\n",
        tests_run=(
            "tests.test_models.TestCase.test_redirect",
            "tests.test_models.TestCase.test_other",
        ),
        warnings=(),
        started_at_utc="2025-06-01T12:00:00+00:00",
        completed_at_utc="2025-06-01T12:05:00+00:00",
        patch_sha256=sha256_hex(request.patch_content),
    )


def test_run_nominal_grading_writes_artifacts(tmp_path: Path) -> None:
    patch_path = tmp_path / "golden.patch"
    patch_path.write_text(
        "diff --git a/requests/models.py b/requests/models.py\n", encoding="utf-8"
    )

    grade = run_nominal_grading(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=patch_path,
        output_dir=tmp_path,
        timeout_seconds=1800,
        runner=_mock_runner,
    )

    nominal_dir = tmp_path / INSTANCE_ID / "nominal"
    assert set(GRADE_FIELDS) <= set(grade.keys())
    assert grade["instance_id"] == INSTANCE_ID
    assert grade["repo"] == "psf/requests"
    assert grade["success"] is True
    assert grade["status"] == "ok"
    assert grade["timeout_seconds"] == 1800
    assert grade["log_ref"] == f"{INSTANCE_ID}/nominal/harness.log"

    assert (nominal_dir / "grade.json").is_file()
    assert (nominal_dir / "harness.log").is_file()
    assert (nominal_dir / "audit.json").is_file()
    assert (nominal_dir / "harness.log").read_text(
        encoding="utf-8"
    ) == "mock harness log\n"

    saved_grade = json.loads((nominal_dir / "grade.json").read_text(encoding="utf-8"))
    assert saved_grade == grade

    audit_data = json.loads((nominal_dir / "audit.json").read_text(encoding="utf-8"))
    audit = AuditRecord.from_dict(audit_data)
    assert audit.perturbation_id == NOMINAL_PERTURBATION_ID
    assert audit.instance_id == INSTANCE_ID
    assert audit.status.value == "ok"
    assert audit.success is True
    assert audit.log_ref == grade["log_ref"]


def test_run_nominal_grading_rejects_empty_patch(tmp_path: Path) -> None:
    patch_path = tmp_path / "empty.patch"
    patch_path.write_text("   \n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        run_nominal_grading(
            metadata_path=METADATA_FIXTURE,
            instance_id=INSTANCE_ID,
            patch_path=patch_path,
            output_dir=tmp_path,
            runner=_mock_runner,
        )


def test_require_swebench_harness_raises_when_swebench_missing(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "swebench.harness.run_evaluation" or name.startswith("swebench"):
            raise ImportError("no swebench")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(HarnessNotInstalledError, match="pip install"):
        require_swebench_harness()


def test_require_swebench_harness_raises_when_docker_missing(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "docker":
            raise ImportError("no docker")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(HarnessNotInstalledError, match="docker"):
        require_swebench_harness()
