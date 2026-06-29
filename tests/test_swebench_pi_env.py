"""Tests for SWE-bench pi_env.v1 execution (mocked harness)."""

from __future__ import annotations

import json
import os
import sys
import types
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
    _apply_docker_hardening_kwargs,
    build_pi_env_grade_payload,
    default_pi_env_hardening_config,
    hardened_container_create,
    not_enforced_warning,
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


def _require_working_docker_sdk() -> None:
    """Skip integration tests when the Docker SDK cannot be imported."""
    try:
        import docker  # noqa: F401
        from docker.models.containers import ContainerCollection  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name == "distutils":
            pytest.skip(
                "Docker SDK requires distutils, which is unavailable in this "
                "Python environment (common with distro docker packages on 3.12+)",
            )
        pytest.skip(f"Docker SDK unavailable: {exc}")
    except ImportError as exc:
        pytest.skip(
            "Docker SDK import failed in this environment "
            f"(environment/package issue, not EarnBench logic): {exc}",
        )


def _docker_integration_enabled() -> bool:
    return os.environ.get("EARNBENCH_RUN_DOCKER_INTEGRATION") == "1"


def _require_local_swebench_eval_image(image_key: str) -> None:
    """Skip when the SWE-bench instance image is not present locally."""
    _require_working_docker_sdk()
    import docker

    client = docker.from_env()
    try:
        client.images.get(image_key)
    except docker.errors.ImageNotFound:
        pytest.skip(
            f"SWE-bench eval image not present locally ({image_key}); "
            "preload the image before running Docker integration tests",
        )
    finally:
        client.close()


def _install_fake_docker_container_collection(
    monkeypatch: pytest.MonkeyPatch,
    *,
    recorded_kwargs: dict[str, object],
) -> type:
    def fake_original_create(
        self: object,
        image: object,
        command: object = None,
        **kwargs: object,
    ) -> MagicMock:
        recorded_kwargs.clear()
        recorded_kwargs.update(kwargs)
        return MagicMock(id="container-id")

    class FakeContainerCollection:
        create = fake_original_create

    containers_mod = types.SimpleNamespace(
        ContainerCollection=FakeContainerCollection,
    )
    monkeypatch.setitem(sys.modules, "docker.models.containers", containers_mod)
    return FakeContainerCollection


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


def test_apply_docker_hardening_kwargs_sets_network_and_env() -> None:
    kwargs: dict[str, object] = {"environment": {"PATH": "/usr/bin"}}
    enforced: list[str] = []
    _apply_docker_hardening_kwargs(kwargs, PiEnvHardeningConfig(), enforced)

    assert kwargs["network_mode"] == "none"
    environment = kwargs["environment"]
    assert isinstance(environment, dict)
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PIP_NO_INDEX"] == "1"
    assert environment["PATH"] == "/usr/bin"
    assert enforced == ["network_disabled", "python_nousersite", "pip_no_index"]


def test_hardened_container_create_applies_docker_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_kwargs: dict[str, object] = {}
    fake_collection = _install_fake_docker_container_collection(
        monkeypatch,
        recorded_kwargs=recorded_kwargs,
    )

    client = MagicMock()
    hardening = PiEnvHardeningConfig()
    with hardened_container_create(client, hardening) as (
        enforced,
        not_enforced,
        warnings,
    ):
        fake_collection.create(
            fake_collection(),
            "swebench/test:latest",
            environment={"PATH": "/usr/bin"},
        )

    assert recorded_kwargs["network_mode"] == "none"
    environment = recorded_kwargs["environment"]
    assert isinstance(environment, dict)
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PIP_NO_INDEX"] == "1"
    assert environment["PATH"] == "/usr/bin"
    assert "network_disabled" in enforced
    assert "python_nousersite" in enforced
    assert "pip_no_index" in enforced
    assert "tests_mount_readonly" in not_enforced
    assert any("tests_mount_readonly" in warning for warning in warnings)


