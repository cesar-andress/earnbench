"""SWE-bench ``pi_verif.v1`` grading via the official harness."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.base import AdapterConfig
from earnbench.adapters.swebench import build_pi_verif_prepare_bundle
from earnbench.adapters.swebench_config import (
    SWEBenchRunConfig,
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
    default_nominal_runner,
)
from earnbench.adapters.swebench_patch import (
    DEFAULT_PROTECTED_GLOBS,
    ProdPatchResult,
    extract_prod_patch,
    is_protected_path,
    iter_diff_file_blocks,
    sha256_hex,
    validate_protected_path_stripping,
)
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.provenance import utc_timestamp
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID

DEFAULT_PI_VERIF_MODEL = "earnbench_pi_verif"
PI_VERIF_ARTIFACT_DIR = PI_VERIF_V1_ID
TAMPER_WARNING_PREFIX = (
    "verifier tampering detected: prod-only patch modifies protected path(s)"
)


@dataclass(frozen=True, slots=True)
class PiVerifAnalysis:
    """Pre-execution ``pi_verif.v1`` patch and pristine-state analysis."""

    protected_paths: tuple[str, ...]
    touched_protected_paths: tuple[str, ...]
    tamper_detected: bool
    pristine_test_sha256: str | None
    patch_sha256: str
    prod_result: ProdPatchResult


def pi_verif_protected_paths() -> tuple[str, ...]:
    """Return protected-path globs from the ``pi_verif.v1`` registry spec."""
    return DEFAULT_PROTECTED_GLOBS


def patch_touches_protected_paths(
    patch: str,
    protected_globs: tuple[str, ...] = DEFAULT_PROTECTED_GLOBS,
) -> tuple[str, ...]:
    """Return sorted protected paths modified by ``patch``."""
    touched = [
        path
        for path, _block in iter_diff_file_blocks(patch)
        if is_protected_path(path, protected_globs)
    ]
    return tuple(sorted(set(touched)))


def resolve_pristine_test_sha256(record: SWEBenchVerifiedRecord) -> str | None:
    """Hash the metadata ``test_patch`` when present (pristine verifier tree)."""
    if not record.test_patch.strip():
        return None
    return sha256_hex(record.test_patch)


def validate_pristine_test_state(record: SWEBenchVerifiedRecord) -> tuple[str, ...]:
    """Return warnings when pristine verifier inputs are missing from metadata."""
    if record.test_patch.strip():
        return ()
    return ("metadata test_patch missing; pristine test state not fingerprinted",)


def pi_verif_config_digest(
    *,
    instance_id: str,
    patch_sha256: str,
    protected_paths: tuple[str, ...],
    pristine_test_sha256: str | None,
    timeout_seconds: int,
) -> str:
    """Return a stable digest for ``pi_verif.v1`` grading inputs."""
    payload = {
        "perturbation_id": PI_VERIF_V1_ID,
        "instance_id": instance_id,
        "patch_sha256": patch_sha256,
        "protected_paths": list(protected_paths),
        "pristine_test_sha256": pristine_test_sha256 or "",
        "timeout_seconds": timeout_seconds,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode(),
    ).hexdigest()
    return f"sha256:{digest}"


def analyze_pi_verif_patch(
    patch_content: str,
    *,
    record: SWEBenchVerifiedRecord,
    protected_globs: tuple[str, ...] | None = None,
) -> PiVerifAnalysis:
    """Analyze a prod-only patch for protected-path tampering."""
    globs = protected_globs or pi_verif_protected_paths()
    prod_result = extract_prod_patch(patch_content, protected_globs=globs)
    validate_protected_path_stripping(prod_result, protected_globs=globs)
    touched = patch_touches_protected_paths(patch_content, globs)
    tamper_detected = bool(touched)
    return PiVerifAnalysis(
        protected_paths=globs,
        touched_protected_paths=touched,
        tamper_detected=tamper_detected,
        pristine_test_sha256=resolve_pristine_test_sha256(record),
        patch_sha256=sha256_hex(patch_content),
        prod_result=prod_result,
    )


def build_pi_verif_grade_payload(
    record: SWEBenchVerifiedRecord,
    analysis: PiVerifAnalysis,
    *,
    success: bool,
    status: str,
    timeout_seconds: int,
    started_at_utc: str,
    completed_at_utc: str,
    harness_command: str,
    log_ref: str,
) -> dict[str, Any]:
    """Build the ``grade.json`` document for ``pi_verif.v1``."""
    return {
        "instance_id": record.instance_id,
        "repo": record.repo,
        "base_commit": record.base_commit,
        "perturbation_id": PI_VERIF_V1_ID,
        "success": success,
        "status": status,
        "protected_paths": list(analysis.protected_paths),
        "stripped_paths": list(analysis.touched_protected_paths),
        "tamper_detected": analysis.tamper_detected,
        "timeout_seconds": timeout_seconds,
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "harness_command": harness_command,
        "log_ref": log_ref,
    }


def build_pi_verif_audit(
    record: SWEBenchVerifiedRecord,
    analysis: PiVerifAnalysis,
    *,
    success: bool,
    status: AuditStatus,
    tests_run: tuple[str, ...],
    warnings: tuple[str, ...],
    timeout_seconds: int,
    log_ref: str,
    completed_at_utc: str,
) -> AuditRecord:
    """Build an ``AuditRecord`` for ``pi_verif.v1``."""
    audit_success = success if status is AuditStatus.OK else None
    return AuditRecord(
        instance_id=record.instance_id,
        perturbation_id=PI_VERIF_V1_ID,
        config_digest=pi_verif_config_digest(
            instance_id=record.instance_id,
            patch_sha256=analysis.patch_sha256,
            protected_paths=analysis.protected_paths,
            pristine_test_sha256=analysis.pristine_test_sha256,
            timeout_seconds=timeout_seconds,
        ),
        patch_sha256=analysis.patch_sha256,
        pristine_test_sha256=analysis.pristine_test_sha256,
        status=status,
        success=audit_success,
        tests_run=tests_run,
        warnings=warnings,
        timestamp_utc=completed_at_utc,
        log_ref=log_ref,
    )


def _tamper_warning(touched_paths: tuple[str, ...]) -> str:
    paths = ", ".join(touched_paths)
    return f"{TAMPER_WARNING_PREFIX}: {paths}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _synthetic_tamper_result(
    analysis: PiVerifAnalysis,
    *,
    pristine_warnings: tuple[str, ...],
) -> NominalRunResult:
    started_at = utc_timestamp()
    warnings = (_tamper_warning(analysis.touched_protected_paths),) + pristine_warnings
    return NominalRunResult(
        success=False,
        status=AuditStatus.OK.value,
        harness_command="skipped: protected_path_tamper",
        log_text=(
            "pi_verif.v1 grading skipped: patch modifies protected verifier paths\n"
            f"touched_paths={list(analysis.touched_protected_paths)}\n"
        ),
        tests_run=(),
        warnings=warnings,
        started_at_utc=started_at,
        completed_at_utc=utc_timestamp(),
        patch_sha256=analysis.patch_sha256,
    )


def run_pi_verif_grading(
    *,
    metadata_path: Path,
    instance_id: str,
    patch_path: Path,
    output_dir: Path,
    timeout_seconds: int | None = None,
    run_id: str | None = None,
    runner: NominalRunner | None = None,
    model_name: str = DEFAULT_PI_VERIF_MODEL,
    config: SWEBenchRunConfig | None = None,
) -> dict[str, Any]:
    """Run ``pi_verif.v1`` grading and write ``pi_verif.v1/*`` artifacts."""
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

    analysis = analyze_pi_verif_patch(patch_content, record=record)
    pristine_warnings = validate_pristine_test_state(record)

    effective_run_id = run_id or f"pi_verif_{instance_id}"
    artifact_dir = output_dir / instance_id / PI_VERIF_ARTIFACT_DIR
    artifact_dir.mkdir(parents=True, exist_ok=True)
    log_ref = f"{instance_id}/{PI_VERIF_ARTIFACT_DIR}/harness.log"

    if analysis.tamper_detected:
        result = _synthetic_tamper_result(
            analysis,
            pristine_warnings=pristine_warnings,
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

        work_cwd = prepare_swebench_workdir(output_dir, run_config)
        original_cwd = os.getcwd()
        os.chdir(work_cwd)
        try:
            execute = runner or default_nominal_runner
            harness_result = execute(
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

        warnings = harness_result.warnings + pristine_warnings
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

    audit_status = AuditStatus(result.status)
    grade = build_pi_verif_grade_payload(
        record,
        analysis,
        success=result.success,
        status=result.status,
        timeout_seconds=effective_timeout,
        started_at_utc=result.started_at_utc,
        completed_at_utc=result.completed_at_utc,
        harness_command=result.harness_command,
        log_ref=log_ref,
    )
    audit = build_pi_verif_audit(
        record,
        analysis,
        success=result.success,
        status=audit_status,
        tests_run=result.tests_run,
        warnings=result.warnings,
        timeout_seconds=effective_timeout,
        log_ref=log_ref,
        completed_at_utc=result.completed_at_utc,
    )

    (artifact_dir / "harness.log").write_text(result.log_text, encoding="utf-8")
    _write_json(artifact_dir / "grade.json", grade)
    _write_json(artifact_dir / "audit.json", audit.to_dict())

    return grade


def build_pi_verif_config_from_record(
    record: SWEBenchVerifiedRecord,
    prod_result: ProdPatchResult,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Expose ``pi_verif.v1`` registry config for dry-run parity."""
    bundle = build_pi_verif_prepare_bundle(
        record=record,
        prod_result=prod_result,
        config=AdapterConfig(dataset_revision="runtime"),
        run_id=run_id,
    )
    config = dict(bundle.config)
    pristine = resolve_pristine_test_sha256(record)
    if pristine:
        config["pristine_test_sha256"] = pristine
    return config


__all__ = [
    "DEFAULT_PI_VERIF_MODEL",
    "PI_VERIF_ARTIFACT_DIR",
    "PI_VERIF_V1_ID",
    "PiVerifAnalysis",
    "analyze_pi_verif_patch",
    "build_pi_verif_audit",
    "build_pi_verif_config_from_record",
    "build_pi_verif_grade_payload",
    "patch_touches_protected_paths",
    "pi_verif_config_digest",
    "pi_verif_protected_paths",
    "resolve_pristine_test_sha256",
    "run_pi_verif_grading",
    "validate_pristine_test_state",
]
