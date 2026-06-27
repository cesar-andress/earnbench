"""SWE-bench ``pi_env.v1`` grading via the official harness with hardening."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench_config import (
    SWEBenchRunConfig,
    instance_workspace_root,
    prepare_swebench_workdir,
)
from earnbench.adapters.swebench_metadata import (
    SWEBenchVerifiedRecord,
    load_verified_instance,
    load_verified_instance_row,
)
from earnbench.adapters.swebench_nominal import (
    NominalRunRequest,
    NominalRunResult,
    _normalize_row_for_harness,
    _read_harness_logs,
    _tests_run_from_report,
    require_swebench_harness,
)
from earnbench.adapters.swebench_patch import sha256_hex
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.provenance import build_provenance, utc_timestamp
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID

DEFAULT_PI_ENV_MODEL = "earnbench_pi_env"
PI_ENV_ARTIFACT_DIR = PI_ENV_V1_ID
NOT_ENFORCED_SUFFIX = ": not_enforced"

HARDENING_FLAG_NAMES = (
    "network_disabled",
    "python_nousersite",
    "pip_no_index",
    "tests_mount_readonly",
)


@dataclass(frozen=True, slots=True)
class PiEnvHardeningConfig:
    """Requested ``pi_env.v1`` hardening flags."""

    network_disabled: bool = True
    python_nousersite: bool = True
    pip_no_index: bool = True
    tests_mount_readonly: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {
            "network_disabled": self.network_disabled,
            "python_nousersite": self.python_nousersite,
            "pip_no_index": self.pip_no_index,
            "tests_mount_readonly": self.tests_mount_readonly,
        }


@dataclass(frozen=True, slots=True)
class PiEnvHarnessResult:
    """Outcome from one ``pi_env.v1`` harness execution."""

    outcome: NominalRunResult
    hardening_flags_requested: tuple[str, ...]
    hardening_flags_enforced: tuple[str, ...]
    hardening_flags_not_enforced: tuple[str, ...]
    image_digest: str | None = None


PiEnvRunner = Callable[[NominalRunRequest], PiEnvHarnessResult]


def default_pi_env_hardening_config() -> PiEnvHardeningConfig:
    """Return the default hardened execution profile for smoke and Phase A."""
    return PiEnvHardeningConfig()


def not_enforced_warning(flag_name: str, reason: str) -> str:
    """Format a warning that a hardening flag was not enforced."""
    return f"{flag_name}{NOT_ENFORCED_SUFFIX} ({reason})"


def _requested_flags(config: PiEnvHardeningConfig) -> tuple[str, ...]:
    return tuple(flag for flag in HARDENING_FLAG_NAMES if getattr(config, flag))


def _normalize_docker_env(
    environment: dict[str, str] | list[str] | None,
) -> dict[str, str]:
    if environment is None:
        return {}
    if isinstance(environment, dict):
        return dict(environment)
    env: dict[str, str] = {}
    for item in environment:
        if "=" in item:
            key, value = item.split("=", 1)
            env[key] = value
    return env


def _serialize_docker_env(environment: dict[str, str] | list[str] | None) -> Any:
    if environment is None:
        return None
    if isinstance(environment, list):
        return environment
    return environment


def _apply_docker_hardening_kwargs(
    kwargs: dict[str, Any],
    hardening: PiEnvHardeningConfig,
    enforced: list[str],
) -> None:
    """Mutate Docker ``containers.create`` kwargs and record enforced flags."""
    if hardening.network_disabled:
        kwargs["network_mode"] = "none"
        if "network_disabled" not in enforced:
            enforced.append("network_disabled")
    if hardening.python_nousersite or hardening.pip_no_index:
        env = _normalize_docker_env(kwargs.get("environment"))
        if hardening.python_nousersite:
            env["PYTHONNOUSERSITE"] = "1"
            if "python_nousersite" not in enforced:
                enforced.append("python_nousersite")
        if hardening.pip_no_index:
            env["PIP_NO_INDEX"] = "1"
            if "pip_no_index" not in enforced:
                enforced.append("pip_no_index")
        kwargs["environment"] = _serialize_docker_env(env)


@contextmanager
def hardened_container_create(
    client: Any,
    hardening: PiEnvHardeningConfig,
) -> Iterator[tuple[list[str], list[str], list[str]]]:
    """Patch Docker container creation to apply supported hardening flags."""
    _ = client  # runner passes the harness client; patch is class-scoped
    enforced: list[str] = []
    not_enforced: list[str] = []
    warnings: list[str] = []

    if hardening.tests_mount_readonly:
        not_enforced.append("tests_mount_readonly")
        warnings.append(
            not_enforced_warning(
                "tests_mount_readonly",
                "SWE-bench harness has no read-only test mount hook",
            )
        )

    from docker.models.containers import ContainerCollection

    original_create = ContainerCollection.create

    def patched_create(
        self: Any,
        image: Any,
        command: Any = None,
        **kwargs: Any,
    ) -> Any:
        _apply_docker_hardening_kwargs(kwargs, hardening, enforced)
        return original_create(self, image, command, **kwargs)

    ContainerCollection.create = patched_create  # type: ignore[method-assign]
    try:
        yield enforced, not_enforced, warnings
    finally:
        ContainerCollection.create = original_create  # type: ignore[method-assign]


def resolve_instance_image_digest(client: Any, image_name: str) -> str | None:
    """Return a repo digest or image id for the SWE-bench instance image."""
    try:
        image = client.images.get(image_name)
    except Exception:
        return None
    digests = image.attrs.get("RepoDigests") or []
    if digests:
        return str(digests[0])
    image_id = image.attrs.get("Id")
    return str(image_id) if image_id else None


def pi_env_config_digest(
    *,
    instance_id: str,
    patch_sha256: str,
    image_digest: str | None,
    hardening: PiEnvHardeningConfig,
    timeout_seconds: int,
) -> str:
    """Return a stable digest for ``pi_env.v1`` grading inputs."""
    payload = {
        "perturbation_id": PI_ENV_V1_ID,
        "instance_id": instance_id,
        "patch_sha256": patch_sha256,
        "image_digest": image_digest or "",
        "hardening": hardening.to_dict(),
        "timeout_seconds": timeout_seconds,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode(),
    ).hexdigest()
    return f"sha256:{digest}"


def build_pi_env_grade_payload(
    record: SWEBenchVerifiedRecord,
    result: PiEnvHarnessResult,
    *,
    timeout_seconds: int,
    log_ref: str,
) -> dict[str, Any]:
    """Build the ``grade.json`` document for ``pi_env.v1``."""
    outcome = result.outcome
    return {
        "instance_id": record.instance_id,
        "repo": record.repo,
        "base_commit": record.base_commit,
        "perturbation_id": PI_ENV_V1_ID,
        "success": outcome.success,
        "status": outcome.status,
        "hardening_flags_requested": list(result.hardening_flags_requested),
        "hardening_flags_enforced": list(result.hardening_flags_enforced),
        "hardening_flags_not_enforced": list(result.hardening_flags_not_enforced),
        "timeout_seconds": timeout_seconds,
        "started_at_utc": outcome.started_at_utc,
        "completed_at_utc": outcome.completed_at_utc,
        "harness_command": outcome.harness_command,
        "log_ref": log_ref,
    }


def build_pi_env_audit(
    record: SWEBenchVerifiedRecord,
    result: PiEnvHarnessResult,
    *,
    hardening: PiEnvHardeningConfig,
    timeout_seconds: int,
    log_ref: str,
) -> AuditRecord:
    """Build an ``AuditRecord`` for ``pi_env.v1``."""
    outcome = result.outcome
    audit_status = AuditStatus(outcome.status)
    audit_success = outcome.success if audit_status is AuditStatus.OK else None
    hardening_warnings = tuple(
        not_enforced_warning(flag, "requested but not enforced by harness wrapper")
        for flag in result.hardening_flags_not_enforced
    )
    config_digest = pi_env_config_digest(
        instance_id=record.instance_id,
        patch_sha256=outcome.patch_sha256,
        image_digest=result.image_digest,
        hardening=hardening,
        timeout_seconds=timeout_seconds,
    )
    return AuditRecord(
        instance_id=record.instance_id,
        perturbation_id=PI_ENV_V1_ID,
        config_digest=config_digest,
        patch_sha256=outcome.patch_sha256,
        image_digest=result.image_digest,
        status=audit_status,
        success=audit_success,
        tests_run=outcome.tests_run,
        warnings=outcome.warnings + hardening_warnings,
        timestamp_utc=outcome.completed_at_utc,
        log_ref=log_ref,
        provenance=build_provenance(
            config_digest=config_digest,
            docker_image_digest=result.image_digest,
        ),
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def default_pi_env_runner(
    request: NominalRunRequest,
    *,
    hardening: PiEnvHardeningConfig | None = None,
) -> PiEnvHarnessResult:
    """Execute ``pi_env.v1`` grading through the SWE-bench Docker harness."""
    require_swebench_harness()
    import docker
    from swebench.harness.constants import (
        KEY_INSTANCE_ID,
        KEY_MODEL,
        KEY_PREDICTION,
        LOG_INSTANCE,
        LOG_TEST_OUTPUT,
        RUN_EVALUATION_LOG_DIR,
    )
    from swebench.harness.run_evaluation import run_instance
    from swebench.harness.test_spec.test_spec import make_test_spec

    effective_hardening = hardening or default_pi_env_hardening_config()
    requested = _requested_flags(effective_hardening)
    started_at = utc_timestamp()
    row = _normalize_row_for_harness(request.instance_row)
    instance_id = str(row["instance_id"])
    fail_to_pass = tuple(json.loads(row["FAIL_TO_PASS"]))
    pass_to_pass = tuple(json.loads(row.get("PASS_TO_PASS") or "[]"))

    test_spec = make_test_spec(row)
    harness_command = "/bin/bash /eval.sh"
    if test_spec.eval_script.strip():
        harness_command = test_spec.eval_script.strip().splitlines()[0]

    prediction = {
        KEY_INSTANCE_ID: instance_id,
        KEY_MODEL: request.model_name,
        KEY_PREDICTION: request.patch_content,
    }

    client = docker.from_env()
    enforced: list[str] = []
    not_enforced: list[str] = []
    warnings: list[str] = []
    image_digest = resolve_instance_image_digest(client, test_spec.instance_image_key)
    outcome: dict[str, Any] = {"completed": False, "resolved": False}
    report: dict[str, Any] = {}

    container_hardening_applied = False
    try:
        with hardened_container_create(client, effective_hardening) as hardening_state:
            enforced, not_enforced, warnings = hardening_state
            outcome = run_instance(
                test_spec,
                prediction,
                rm_image=False,
                force_rebuild=request.force_rebuild,
                client=client,
                run_id=request.run_id,
                timeout=request.timeout_seconds,
            )
            report_path = (
                RUN_EVALUATION_LOG_DIR
                / request.run_id
                / request.model_name.replace("/", "__")
                / instance_id
                / "report.json"
            )
            if report_path.is_file():
                report = json.loads(report_path.read_text(encoding="utf-8"))
            container_hardening_applied = bool(enforced)
    finally:
        client.close()

    if bool(outcome.get("completed")) and not container_hardening_applied:
        for flag in ("network_disabled", "python_nousersite", "pip_no_index"):
            if getattr(effective_hardening, flag) and flag not in enforced:
                warnings.append(
                    f"{flag}: requested but create hook did not record enforcement"
                )

    log_dir = (
        Path.cwd()
        / RUN_EVALUATION_LOG_DIR
        / request.run_id
        / request.model_name.replace("/", "__")
        / instance_id
    )
    log_text = _read_harness_logs(log_dir)
    if not log_text:
        run_log = log_dir / LOG_INSTANCE
        test_log = log_dir / LOG_TEST_OUTPUT
        if run_log.is_file():
            log_text = run_log.read_text(encoding="utf-8", errors="replace")
        elif test_log.is_file():
            log_text = test_log.read_text(encoding="utf-8", errors="replace")
        else:
            warnings.append("harness log files not found under logs/run_evaluation")

    completed = bool(outcome.get("completed"))
    resolved = bool(outcome.get("resolved"))
    if not completed:
        status = AuditStatus.INVALID.value
        success = False
        if not any("did not complete" in item for item in warnings):
            warnings.append("harness run did not complete")
    else:
        status = AuditStatus.OK.value
        success = resolved

    tests_run = _tests_run_from_report(
        instance_id,
        report,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
    )
    nominal = NominalRunResult(
        success=success,
        status=status,
        harness_command=harness_command,
        log_text=log_text,
        tests_run=tests_run,
        warnings=tuple(warnings),
        started_at_utc=started_at,
        completed_at_utc=utc_timestamp(),
        patch_sha256=sha256_hex(request.patch_content),
    )
    return PiEnvHarnessResult(
        outcome=nominal,
        hardening_flags_requested=requested,
        hardening_flags_enforced=tuple(dict.fromkeys(enforced)),
        hardening_flags_not_enforced=tuple(dict.fromkeys(not_enforced)),
        image_digest=image_digest,
    )


def run_pi_env_grading(
    *,
    metadata_path: Path,
    instance_id: str,
    patch_path: Path,
    output_dir: Path,
    timeout_seconds: int | None = None,
    run_id: str | None = None,
    runner: PiEnvRunner | None = None,
    model_name: str = DEFAULT_PI_ENV_MODEL,
    config: SWEBenchRunConfig | None = None,
    hardening: PiEnvHardeningConfig | None = None,
) -> dict[str, Any]:
    """Run ``pi_env.v1`` grading and write ``pi_env.v1/*`` artifacts."""
    from earnbench.adapters.swebench_config import (
        DEFAULT_TIMEOUT_SECONDS,
        DEFAULT_WORKERS,
    )

    run_config = config or SWEBenchRunConfig(
        workers=DEFAULT_WORKERS,
        max_parallel_containers=DEFAULT_WORKERS,
        max_parallel_builds=DEFAULT_WORKERS,
        reuse_images=True,
        allow_build=True,
        cache_dir=None,
        timeout_seconds=timeout_seconds or DEFAULT_TIMEOUT_SECONDS,
    )
    effective_timeout = timeout_seconds or run_config.timeout_seconds
    effective_hardening = hardening or default_pi_env_hardening_config()

    if not patch_path.is_file():
        msg = f"patch file not found: {patch_path}"
        raise FileNotFoundError(msg)

    record = load_verified_instance(metadata_path, instance_id)
    instance_row = load_verified_instance_row(metadata_path, instance_id)
    patch_content = patch_path.read_text(encoding="utf-8")
    if not patch_content.strip():
        msg = f"patch file is empty: {patch_path}"
        raise ValueError(msg)

    effective_run_id = run_id or f"pi_env_{instance_id}"
    artifact_dir = output_dir / instance_id / PI_ENV_ARTIFACT_DIR
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_ref = f"{instance_id}/{PI_ENV_ARTIFACT_DIR}/harness.log"

    if runner is None:
        from earnbench.adapters.swebench_preflight import (
            MissingDockerImagesError,
            check_nominal_docker_images,
        )

        missing_images = check_nominal_docker_images(
            metadata_path=metadata_path,
            instance_id=instance_id,
        )
        if missing_images:
            raise MissingDockerImagesError(
                instance_id=instance_id,
                missing_images=missing_images,
                metadata_path=metadata_path,
                output_dir=output_dir,
            )

    work_cwd = prepare_swebench_workdir(
        instance_workspace_root(output_dir, instance_id),
        run_config,
        work_dir_name=".swebench_work_pi_env",
    )
    original_cwd = os.getcwd()
    os.chdir(work_cwd)
    try:
        if runner is None:
            harness_result = default_pi_env_runner(
                NominalRunRequest(
                    instance_row=instance_row,
                    patch_content=patch_content,
                    model_name=model_name,
                    run_id=effective_run_id,
                    timeout_seconds=effective_timeout,
                    force_rebuild=run_config.force_rebuild,
                ),
                hardening=effective_hardening,
            )
        else:
            harness_result = runner(
                NominalRunRequest(
                    instance_row=instance_row,
                    patch_content=patch_content,
                    model_name=model_name,
                    run_id=effective_run_id,
                    timeout_seconds=effective_timeout,
                    force_rebuild=run_config.force_rebuild,
                )
            )
    finally:
        os.chdir(original_cwd)

    grade = build_pi_env_grade_payload(
        record,
        harness_result,
        timeout_seconds=effective_timeout,
        log_ref=log_ref,
    )
    audit = build_pi_env_audit(
        record,
        harness_result,
        hardening=effective_hardening,
        timeout_seconds=effective_timeout,
        log_ref=log_ref,
    )

    (artifact_dir / "harness.log").write_text(
        harness_result.outcome.log_text,
        encoding="utf-8",
    )
    _write_json(artifact_dir / "grade.json", grade)
    _write_json(artifact_dir / "audit.json", audit.to_dict())

    return grade


__all__ = [
    "DEFAULT_PI_ENV_MODEL",
    "HARDENING_FLAG_NAMES",
    "NOT_ENFORCED_SUFFIX",
    "PI_ENV_ARTIFACT_DIR",
    "PI_ENV_V1_ID",
    "PiEnvHarnessResult",
    "PiEnvHardeningConfig",
    "PiEnvRunner",
    "build_pi_env_audit",
    "build_pi_env_grade_payload",
    "default_pi_env_hardening_config",
    "default_pi_env_runner",
    "hardened_container_create",
    "not_enforced_warning",
    "pi_env_config_digest",
    "resolve_instance_image_digest",
    "run_pi_env_grading",
]
