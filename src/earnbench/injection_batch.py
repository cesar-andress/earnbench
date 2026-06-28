"""Blind injection evaluation batch runner (evaluator manifest only)."""

from __future__ import annotations

import csv
import json
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench import prepare_exploit, supported_perturbations
from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.injections.manifests import (
    load_evaluator_manifest,
    results_injection_id,
)
from earnbench.injections.validate import resolve_patch_ref
from earnbench.phase_a_batch import (
    METADATA_ENV_VAR,
    BatchProgress,
    _InterruptController,
    _write_json,
)
from earnbench.phase_b_batch import (
    BATCH_PI_ORDER,
    run_exploit_nominal_stage,
)
from earnbench.provenance import resolve_git_commit, utc_timestamp
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID
from earnbench.scheduler import (
    aggregate_instance,
    artifact_stage_complete,
    run_config_from_payload,
    run_config_to_payload,
    run_preflight_stage,
)

INJECTION_RESULTS_CSV = "injection_results.csv"
RUN_MANIFEST_JSON = "run_manifest.json"
FAILURES_CSV = "failures.csv"

RESULTS_COLUMNS = (
    "injection_id",
    "instance_id",
    "artifact_id",
    "arm",
    "y0",
    "ef_exclude_invalid",
    "ef_invalid_as_fail",
    "failed_mechanisms",
    "invalid_pi_count",
    "pi_vtest_status",
    "pi_verif_status",
    "pi_env_status",
)

FAILURE_COLUMNS = ("artifact_id", "instance_id", "stage", "error", "timestamp_utc")

logger = logging.getLogger("earnbench.injection_batch")


@dataclass(frozen=True, slots=True)
class InjectionBatchConfig:
    evaluator_manifest_path: Path
    metadata_path: Path
    output_dir: Path
    run_config: SWEBenchRunConfig
    workers: int = 1
    resume: bool = False
    run_id: str = "blind_injection"
    dataset_revision: str = "unpinned"
    build_missing_images: bool = False


@dataclass(frozen=True, slots=True)
class InjectionArtifactTask:
    artifact: dict[str, Any]
    spec_dir: Path
    metadata_path: str
    output_dir: str
    run_config_payload: dict[str, Any]
    run_id: str
    dataset_revision: str
    build_missing_images: bool
    resume: bool
    scheduled_perturbations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InjectionArtifactResult:
    artifact_id: str
    row: dict[str, Any] | None
    failure: dict[str, Any] | None


def resolve_metadata_path(
    metadata_path: Path | None,
    *,
    cwd: Path | None = None,
) -> Path:
    if metadata_path is not None:
        resolved = metadata_path.expanduser().resolve()
        if resolved.is_file():
            return resolved
        msg = f"metadata file not found: {resolved}"
        raise FileNotFoundError(msg)

    import os

    env_path = os.environ.get(METADATA_ENV_VAR, "").strip()
    if env_path:
        resolved = Path(env_path).expanduser().resolve()
        if resolved.is_file():
            return resolved

    base = cwd or Path.cwd()
    candidates = (
        base / "../paper/vendor/swe_verified_test.parquet",
        base / "paper/vendor/swe_verified_test.parquet",
        base / "vendor/swe_verified_test.parquet",
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    msg = (
        f"metadata parquet not found; pass --metadata-parquet or set {METADATA_ENV_VAR}"
    )
    raise FileNotFoundError(msg)


def artifact_work_dir(output_dir: Path, artifact_id: str) -> Path:
    safe = artifact_id.replace("/", "_")
    return output_dir / "artifacts" / safe


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_timestamp()} {message}\n")


def _uses_raw_patch_for_verif(instance_dir: Path) -> bool:
    meta_path = instance_dir / "meta.json"
    if not meta_path.is_file():
        return False
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    stripped = meta.get("stripped_paths") or []
    return bool(stripped)


def _pi_patch_path(instance_dir: Path, perturbation_id: str) -> Path:
    if perturbation_id == PI_VERIF_V1_ID and _uses_raw_patch_for_verif(instance_dir):
        return instance_dir / "patch" / "raw.patch"
    return instance_dir / "patch" / "prod_only.patch"