def test_hardened_container_create_reports_readonly_not_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_kwargs: dict[str, object] = {}
    fake_collection = _install_fake_docker_container_collection(
        monkeypatch,
        recorded_kwargs=recorded_kwargs,
    )

    client = MagicMock()
    hardening = PiEnvHardeningConfig(tests_mount_readonly=True)
    with hardened_container_create(client, hardening) as (
        _enforced,
        not_enforced,
        warnings,
    ):
        fake_collection.create(fake_collection(), "img")

    assert "tests_mount_readonly" in not_enforced
    assert warnings == [
        not_enforced_warning(
            "tests_mount_readonly",
            "SWE-bench harness has no read-only test mount hook",
        ),
    ]


@pytest.mark.skipif(
    not _docker_integration_enabled(),
    reason="set EARNBENCH_RUN_DOCKER_INTEGRATION=1 to run live Docker integration tests",
)
def test_hardened_container_create_applies_on_real_docker_client(
    tmp_path: Path,
) -> None:
    pytest.importorskip("swebench")
    import uuid

    import docker
    from swebench.harness.docker_build import build_container, setup_logger

    image_key = "sweb.eval.x86_64.psf__requests-1724:latest"
    _require_local_swebench_eval_image(image_key)

    client = docker.from_env()
    hardening = PiEnvHardeningConfig()
    run_id = f"earnbench_hardening_test_{uuid.uuid4().hex[:8]}"
    spec = MagicMock()
    spec.instance_id = "psf__requests-1724"
    spec.is_remote_image = True
    spec.instance_image_key = image_key
    spec.docker_specs = {}
    spec.platform = None
    spec.get_instance_container_name.return_value = run_id
    logger = setup_logger(spec.instance_id, tmp_path / "docker_build.log")
    container = None

    try:
        with hardened_container_create(client, hardening) as (enforced, _, _):
            container = build_container(
                spec,
                client,
                run_id,
                logger,
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


def _mock_runner_pip_blocked(request: NominalRunRequest) -> PiEnvHarnessResult:
    outcome = NominalRunResult(
        success=False,
        status=AuditStatus.OK.value,
        harness_command="/bin/bash /eval.sh",
        log_text=(
            "APPLY_PATCH_PASS\n"
            "PIP_NO_INDEX=1\n"
            "ERROR: No matching distribution found for urllib3\n"
            "ERROR: Could not find a version that satisfies the requirement urllib3\n"
            "FAILED tests/test_models.py::TestCase::test_redirect\n"
        ),
        tests_run=(),
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


def test_run_pi_env_reclassifies_pip_no_index_as_invalid(tmp_path: Path) -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    prod = extract_prod_patch(record.golden_patch)
    patch_path = tmp_path / "prod_only.patch"
    patch_path.write_text(prod.prod_patch, encoding="utf-8")
    instance_dir = tmp_path / INSTANCE_ID
    (instance_dir / "nominal").mkdir(parents=True)
    (instance_dir / "nominal" / "grade.json").write_text(
        json.dumps({"success": True, "status": "ok"}) + "\n",
        encoding="utf-8",
    )

    grade = run_pi_env_grading(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        patch_path=patch_path,
        output_dir=tmp_path,
        runner=_mock_runner_pip_blocked,
    )

    artifact_dir = tmp_path / INSTANCE_ID / PI_ENV_ARTIFACT_DIR
    assert grade["status"] == AuditStatus.INVALID.value
    assert grade["success"] is None
    assert grade["outcome"] == "invalid"
    assert grade["failure_category"] == "dependency_blocked_by_pip_no_index"

    audit = AuditRecord.from_dict(
        json.loads((artifact_dir / "audit.json").read_text(encoding="utf-8"))
    )
    assert audit.status is AuditStatus.INVALID
    assert audit.success is None
    assert audit.outcome is not None
    assert audit.outcome.value == "invalid"
    assert any("pi_env_invalid" in warning for warning in audit.warnings)
