"""Tests for SWE-bench pi_env.v1 execution (mocked harness)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.adapters.swebench_nominal import NominalRunRequest, NominalRunResult
from earnbench.adapters.swebench_patch import extract_prod_patch, sha256_hex
from earnbench.adapters.swebench_pi_env import (
    HARDENING_FLAG_NAMES,
    PI_ENV_ARTIFACT_DIR,
    PiEnvHardeningConfig,
    PiEnvHarnessResult,
    build_pi_env_grade_payload,
    default_pi_env_hardening_config,
    hardened_container_create,
    run_pi_env_grading,
)
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID

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
    "hardening_flags_requested",
    "hardening_flags_enforced",
    "hardening_flags_not_enforced",
    "timeout_seconds",
    "started_at_utc",
    "completed_at_utc",
    "harness_command",
    "log_ref",
)


def _mock_runner(request: NominalRunRequest) -> PiEnvHarnessResult:
    outcome = NominalRunResult(
        success=True,
        status=AuditStatus.OK.value,
        harness_command="/bin/bash /eval.sh",
        log_text="mock pi_env harness log\n",
        tests_run=("tests.test_models.TestCase.test_redirect",),
        warnings=(),
        started_at_utc="2025-06-01T12:20:00+00:00",
        completed_at_utc="2025-06-01T12:25:00+00:00",
        patch_sha256=sha256_hex(request.patch_content),
    )
    return PiEnvHarnessResult(
        outcome=outcome,
        hardening_flags_requested=HARDENING_FLAG_NAMES,
        hardening_flags_enforced=(
            "network_disabled",
            "python_nousersite",
            "pip_no_index",
        ),
        hardening_flags_not_enforced=("tests_mount_readonly",),
        image_digest="sha256:mock-instance-image",
    )


def test_default_hardening_config_requests_all_flags() -> None:
    config = default_pi_env_hardening_config()
    assert config.network_disabled is True
    assert config.python_nousersite is True
    assert config.pip_no_index is True
    assert config.tests_mount_readonly is True


def test_hardened_container_create_applies_docker_flags() -> None:
    from docker.models.containers import ContainerCollection

    client = MagicMock()
    client.api._version = "1.41"
    client.api.create_container.return_value = {"Id": "container-id"}
    client.api.inspect_container.return_value = {"Id": "container-id"}
    collection = ContainerCollection(client=client)
    container = MagicMock(id="container-id")
    collection.get = MagicMock(return_value=container)  # type: ignore[method-assign]

    hardening = PiEnvHardeningConfig()
    with hardened_container_create(client, hardening) as (
        enforced,
        not_enforced,
        warnings,
    ):
        collection.create(
            image="swebench/test:latest",
            environment={"PATH": "/usr/bin"},
        )

    kwargs = client.api.create_container.call_args.kwargs
    assert kwargs["host_config"]["NetworkMode"] == "none"
    assert kwargs["environment"]["PYTHONNOUSERSITE"] == "1"
    assert kwargs["environment"]["PIP_NO_INDEX"] == "1"
    assert kwargs["environment"]["PATH"] == "/usr/bin"
    assert "network_disabled" in enforced
    assert "python_nousersite" in enforced
    assert "pip_no_index" in enforced
    assert "tests_mount_readonly" in not_enforced
    assert any("tests_mount_readonly" in warning for warning in warnings)


def test_hardened_container_create_applies_on_real_docker_client() -> None:
    pytest.importorskip("swebench")
    import logging
    import uuid
    from unittest.mock import MagicMock

    import docker
    from swebench.harness.docker_build import build_container

    client = docker.from_env()
    hardening = PiEnvHardeningConfig()
    run_id = f"earnbench_hardening_test_{uuid.uuid4().hex[:8]}"
    spec = MagicMock()
    spec.instance_id = "psf__requests-1724"
    spec.is_remote_image = True
    spec.instance_image_key = "sweb.eval.x86_64.psf__requests-1724:latest"
    spec.docker_specs = {}
    spec.platform = None
    spec.get_instance_container_name.return_value = run_id
    container = None

    try:
        with hardened_container_create(client, hardening) as (enforced, _, _):
            container = build_container(
                spec,
                client,
                run_id,
                logging.getLogger("earnbench.test"),
                nocache=False,
            )
            assert enforced == [
                "network_disabled",
                "python_nousersite",
                "pip_no_index",
            ]
            assert container.attrs["HostConfig"]["NetworkMode"] == "none"
    finally:
        if container is not None:
            container.remove(force=True)
        client.close()


def test_run_pi_env_grading_writes_artifacts(tmp_path: Path) -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    prod = extract_prod_patch(record.golden_patch)
    patch_path = tmp_path / "prod_only.patch"
    patch_path.write_text(prod.prod_patch, encoding="utf-8")

    grade = run_pi_env_grading(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=patch_path,
        output_dir=tmp_path,
        runner=_mock_runner,
    )

    artifact_dir = tmp_path / INSTANCE_ID / PI_ENV_ARTIFACT_DIR
    assert set(GRADE_FIELDS) <= set(grade.keys())
    assert grade["perturbation_id"] == PI_ENV_V1_ID
    assert grade["success"] is True
    assert grade["status"] == AuditStatus.OK.value
    assert grade["hardening_flags_requested"] == list(HARDENING_FLAG_NAMES)
    assert grade["hardening_flags_enforced"] == [
        "network_disabled",
        "python_nousersite",
        "pip_no_index",
    ]
    assert grade["hardening_flags_not_enforced"] == ["tests_mount_readonly"]
    assert (artifact_dir / "harness.log").read_text(encoding="utf-8") == (
        "mock pi_env harness log\n"
    )

    audit = AuditRecord.from_dict(
        json.loads((artifact_dir / "audit.json").read_text(encoding="utf-8"))
    )
    assert audit.perturbation_id == PI_ENV_V1_ID
    assert audit.status is AuditStatus.OK
    assert audit.success is True
    assert audit.image_digest == "sha256:mock-instance-image"
    assert any(
        "tests_mount_readonly: not_enforced" in warning for warning in audit.warnings
    )
    assert "provenance" in audit.to_dict()


def test_run_pi_env_rejects_empty_patch(tmp_path: Path) -> None:
    patch_path = tmp_path / "empty.patch"
    patch_path.write_text("  \n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        run_pi_env_grading(
            metadata_path=METADATA_FIXTURE,
            instance_id=INSTANCE_ID,
            patch_path=patch_path,
            output_dir=tmp_path,
            runner=_mock_runner,
        )


def test_build_pi_env_grade_payload_includes_repo_fields() -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    result = _mock_runner(
        NominalRunRequest(
            instance_row={"instance_id": INSTANCE_ID},
            patch_content="patch",
            model_name="earnbench_pi_env",
            run_id="pi_env_test",
            timeout_seconds=1800,
        )
    )
    grade = build_pi_env_grade_payload(
        record,
        result,
        timeout_seconds=1800,
        log_ref=f"{INSTANCE_ID}/pi_env.v1/harness.log",
    )
    assert grade["repo"] == record.repo
    assert grade["base_commit"] == record.base_commit