def run_injection_perturbation_stage(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    perturbation_id: str,
    run_config: SWEBenchRunConfig,
    scheduled: tuple[str, ...],
) -> None:
    if perturbation_id not in scheduled:
        from earnbench.scheduler import write_missing_perturbation_artifacts

        write_missing_perturbation_artifacts(
            output_dir=output_dir,
            instance_id=instance_id,
            perturbation_id=perturbation_id,
            message="not scheduled for this instance",
        )
        return

    instance_dir = output_dir / instance_id
    patch_path = _pi_patch_path(instance_dir, perturbation_id)
    common = {
        "metadata_path": metadata_path,
        "instance_id": instance_id,
        "patch_path": patch_path,
        "output_dir": output_dir,
        "config": run_config,
    }
    if perturbation_id.endswith("pi_vtest.v1"):
        from earnbench.adapters.swebench_pi_vtest import run_pi_vtest_grading

        run_pi_vtest_grading(**common, run_id=f"pi_vtest_{instance_id}")
        return
    if perturbation_id.endswith("pi_verif.v1"):
        from earnbench.adapters.swebench_pi_verif import run_pi_verif_grading

        run_pi_verif_grading(**common, run_id=f"pi_verif_{instance_id}")
        return
    if perturbation_id.endswith("pi_env.v1"):
        from earnbench.adapters.swebench_pi_env import run_pi_env_grading

        run_pi_env_grading(**common, run_id=f"pi_env_{instance_id}")
        return
    from earnbench.scheduler import write_missing_perturbation_artifacts

    write_missing_perturbation_artifacts(
        output_dir=output_dir,
        instance_id=instance_id,
        perturbation_id=perturbation_id,
        message=f"{perturbation_id} harness executor is not implemented",
    )


def _artifact_result_row(
    artifact: dict[str, Any],
    csv_row: dict[str, Any],
    report_payload: dict[str, Any],
) -> dict[str, Any]:
    failed = report_payload.get("failed_mechanisms") or []
    if isinstance(failed, list):
        failed_text = ";".join(str(item) for item in failed)
    else:
        failed_text = str(failed)
    return {
        "injection_id": results_injection_id(artifact),
        "instance_id": str(artifact.get("instance_id", "")),
        "artifact_id": str(artifact.get("artifact_id", "")),
        "arm": str(artifact.get("arm", "")),
        "y0": csv_row.get("y0"),
        "ef_exclude_invalid": csv_row.get("ef_exclude_invalid"),
        "ef_invalid_as_fail": csv_row.get("ef_invalid_as_fail"),
        "failed_mechanisms": failed_text,
        "invalid_pi_count": csv_row.get("invalid_pi_count"),
        "pi_vtest_status": csv_row.get("pi_vtest_status"),
        "pi_verif_status": csv_row.get("pi_verif_status"),
        "pi_env_status": csv_row.get("pi_env_status"),
    }


def run_injection_artifact_pipeline(
    task: InjectionArtifactTask,
) -> InjectionArtifactResult:
    artifact = task.artifact
    artifact_id = str(artifact["artifact_id"])
    instance_id = str(artifact["instance_id"])
    metadata_path = Path(task.metadata_path)
    batch_output = Path(task.output_dir)
    work_root = artifact_work_dir(batch_output, artifact_id)
    run_config = run_config_from_payload(task.run_config_payload)
    log_path = work_root / "pipeline.log"
    patch_path = resolve_patch_ref(task.spec_dir, str(artifact["patch_ref"]))

    def skip_stage(stage: str) -> bool:
        return task.resume and artifact_stage_complete(
            work_root,
            instance_id,
            stage,
        )

    try:
        patch_content = patch_path.read_text(encoding="utf-8")
        if not skip_stage("prepare"):
            _append_log(log_path, "stage prepare start")
            prepare_exploit(
                metadata_path=metadata_path,
                instance_id=instance_id,
                exploit_id=artifact_id,
                patch_content=patch_content,
                output_dir=work_root,
                run_id=task.run_id,
                dataset_revision=task.dataset_revision,
                patch_class="injection_blinded",
                y0_policy="prod_only",
            )
            _append_log(log_path, "stage prepare done")

        if not skip_stage("preflight"):
            _append_log(log_path, "stage preflight start")
            run_preflight_stage(
                metadata_path=metadata_path,
                instance_id=instance_id,
                output_dir=work_root,
                run_config=run_config,
                build_missing_images=task.build_missing_images,
            )
            _append_log(log_path, "stage preflight done")

        if not skip_stage("nominal"):
            _append_log(log_path, "stage nominal start")
            run_exploit_nominal_stage(
                metadata_path=metadata_path,
                instance_id=instance_id,
                output_dir=work_root,
                run_config=run_config,
                run_id=task.run_id,
                y0_policy="prod_only",
            )
            _append_log(log_path, "stage nominal done")

        for perturbation_id in BATCH_PI_ORDER:
            if not skip_stage(perturbation_id):
                _append_log(log_path, f"stage {perturbation_id} start")
                run_injection_perturbation_stage(
                    metadata_path=metadata_path,
                    instance_id=instance_id,
                    output_dir=work_root,
                    perturbation_id=perturbation_id,
                    run_config=run_config,
                    scheduled=task.scheduled_perturbations,
                )
                _append_log(log_path, f"stage {perturbation_id} done")

        csv_row = aggregate_instance(
            metadata_path=metadata_path,
            output_dir=work_root,
            instance_id=instance_id,
            scheduled_perturbations=task.scheduled_perturbations,
            run_id=task.run_id,
        )
        report_path = work_root / instance_id / "report.json"
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        row = _artifact_result_row(artifact, csv_row, report_payload)
        return InjectionArtifactResult(artifact_id=artifact_id, row=row, failure=None)
    except Exception as exc:
        logger.exception("artifact %s failed", artifact_id)
        return InjectionArtifactResult(
            artifact_id=artifact_id,
            row=None,
            failure={
                "artifact_id": artifact_id,
                "instance_id": instance_id,
                "stage": "pipeline",
                "error": str(exc),
                "timestamp_utc": utc_timestamp(),
            },
        )


