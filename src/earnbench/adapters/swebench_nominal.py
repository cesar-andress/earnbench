"""Nominal SWE-bench grading via the official harness (Docker)."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench_config import (
    SWEBenchRunConfig,
    prepare_swebench_workdir,
)
from earnbench.adapters.swebench_metadata import (
    SWEBenchVerifiedRecord,
    load_verified_instance,
    load_verified_instance_row,
)
from earnbench.adapters.swebench_patch import sha256_hex
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.provenance import utc_timestamp

NOMINAL_PERTURBATION_ID = "nominal.v1"
DEFAULT_MODEL_NAME = "earnbench_nominal"
HARNESS_INSTALL_HINT = (
    "SWE-bench harness is not installed. Install with:\n"
    '  pip install -e ".[swebench]"\n'
    "Real grading also requires a running Docker daemon."
)


class HarnessNotInstalledError(RuntimeError):
    """Raised when the optional SWE-bench harness dependency is missing."""


@dataclass(frozen=True, slots=True)
class NominalRunRequest:
    """Inputs for one nominal harness execution."""

    instance_row: dict[str, Any]
    patch_content: str
    model_name: str
    run_id: str
    timeout_seconds: int
    force_rebuild: bool = False


@dataclass(frozen=True, slots=True)
class NominalRunResult:
    """Normalized outcome from a nominal harness execution."""

    success: bool
    status: str
    harness_command: str
    log_text: str
    tests_run: tuple[str, ...]
    warnings: tuple[str, ...]
    started_at_utc: str
    completed_at_utc: str
    patch_sha256: str


NominalRunner = Callable[[NominalRunRequest], NominalRunResult]


def require_swebench_harness() -> None:
    """Ensure SWE-bench harness and Docker client dependencies import cleanly."""
    missing: list[str] = []
    try:
        import swebench.harness.run_evaluation  # noqa: F401
    except ImportError:
        missing.append("swebench")
    try:
        import docker  # noqa: F401
    except ImportError:
        missing.append("docker")
    if missing:
        packages = " ".join(missing)
        msg = f"{HARNESS_INSTALL_HINT}\nMissing packages: {packages}"
        raise HarnessNotInstalledError(msg)


def nominal_config_digest(
    *,
    instance_id: str,
    patch_sha256: str,
    timeout_seconds: int,
) -> str:
    """Return a stable digest for nominal grading inputs."""
    payload = {
        "perturbation_id": NOMINAL_PERTURBATION_ID,
        "instance_id": instance_id,
        "patch_sha256": patch_sha256,
        "timeout_seconds": timeout_seconds,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode(),
    ).hexdigest()
    return f"sha256:{digest}"


def _normalize_row_for_harness(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        value = normalized.get(key)
        if isinstance(value, list):
            normalized[key] = json.dumps(value)
    return normalized


def _read_harness_logs(log_dir: Path) -> str:
    parts: list[str] = []
    for name in ("run_instance.log", "test_output.txt", "report.json"):
        path = log_dir / name
        if path.is_file():
            parts.append(f"===== {name} =====\n")
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
            if not parts[-1].endswith("\n"):
                parts.append("\n")
    if parts:
        return "".join(parts)
    return ""


def _tests_run_from_report(
    instance_id: str,
    report: dict[str, Any] | None,
    *,
    fail_to_pass: tuple[str, ...],
    pass_to_pass: tuple[str, ...],
) -> tuple[str, ...]:
    if not report or instance_id not in report:
        return fail_to_pass + pass_to_pass
    entry = report[instance_id]
    tests_status = entry.get("tests_status")
    if isinstance(tests_status, dict):
        names: list[str] = []
        for bucket in tests_status.values():
            if isinstance(bucket, dict):
                for key in ("success", "failure"):
                    values = bucket.get(key)
                    if isinstance(values, list):
                        names.extend(str(item) for item in values)
        if names:
            return tuple(dict.fromkeys(names))
    return fail_to_pass + pass_to_pass


def default_nominal_runner(request: NominalRunRequest) -> NominalRunResult:
    """Execute nominal grading through the official SWE-bench Docker harness."""
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
    outcome: dict[str, Any] = {"completed": False, "resolved": False}
    report: dict[str, Any] = {}
    warnings: list[str] = []
    try:
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
    finally:
        client.close()

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
        if not warnings:
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
    return NominalRunResult(
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


def build_grade_payload(
    record: SWEBenchVerifiedRecord,
    result: NominalRunResult,
    *,
    timeout_seconds: int,
    log_ref: str,
) -> dict[str, Any]:
    """Build the ``grade.json`` document."""
    return {
        "instance_id": record.instance_id,
        "repo": record.repo,
        "base_commit": record.base_commit,
        "success": result.success,
        "status": result.status,
        "fail_to_pass": list(record.fail_to_pass),
        "pass_to_pass": list(record.pass_to_pass),
        "timeout_seconds": timeout_seconds,
        "started_at_utc": result.started_at_utc,
        "completed_at_utc": result.completed_at_utc,
        "harness_command": result.harness_command,
        "log_ref": log_ref,
    }


def build_nominal_audit(
    record: SWEBenchVerifiedRecord,
    result: NominalRunResult,
    *,
    timeout_seconds: int,
    log_ref: str,
) -> AuditRecord:
    """Build an ``AuditRecord`` for nominal grading."""
    audit_status = AuditStatus(result.status)
    success = result.success if audit_status is AuditStatus.OK else None
    return AuditRecord(
        instance_id=record.instance_id,
        perturbation_id=NOMINAL_PERTURBATION_ID,
        config_digest=nominal_config_digest(
            instance_id=record.instance_id,
            patch_sha256=result.patch_sha256,
            timeout_seconds=timeout_seconds,
        ),
        patch_sha256=result.patch_sha256,
        status=audit_status,
        success=success,
        tests_run=result.tests_run,
        warnings=result.warnings,
        timestamp_utc=result.completed_at_utc,
        log_ref=log_ref,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_nominal_grading(
    *,
    metadata_path: Path,
    instance_id: str,
    patch_path: Path,
    output_dir: Path,
    timeout_seconds: int | None = None,
    run_id: str | None = None,
    runner: NominalRunner | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
    config: SWEBenchRunConfig | None = None,
) -> dict[str, Any]:
    """Run nominal SWE-bench grading and write ``nominal/*`` artifacts."""
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

    if not patch_path.is_file():
        msg = f"patch file not found: {patch_path}"
        raise FileNotFoundError(msg)

    record = load_verified_instance(metadata_path, instance_id)
    instance_row = load_verified_instance_row(metadata_path, instance_id)
    patch_content = patch_path.read_text(encoding="utf-8")
    if not patch_content.strip():
        msg = f"patch file is empty: {patch_path}"
        raise ValueError(msg)

    effective_run_id = run_id or f"nominal_{instance_id}"
    nominal_dir = output_dir / instance_id / "nominal"
    nominal_dir.mkdir(parents=True, exist_ok=True)
    log_ref = f"{instance_id}/nominal/harness.log"

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

    work_cwd = prepare_swebench_workdir(output_dir, run_config)
    original_cwd = os.getcwd()
    os.chdir(work_cwd)
    try:
        execute = runner or default_nominal_runner
        result = execute(
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

    grade = build_grade_payload(
        record,
        result,
        timeout_seconds=effective_timeout,
        log_ref=log_ref,
    )
    audit = build_nominal_audit(
        record,
        result,
        timeout_seconds=effective_timeout,
        log_ref=log_ref,
    )

    harness_log_path = nominal_dir / "harness.log"
    harness_log_path.write_text(result.log_text, encoding="utf-8")
    _write_json(nominal_dir / "grade.json", grade)
    _write_json(nominal_dir / "audit.json", audit.to_dict())

    return grade


__all__ = [
    "DEFAULT_MODEL_NAME",
    "HARNESS_INSTALL_HINT",
    "HarnessNotInstalledError",
    "NOMINAL_PERTURBATION_ID",
    "NominalRunRequest",
    "NominalRunResult",
    "NominalRunner",
    "build_grade_payload",
    "build_nominal_audit",
    "default_nominal_runner",
    "nominal_config_digest",
    "require_swebench_harness",
    "run_nominal_grading",
]
