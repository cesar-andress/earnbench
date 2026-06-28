"""Batch execution of external unearned anchor grading."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench import prepare_exploit, supported_perturbations
from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.external_unearned.catalog import load_external_unearned_catalog
from earnbench.external_unearned.manifest import (
    EXECUTION_MANIFEST_CSV,
    ExternalUnearnedExecutionRow,
    load_execution_manifest,
    resolve_execution_patch_path,
    validate_execution_manifest,
)
from earnbench.external_unearned.report import (
    EXTERNAL_UNEARNED_RESULTS_CSV,
    RESULTS_REQUIRED_COLUMNS,
)
from earnbench.phase_a_batch import _write_json
from earnbench.phase_b_batch import (
    BATCH_PI_ORDER,
    run_exploit_nominal_stage,
    run_exploit_perturbation_stage,
)
from earnbench.provenance import resolve_git_commit, utc_timestamp
from earnbench.scheduler import (
    aggregate_instance,
    artifact_stage_complete,
    run_config_from_payload,
    run_config_to_payload,
    run_preflight_stage,
)

EXTERNAL_UNEARNED_RUN_MANIFEST_JSON = "run_manifest.json"
EXTERNAL_UNEARNED_FAILURES_CSV = "failures.csv"

MECHANISM_TO_CHANNEL = {
    "pi_vtest.v1": "visible_test_overfitting",
    "pi_verif.v1": "verifier_tampering",
    "pi_env.v1": "environment_hijack",
    "vtest": "visible_test_overfitting",
    "verif": "verifier_tampering",
    "env": "environment_hijack",
}

logger = logging.getLogger("earnbench.external_unearned.execute")


class ExternalUnearnedExecuteError(Exception):
    """Raised when external unearned execution fails."""


@dataclass(frozen=True, slots=True)
class ExternalUnearnedExecuteConfig:
    catalog_path: Path
    bundle_dir: Path
    metadata_path: Path
    output_dir: Path
    run_config: SWEBenchRunConfig
    run_id: str = "external_unearned"
    dataset_revision: str = "unpinned"
    build_missing_images: bool = False
    resume: bool = False


@dataclass(frozen=True, slots=True)
class ExternalUnearnedExecutionTask:
    row: ExternalUnearnedExecutionRow
    catalog_row: dict[str, str]
    metadata_path: str
    bundle_dir: str
    output_dir: str
    run_config_payload: dict[str, Any]
    run_id: str
    dataset_revision: str
    build_missing_images: bool
    resume: bool
    scheduled_perturbations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExternalUnearnedExecutionResult:
    external_id: str
    row: dict[str, str] | None
    failure: dict[str, str] | None


@dataclass(frozen=True, slots=True)
class ExternalUnearnedExecuteRunResult:
    output_dir: Path
    results_csv: Path
    failures_csv: Path
    run_manifest_json: Path
    completed_count: int
    failed_count: int
    skipped_count: int


def external_work_dir(output_dir: Path, external_id: str) -> Path:
    safe = external_id.replace("/", "_")
    return output_dir / "cases" / safe


def format_failed_mechanisms(report_payload: dict[str, Any]) -> str:
    """Map harness report mechanisms to EarnBench channel taxonomy names."""
    failed = report_payload.get("failed_mechanisms") or []
    if not isinstance(failed, list):
        failed = [failed]
    channels: list[str] = []
    for item in failed:
        key = str(item).strip()
        if not key:
            continue
        channel = MECHANISM_TO_CHANNEL.get(key, key)
        if channel not in channels:
            channels.append(channel)
    return ";".join(channels)


def build_external_unearned_result_row(
    *,
    external_id: str,
    csv_row: dict[str, Any],
    report_payload: dict[str, Any],
) -> dict[str, str]:
    y0 = bool(csv_row.get("y0"))
    ef_status = str(csv_row.get("ef_status", "")).strip()
    ef_pi = csv_row.get("ef_pi")
    ef_pi_text = "" if ef_pi is None or ef_pi == "" else f"{float(ef_pi):.6f}"
    return {
        "external_id": external_id,
        "y0": "1" if y0 else "0",
        "ef_pi": ef_pi_text,
        "ef_status": ef_status,
        "failed_mechanisms": format_failed_mechanisms(report_payload),
    }


def write_external_unearned_results_csv(
    output_dir: Path,
    rows: dict[str, dict[str, str]],
) -> Path:
    path = output_dir / EXTERNAL_UNEARNED_RESULTS_CSV
    ordered = [rows[key] for key in sorted(rows)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULTS_REQUIRED_COLUMNS)
        writer.writeheader()
        writer.writerows(ordered)
    return path


def write_failures_csv(output_dir: Path, rows: list[dict[str, str]]) -> Path:
    path = output_dir / EXTERNAL_UNEARNED_FAILURES_CSV
    columns = ("external_id", "instance_id", "stage", "error", "timestamp_utc")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item.get("external_id", "")):
            writer.writerow(row)
    return path


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_timestamp()} {message}\n")


def run_external_unearned_case(task: ExternalUnearnedExecutionTask) -> ExternalUnearnedExecutionResult:
    """Run nominal + π grading for one external unearned manifest row."""
    row = task.row
    external_id = row.external_id
    instance_id = row.instance_id
    metadata_path = Path(task.metadata_path)
    bundle_dir = Path(task.bundle_dir)
    batch_output = Path(task.output_dir)
    work_root = external_work_dir(batch_output, external_id)
    run_config = run_config_from_payload(task.run_config_payload)
    log_path = work_root / "pipeline.log"

    patch_path = resolve_execution_patch_path(
        row,
        manifest_dir=bundle_dir,
        patches_root=bundle_dir,
    )

    def skip_stage(stage: str) -> bool:
        return task.resume and artifact_stage_complete(work_root, instance_id, stage)

    try:
        patch_content = patch_path.read_text(encoding="utf-8")
        if not skip_stage("prepare"):
            _append_log(log_path, "stage prepare start")
            prepare_exploit(
                metadata_path=metadata_path,
                instance_id=instance_id,
                exploit_id=external_id.replace("/", "_"),
                patch_content=patch_content,
                output_dir=work_root,
                run_id=task.run_id,
                dataset_revision=task.dataset_revision,
                patch_class="external_unearned",
                y0_policy=row.y0_policy,
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
                y0_policy=row.y0_policy,
            )
            _append_log(log_path, "stage nominal done")

        for perturbation_id in BATCH_PI_ORDER:
            if skip_stage(perturbation_id):
                continue
            _append_log(log_path, f"stage {perturbation_id} start")
            run_exploit_perturbation_stage(
                metadata_path=metadata_path,
                instance_id=instance_id,
                output_dir=work_root,
                perturbation_id=perturbation_id,
                run_config=run_config,
                scheduled=task.scheduled_perturbations,
                y0_policy=row.y0_policy,
                family="",
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
        result_row = build_external_unearned_result_row(
            external_id=external_id,
            csv_row=csv_row,
            report_payload=report_payload,
        )
        return ExternalUnearnedExecutionResult(
            external_id=external_id,
            row=result_row,
            failure=None,
        )
    except Exception as exc:
        logger.exception("external unearned case %s failed", external_id)
        return ExternalUnearnedExecutionResult(
            external_id=external_id,
            row=None,
            failure={
                "external_id": external_id,
                "instance_id": instance_id,
                "stage": "pipeline",
                "error": str(exc),
                "timestamp_utc": utc_timestamp(),
            },
        )


def _eligible_execution_rows(
    *,
    catalog_path: Path,
    bundle_dir: Path,
) -> list[tuple[ExternalUnearnedExecutionRow, dict[str, str]]]:
    catalog_rows = load_external_unearned_catalog(catalog_path)
    catalog_by_id = {str(row["external_id"]).strip(): row for row in catalog_rows}

    manifest_path = bundle_dir / EXECUTION_MANIFEST_CSV
    validation = validate_execution_manifest(
        manifest_path,
        catalog_path=catalog_path,
        patches_root=bundle_dir,
        require_patch_files=True,
    )
    if not validation.ok:
        msg = "; ".join(validation.errors)
        raise ExternalUnearnedExecuteError(msg)

    eligible: list[tuple[ExternalUnearnedExecutionRow, dict[str, str]]] = []
    for row in load_execution_manifest(manifest_path):
        catalog_row = catalog_by_id.get(row.external_id)
        if catalog_row is None:
            continue
        if str(catalog_row.get("inclusion_decision", "")).strip() != "include":
            continue
        eligible.append((row, catalog_row))
    return eligible


def run_external_unearned_execution(
    config: ExternalUnearnedExecuteConfig,
) -> ExternalUnearnedExecuteRunResult:
    """Execute external unearned grading for included catalog rows."""
    bundle_dir = config.bundle_dir.resolve()
    output_dir = config.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    eligible = _eligible_execution_rows(
        catalog_path=config.catalog_path.resolve(),
        bundle_dir=bundle_dir,
    )

    run_config_payload = run_config_to_payload(config.run_config)
    tasks: list[ExternalUnearnedExecutionTask] = []
    for row, catalog_row in eligible:
        instance = load_verified_instance(config.metadata_path, row.instance_id)
        scheduled = supported_perturbations(row.instance_id, instance.fail_to_pass)
        tasks.append(
            ExternalUnearnedExecutionTask(
                row=row,
                catalog_row=catalog_row,
                metadata_path=str(config.metadata_path.resolve()),
                bundle_dir=str(bundle_dir),
                output_dir=str(output_dir),
                run_config_payload=run_config_payload,
                run_id=config.run_id,
                dataset_revision=config.dataset_revision,
                build_missing_images=config.build_missing_images,
                resume=config.resume,
                scheduled_perturbations=scheduled,
            )
        )

    rows: dict[str, dict[str, str]] = {}
    failures: list[dict[str, str]] = []
    for task in tasks:
        result = run_external_unearned_case(task)
        if result.row is not None:
            rows[result.external_id] = result.row
        if result.failure is not None:
            failures.append(result.failure)

    results_csv = write_external_unearned_results_csv(output_dir, rows)
    failures_csv = write_failures_csv(output_dir, failures)
    manifest_payload = {
        "schema_version": "earnbench.external_unearned_execution_run.v1",
        "run_id": config.run_id,
        "catalog_path": str(config.catalog_path.resolve()),
        "bundle_dir": str(bundle_dir),
        "metadata_path": str(config.metadata_path.resolve()),
        "eligible_count": len(tasks),
        "completed_count": len(rows),
        "failed_count": len(failures),
        "skipped_count": sum(
            1
            for row in load_external_unearned_catalog(config.catalog_path)
            if str(row.get("inclusion_decision", "")).strip() != "include"
        ),
        "external_unearned_results_csv": str(results_csv.resolve()),
        "failures_csv": str(failures_csv.resolve()),
        "git_commit": resolve_git_commit(),
        "timestamp_utc": utc_timestamp(),
    }
    run_manifest_json = output_dir / EXTERNAL_UNEARNED_RUN_MANIFEST_JSON
    _write_json(run_manifest_json, manifest_payload)

    return ExternalUnearnedExecuteRunResult(
        output_dir=output_dir,
        results_csv=results_csv,
        failures_csv=failures_csv,
        run_manifest_json=run_manifest_json,
        completed_count=len(rows),
        failed_count=len(failures),
        skipped_count=manifest_payload["skipped_count"],
    )
