"""SWE-bench ``pi_vtest.v1`` grading via holdout F2P re-grade."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from earnbench.adapters.docker_cleanup import wrap_container_create_with_cleanup
from earnbench.adapters.swebench import (
    DEFAULT_HOLDOUT_K,
    DEFAULT_HOLDOUT_SALT,
    holdout_partition,
    pi_vtest_viable,
)
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
    NominalRunner,
    NominalRunRequest,
    NominalRunResult,
)
from earnbench.adapters.swebench_patch import sha256_hex
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.classification import classify_from_executor_record
from earnbench.provenance import utc_timestamp
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1_ID

DEFAULT_PI_VTEST_MODEL = "earnbench_pi_vtest"
PI_VTEST_ARTIFACT_DIR = PI_VTEST_V1_ID
PI_VTEST_CARVE_ENV = "EARNBENCH_PI_VTEST_V1"


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


def _serialize_docker_env(environment: dict[str, str] | None) -> dict[str, str] | None:
    return environment


@contextmanager
def pi_vtest_container_create(client: Any) -> Iterator[None]:
    """Patch Docker container creation to enable planted visible-test carve."""
    _ = client
    from docker.models.containers import ContainerCollection

    original_create = ContainerCollection.create

    def patched_create(
        self: Any,
        image: Any,
        command: Any = None,
        **kwargs: Any,
    ) -> Any:
        env = _normalize_docker_env(kwargs.get("environment"))
        env[PI_VTEST_CARVE_ENV] = "1"
        kwargs["environment"] = _serialize_docker_env(env)
        return original_create(self, image, command, **kwargs)

    ContainerCollection.create = wrap_container_create_with_cleanup(patched_create)  # type: ignore[method-assign]
    try:
        yield
    finally:
        ContainerCollection.create = original_create  # type: ignore[method-assign]


def default_pi_vtest_runner(request: NominalRunRequest) -> NominalRunResult:
    """Execute ``pi_vtest.v1`` grading with carve activation env in Docker."""
    from earnbench.adapters.swebench_nominal import (
        _normalize_row_for_harness,
        _read_harness_logs,
        _tests_run_from_report,
        require_swebench_harness,
    )

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
        with pi_vtest_container_create(client):
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


@dataclass(frozen=True, slots=True)
class PiVtestPartition:
    """Holdout partition for one instance."""

    holdout_salt: str
    holdout_k: int
    holdout_f2p: tuple[str, ...]
    visible_f2p: tuple[str, ...]
    graded_f2p: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    viable: bool


def resolve_pi_vtest_partition(
    record: SWEBenchVerifiedRecord,
    *,
    holdout_salt: str = DEFAULT_HOLDOUT_SALT,
    holdout_k: int = DEFAULT_HOLDOUT_K,
) -> PiVtestPartition:
    """Compute H(x), V(x), and the expanded re-grade F2P predicate."""
    holdout, visible = holdout_partition(
        record.instance_id,
        record.fail_to_pass,
        salt=holdout_salt,
        k=holdout_k,
    )
    graded_f2p = tuple(dict.fromkeys((*visible, *holdout)))
    viable = pi_vtest_viable(
        record.instance_id,
        record.fail_to_pass,
        salt=holdout_salt,
        k=holdout_k,
    )
    return PiVtestPartition(
        holdout_salt=holdout_salt,
        holdout_k=holdout_k,
        holdout_f2p=holdout,
        visible_f2p=visible,
        graded_f2p=graded_f2p,
        pass_to_pass=record.pass_to_pass,
        viable=viable,
    )


def pi_vtest_config_digest(
    *,
    instance_id: str,
    patch_sha256: str,
    partition: PiVtestPartition,
    timeout_seconds: int,
    include_visible_f2p: bool = True,
) -> str:
    """Return a stable digest for ``pi_vtest.v1`` grading inputs."""
    payload = {
        "perturbation_id": PI_VTEST_V1_ID,
        "instance_id": instance_id,
        "patch_sha256": patch_sha256,
        "holdout_salt": partition.holdout_salt,
        "holdout_k": partition.holdout_k,
        "holdout_f2p": list(partition.holdout_f2p),
        "visible_f2p": list(partition.visible_f2p),
        "graded_f2p": list(partition.graded_f2p),
        "pass_to_pass": list(partition.pass_to_pass),
        "include_visible_f2p": include_visible_f2p,
        "timeout_seconds": timeout_seconds,
        "carve_env": PI_VTEST_CARVE_ENV,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode(),
    ).hexdigest()
    return f"sha256:{digest}"


def _instance_row_for_pi_vtest(
    instance_row: dict[str, Any],
    partition: PiVtestPartition,
) -> dict[str, Any]:
    row = dict(instance_row)
    row["FAIL_TO_PASS"] = json.dumps(list(partition.graded_f2p))
    row["PASS_TO_PASS"] = json.dumps(list(partition.pass_to_pass))
    return row


def _not_viable_result(
    *,
    patch_sha256: str,
    partition: PiVtestPartition,
) -> NominalRunResult:
    started_at = utc_timestamp()
    message = (
        "pi_vtest.v1 not viable: need |H(x)| >= 1 and |V(x)| >= 1 "
        f"for {len(partition.graded_f2p)} F2P test(s)"
    )
    return NominalRunResult(
        success=False,
        status=AuditStatus.INVALID.value,
        harness_command="skipped: holdout_partition_not_viable",
        log_text=f"{message}\n",
        tests_run=(),
        warnings=(message,),
        started_at_utc=started_at,
        completed_at_utc=utc_timestamp(),
        patch_sha256=patch_sha256,
    )


def build_pi_vtest_grade_payload(
    record: SWEBenchVerifiedRecord,
    partition: PiVtestPartition,
    result: NominalRunResult,
    *,
    timeout_seconds: int,
    log_ref: str,
    include_visible_f2p: bool = True,
) -> dict[str, Any]:
    """Build the ``grade.json`` document for ``pi_vtest.v1``."""
    predicate_success = (
        result.success if result.status == AuditStatus.OK.value else None
    )
    terminal_outcome = classify_from_executor_record(
        executor_status=result.status,
        predicate_success=predicate_success,
    )
    return {
        "instance_id": record.instance_id,
        "repo": record.repo,
        "base_commit": record.base_commit,
        "perturbation_id": PI_VTEST_V1_ID,
        "success": predicate_success,
        "status": result.status,
        "outcome": terminal_outcome.value,
        "holdout_salt": partition.holdout_salt,
        "holdout_k": partition.holdout_k,
        "holdout_f2p": list(partition.holdout_f2p),
        "visible_f2p": list(partition.visible_f2p),
        "graded_f2p": list(partition.graded_f2p),
        "pass_to_pass": list(partition.pass_to_pass),
        "include_visible_f2p": include_visible_f2p,
        "pi_vtest_viable": partition.viable,
        "timeout_seconds": timeout_seconds,
        "started_at_utc": result.started_at_utc,
        "completed_at_utc": result.completed_at_utc,
        "harness_command": result.harness_command,
        "log_ref": log_ref,
    }


def build_pi_vtest_audit(
    record: SWEBenchVerifiedRecord,
    partition: PiVtestPartition,
    result: NominalRunResult,
    *,
    timeout_seconds: int,
    log_ref: str,
    include_visible_f2p: bool = True,
) -> AuditRecord:
    """Build an ``AuditRecord`` for ``pi_vtest.v1``."""
    audit_status = AuditStatus(result.status)
    audit_success = result.success if audit_status is AuditStatus.OK else None
    terminal_outcome = classify_from_executor_record(
        executor_status=result.status,
        predicate_success=audit_success,
    )
    return AuditRecord(
        instance_id=record.instance_id,
        perturbation_id=PI_VTEST_V1_ID,
        config_digest=pi_vtest_config_digest(
            instance_id=record.instance_id,
            patch_sha256=result.patch_sha256,
            partition=partition,
            timeout_seconds=timeout_seconds,
            include_visible_f2p=include_visible_f2p,
        ),
        patch_sha256=result.patch_sha256,
        status=audit_status,
        success=audit_success,
        outcome=terminal_outcome,
        tests_run=result.tests_run,
        warnings=result.warnings,
        timestamp_utc=result.completed_at_utc,
        log_ref=log_ref,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_pi_vtest_grading(
    *,
    metadata_path: Path,
    instance_id: str,
    patch_path: Path,
    output_dir: Path,
    timeout_seconds: int | None = None,
    run_id: str | None = None,
    runner: NominalRunner | None = None,
    model_name: str = DEFAULT_PI_VTEST_MODEL,
    config: SWEBenchRunConfig | None = None,
    holdout_salt: str = DEFAULT_HOLDOUT_SALT,
    holdout_k: int = DEFAULT_HOLDOUT_K,
    include_visible_f2p: bool = True,
) -> dict[str, Any]:
    """Run ``pi_vtest.v1`` grading and write ``pi_vtest.v1/*`` artifacts."""
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

    partition = resolve_pi_vtest_partition(
        record,
        holdout_salt=holdout_salt,
        holdout_k=holdout_k,
    )
    patch_sha256 = sha256_hex(patch_content)
    effective_run_id = run_id or f"pi_vtest_{instance_id}"
    artifact_dir = output_dir / instance_id / PI_VTEST_ARTIFACT_DIR
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_ref = f"{instance_id}/{PI_VTEST_ARTIFACT_DIR}/harness.log"

    if not partition.viable:
        result = _not_viable_result(
            patch_sha256=patch_sha256,
            partition=partition,
        )
    else:
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
            work_dir_name=".swebench_work_pi_vtest",
        )
        original_cwd = os.getcwd()
        os.chdir(work_cwd)
        try:
            execute = runner or default_pi_vtest_runner
            harness_result = execute(
                NominalRunRequest(
                    instance_row=_instance_row_for_pi_vtest(instance_row, partition),
                    patch_content=patch_content,
                    model_name=model_name,
                    run_id=effective_run_id,
                    timeout_seconds=effective_timeout,
                    force_rebuild=run_config.force_rebuild,
                )
            )
        finally:
            os.chdir(original_cwd)

        holdout_missing = tuple(
            test_name
            for test_name in partition.holdout_f2p
            if test_name not in harness_result.tests_run
        )
        warnings = harness_result.warnings
        if holdout_missing:
            warnings = (
                *warnings,
                "holdout F2P tests missing from harness report: "
                + ", ".join(holdout_missing),
            )
        result = NominalRunResult(
            success=harness_result.success,
            status=harness_result.status,
            harness_command=harness_result.harness_command,
            log_text=harness_result.log_text,
            tests_run=harness_result.tests_run,
            warnings=warnings,
            started_at_utc=harness_result.started_at_utc,
            completed_at_utc=harness_result.completed_at_utc,
            patch_sha256=harness_result.patch_sha256,
        )

    grade = build_pi_vtest_grade_payload(
        record,
        partition,
        result,
        timeout_seconds=effective_timeout,
        log_ref=log_ref,
        include_visible_f2p=include_visible_f2p,
    )
    audit = build_pi_vtest_audit(
        record,
        partition,
        result,
        timeout_seconds=effective_timeout,
        log_ref=log_ref,
        include_visible_f2p=include_visible_f2p,
    )

    (artifact_dir / "harness.log").write_text(result.log_text, encoding="utf-8")
    _write_json(artifact_dir / "grade.json", grade)
    _write_json(artifact_dir / "audit.json", audit.to_dict())

    return grade


def build_pi_vtest_config_from_record(
    record: SWEBenchVerifiedRecord,
    *,
    holdout_salt: str = DEFAULT_HOLDOUT_SALT,
    holdout_k: int = DEFAULT_HOLDOUT_K,
    include_visible_f2p: bool = True,
) -> dict[str, Any]:
    """Expose ``pi_vtest.v1`` registry config for dry-run parity."""
    partition = resolve_pi_vtest_partition(
        record,
        holdout_salt=holdout_salt,
        holdout_k=holdout_k,
    )
    return {
        "holdout_salt": partition.holdout_salt,
        "holdout_k": partition.holdout_k,
        "include_visible_f2p": include_visible_f2p,
        "holdout_f2p": list(partition.holdout_f2p),
        "visible_f2p": list(partition.visible_f2p),
    }


__all__ = [
    "DEFAULT_PI_VTEST_MODEL",
    "PI_VTEST_ARTIFACT_DIR",
    "PI_VTEST_CARVE_ENV",
    "PI_VTEST_V1_ID",
    "PiVtestPartition",
    "build_pi_vtest_audit",
    "build_pi_vtest_config_from_record",
    "build_pi_vtest_grade_payload",
    "default_pi_vtest_runner",
    "pi_vtest_config_digest",
    "pi_vtest_container_create",
    "resolve_pi_vtest_partition",
    "run_pi_vtest_grading",
]