def write_injection_results_csv(
    output_dir: Path,
    rows: dict[str, dict[str, Any]],
) -> Path:
    path = output_dir / INJECTION_RESULTS_CSV
    ordered = [rows[key] for key in sorted(rows)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULTS_COLUMNS)
        writer.writeheader()
        writer.writerows(ordered)
    return path


def write_failures_csv(output_dir: Path, rows: list[dict[str, Any]]) -> Path:
    path = output_dir / FAILURES_CSV
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FAILURE_COLUMNS)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row.get("artifact_id", "")))
    return path


def run_injection_batch(config: InjectionBatchConfig) -> dict[str, Any]:
    """Execute blind injection grading for all evaluator-manifest artifacts."""
    evaluator = load_evaluator_manifest(config.evaluator_manifest_path)
    artifacts = evaluator.get("artifacts", [])
    if not isinstance(artifacts, list):
        msg = "evaluator manifest artifacts must be a list"
        raise ValueError(msg)

    lockfile_candidate = config.evaluator_manifest_path.parent / "blind_lockfile.json"
    spec_dir = config.evaluator_manifest_path.parent
    if lockfile_candidate.is_file():
        lock_payload = json.loads(lockfile_candidate.read_text(encoding="utf-8"))
        recorded_spec_dir = lock_payload.get("spec_dir")
        if recorded_spec_dir:
            spec_dir = Path(str(recorded_spec_dir))

    config.output_dir.mkdir(parents=True, exist_ok=True)
    run_config_payload = run_config_to_payload(config.run_config)

    tasks: list[InjectionArtifactTask] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        instance_id = str(artifact["instance_id"])
        instance = load_verified_instance(config.metadata_path, instance_id)
        scheduled = supported_perturbations(instance_id, instance.fail_to_pass)
        tasks.append(
            InjectionArtifactTask(
                artifact=artifact,
                spec_dir=spec_dir,
                metadata_path=str(config.metadata_path),
                output_dir=str(config.output_dir),
                run_config_payload=run_config_payload,
                run_id=config.run_id,
                dataset_revision=config.dataset_revision,
                build_missing_images=config.build_missing_images,
                resume=config.resume,
                scheduled_perturbations=scheduled,
            )
        )

    rows: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    progress = BatchProgress(total=len(tasks))

    if config.workers <= 1:
        for task in tasks:
            artifact_id = str(task.artifact["artifact_id"])
            result = run_injection_artifact_pipeline(task)
            if result.row is not None:
                rows[result.artifact_id] = result.row
            if result.failure is not None:
                failures.append(result.failure)
                progress.failed += 1
                progress.render(instance_id=artifact_id, message="failed")
            else:
                progress.completed += 1
                progress.render(instance_id=artifact_id, message="complete")
    else:
        controller = _InterruptController()
        controller.install()
        try:
            with ProcessPoolExecutor(max_workers=config.workers) as pool:
                futures = {
                    pool.submit(run_injection_artifact_pipeline, task): task
                    for task in tasks
                }
                for future in as_completed(futures):
                    if controller.requested:
                        break
                    task = futures[future]
                    artifact_id = str(task.artifact["artifact_id"])
                    result = future.result()
                    if result.row is not None:
                        rows[result.artifact_id] = result.row
                    if result.failure is not None:
                        failures.append(result.failure)
                        progress.failed += 1
                        progress.render(instance_id=artifact_id, message="failed")
                    else:
                        progress.completed += 1
                        progress.render(instance_id=artifact_id, message="complete")
        finally:
            controller.restore()

    results_path = write_injection_results_csv(config.output_dir, rows)
    failures_path = write_failures_csv(config.output_dir, failures)
    manifest_payload = {
        "run_id": config.run_id,
        "evaluator_manifest": str(config.evaluator_manifest_path.resolve()),
        "metadata_path": str(config.metadata_path.resolve()),
        "artifact_count": len(tasks),
        "completed_artifacts": len(rows),
        "failed_artifacts": len(failures),
        "injection_results_csv": str(results_path.resolve()),
        "failures_csv": str(failures_path.resolve()),
        "git_commit": resolve_git_commit(),
        "timestamp_utc": utc_timestamp(),
    }
    manifest_path = config.output_dir / RUN_MANIFEST_JSON
    _write_json(manifest_path, manifest_payload)

    return {
        "completed_artifacts": len(rows),
        "failed_artifacts": len(failures),
        "injection_results_csv": str(results_path),
        "run_manifest_json": str(manifest_path),
    }
