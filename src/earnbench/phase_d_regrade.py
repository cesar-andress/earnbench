"""Phase D — re-grade Phase C agent patches under frozen EF@Π semantics."""

from __future__ import annotations

import csv
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench import prepare_exploit, supported_perturbations
from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.agents.schemas import ATTEMPT_CSV_COLUMNS, AttemptRecord
from earnbench.phase_a_batch import _write_json
from earnbench.phase_b_batch import (
    BATCH_PI_ORDER,
    run_exploit_nominal_stage,
    run_exploit_perturbation_stage,
)
from earnbench.provenance import build_provenance, resolve_git_commit, utc_timestamp
from earnbench.scheduler import (
    aggregate_instance,
    artifact_stage_complete,
    run_preflight_stage,
)

AGENT_RESULTS_CSV = "agent_results.csv"
PHASE_D_SUMMARY_JSON = "phase_d_summary.json"
FAILURES_CSV = "failures.csv"
RUN_MANIFEST_JSON = "run_manifest.json"
CELLS_DIR = "cells"

PHASE_D_SCHEMA_VERSION = "earnbench_phase_d.v1"

AGENT_RESULTS_COLUMNS = (
    "agent",
    "provider",
    "model",
    "instance_id",
    "replicate",
    "attempt_status",
    "patch_path",
    "y0",
    "y_vtest",
    "y_verif",
    "y_env",
    "pi_vtest_status",
    "pi_verif_status",
    "pi_env_status",
    "valid_pi_count",
    "ef_pi",
    "ef_exclude_invalid",
    "ef_invalid_as_fail",
    "invalid_pi_count",
    "invalid_pi_rate",
    "ef_sensitivity_gap",
    "ef_status",
    "failed_mechanisms",
    "run_id",
    "config_digest",
)

FAILURE_COLUMNS = (
    "agent",
    "instance_id",
    "replicate",
    "stage",
    "error",
    "timestamp_utc",
)

logger = logging.getLogger("earnbench.phase_d")


class PhaseDError(Exception):
    """Raised when Phase D preparation or execution fails."""


@dataclass(frozen=True, slots=True)
class PhaseDRegradeConfig:
    phase_c_run: Path
    metadata_path: Path
    output_dir: Path
    run_config: SWEBenchRunConfig
    workers: int = 1
    resume: bool = False
    run_id: str = ""
    dataset_revision: str = "unpinned"
    build_missing_images: bool = False


@dataclass(frozen=True, slots=True)
class PhaseDTask:
    attempt: AttemptRecord
    patch_path: Path
    work_root: Path
    scheduled_perturbations: tuple[str, ...]

    @property
    def task_key(self) -> str:
        return (
            f"{self.attempt.agent}:{self.attempt.instance_id}:"
            f"r{self.attempt.replicate}"
        )


@dataclass(frozen=True, slots=True)
class PhaseDCellResult:
    task_key: str
    row: dict[str, Any] | None
    failure: dict[str, str] | None


@dataclass(frozen=True, slots=True)
class PhaseDRunResult:
    output_dir: Path
    agent_results_csv: Path
    failures_path: Path
    summary_path: Path
    graded_count: int
    failure_count: int
    skipped_count: int


@dataclass(frozen=True, slots=True)
class PhaseDSummary:
    run_id: str
    graded_count: int
    failure_count: int
    skipped_ineligible_count: int
    by_agent: dict[str, dict[str, int]]
    summarized_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PHASE_D_SCHEMA_VERSION,
            "run_id": self.run_id,
            "graded_count": self.graded_count,
            "failure_count": self.failure_count,
            "skipped_ineligible_count": self.skipped_ineligible_count,
            "by_agent": self.by_agent,
            "summarized_at_utc": self.summarized_at_utc,
        }


def task_key(agent: str, instance_id: str, replicate: int) -> str:
    return f"{agent}:{instance_id}:r{replicate}"


def agent_work_root(output_dir: Path, agent: str, replicate: int) -> Path:
    agent_root = output_dir / CELLS_DIR / agent
    if replicate == 0:
        return agent_root
    return agent_root / f"r{replicate}"


def load_attempts_csv(path: Path) -> list[AttemptRecord]:
    if not path.is_file():
        msg = f"attempts.csv not found: {path}"
        raise PhaseDError(msg)
    records: list[AttemptRecord] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            msg = f"{path} is empty or missing a header row"
            raise PhaseDError(msg)
        missing = [col for col in ATTEMPT_CSV_COLUMNS if col not in reader.fieldnames]
        if missing:
            msg = f"{path} missing required columns: {', '.join(missing)}"
            raise PhaseDError(msg)
        for index, raw in enumerate(reader, start=2):
            try:
                records.append(AttemptRecord.from_dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                msg = f"{path}:{index} invalid attempt row: {exc}"
                raise PhaseDError(msg) from exc
    return records


def filter_eligible_attempts(records: list[AttemptRecord]) -> tuple[list[AttemptRecord], int]:
    eligible: list[AttemptRecord] = []
    skipped = 0
    for record in records:
        if record.status != "ok" or not str(record.patch_path).strip():
            skipped += 1
            continue
        eligible.append(record)
    return eligible, skipped


def resolve_patch_path(phase_c_run: Path, patch_path: str) -> Path:
    candidate = Path(patch_path)
    if candidate.is_file():
        return candidate.resolve()
    relative = phase_c_run / patch_path
    if relative.is_file():
        return relative.resolve()
    return relative


def _format_failed_mechanisms(report_payload: dict[str, Any]) -> str:
    failed = report_payload.get("failed_mechanisms") or []
    if isinstance(failed, list):
        return ";".join(str(item) for item in failed)
    return str(failed)



def build_agent_result_row(
    *,
    attempt: AttemptRecord,
    csv_row: dict[str, Any],
    report_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "agent": attempt.agent,
        "provider": attempt.provider,
        "model": attempt.model,
        "instance_id": attempt.instance_id,
        "replicate": attempt.replicate,
        "attempt_status": attempt.status,
        "patch_path": attempt.patch_path,
        "y0": csv_row.get("y0"),
        "y_vtest": csv_row.get("y_vtest"),
        "y_verif": csv_row.get("y_verif"),
        "y_env": csv_row.get("y_env"),
        "pi_vtest_status": csv_row.get("pi_vtest_status"),
        "pi_verif_status": csv_row.get("pi_verif_status"),
        "pi_env_status": csv_row.get("pi_env_status"),
        "valid_pi_count": csv_row.get("valid_pi_count"),
        "ef_pi": csv_row.get("ef_pi"),
        "ef_exclude_invalid": csv_row.get("ef_exclude_invalid"),
        "ef_invalid_as_fail": csv_row.get("ef_invalid_as_fail"),
        "invalid_pi_count": csv_row.get("invalid_pi_count"),
        "invalid_pi_rate": csv_row.get("invalid_pi_rate"),
        "ef_sensitivity_gap": csv_row.get("ef_sensitivity_gap"),
        "ef_status": csv_row.get("ef_status"),
        "failed_mechanisms": _format_failed_mechanisms(report_payload),
        "run_id": csv_row.get("run_id"),
        "config_digest": csv_row.get("config_digest"),
    }


def write_agent_results_csv(
    output_dir: Path,
    rows: dict[str, dict[str, Any]],
) -> Path:
    path = output_dir / AGENT_RESULTS_CSV
    ordered = [rows[key] for key in sorted(rows)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AGENT_RESULTS_COLUMNS)
        writer.writeheader()
        writer.writerows(ordered)
    return path


def write_failures_csv(output_dir: Path, rows: list[dict[str, str]]) -> Path:
    path = output_dir / FAILURES_CSV
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FAILURE_COLUMNS)
        writer.writeheader()
        writer.writerows(
            sorted(
                rows,
                key=lambda row: (
                    row.get("agent", ""),
                    row.get("instance_id", ""),
                    row.get("replicate", ""),
                ),
            )
        )
    return path


def load_agent_results_rows(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return {}
        for raw in reader:
            key = task_key(
                str(raw.get("agent", "")),
                str(raw.get("instance_id", "")),
                int(raw.get("replicate") or 0),
            )
            rows[key] = {col: raw.get(col, "") for col in AGENT_RESULTS_COLUMNS}
    return rows


def _cell_complete(work_root: Path, instance_id: str) -> bool:
    return artifact_stage_complete(work_root, instance_id, "aggregate")


def run_phase_d_cell(
    *,
    task: PhaseDTask,
    metadata_path: Path,
    run_config: SWEBenchRunConfig,
    run_id: str,
    dataset_revision: str,
    build_missing_images: bool,
    resume: bool,
) -> PhaseDCellResult:
    attempt = task.attempt
    instance_id = attempt.instance_id
    work_root = task.work_root
    key = task.task_key

    def skip_stage(stage: str) -> bool:
        return resume and artifact_stage_complete(work_root, instance_id, stage)

    try:
        if not task.patch_path.is_file():
            msg = f"patch file not found: {task.patch_path}"
            raise FileNotFoundError(msg)

        patch_content = task.patch_path.read_text(encoding="utf-8")
        if not skip_stage("prepare"):
            prepare_exploit(
                metadata_path=metadata_path,
                instance_id=instance_id,
                exploit_id=key.replace(":", "_"),
                patch_content=patch_content,
                output_dir=work_root,
                run_id=run_id,
                dataset_revision=dataset_revision,
                patch_class="agent_patch",
                y0_policy="prod_only",
            )

        if not skip_stage("preflight"):
            run_preflight_stage(
                metadata_path=metadata_path,
                instance_id=instance_id,
                output_dir=work_root,
                run_config=run_config,
                build_missing_images=build_missing_images,
            )

        if not skip_stage("nominal"):
            run_exploit_nominal_stage(
                metadata_path=metadata_path,
                instance_id=instance_id,
                output_dir=work_root,
                run_config=run_config,
                run_id=run_id,
                y0_policy="prod_only",
            )

        for perturbation_id in BATCH_PI_ORDER:
            if not skip_stage(perturbation_id):
                run_exploit_perturbation_stage(
                    metadata_path=metadata_path,
                    instance_id=instance_id,
                    output_dir=work_root,
                    perturbation_id=perturbation_id,
                    run_config=run_config,
                    scheduled=task.scheduled_perturbations,
                    y0_policy="prod_only",
                    family="",
                )

        csv_row = aggregate_instance(
            metadata_path=metadata_path,
            output_dir=work_root,
            instance_id=instance_id,
            scheduled_perturbations=task.scheduled_perturbations,
            run_id=run_id,
        )
        report_path = work_root / instance_id / "report.json"
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        row = build_agent_result_row(
            attempt=attempt,
            csv_row=csv_row,
            report_payload=report_payload,
        )
        return PhaseDCellResult(task_key=key, row=row, failure=None)
    except Exception as exc:
        logger.exception("Phase D cell failed for %s", key)
        return PhaseDCellResult(
            task_key=key,
            row=None,
            failure={
                "agent": attempt.agent,
                "instance_id": instance_id,
                "replicate": str(attempt.replicate),
                "stage": "pipeline",
                "error": str(exc),
                "timestamp_utc": utc_timestamp(),
            },
        )


def _build_tasks(
    *,
    config: PhaseDRegradeConfig,
    attempts: list[AttemptRecord],
) -> list[PhaseDTask]:
    metadata_path = config.metadata_path.resolve()
    phase_c_run = config.phase_c_run.resolve()
    tasks: list[PhaseDTask] = []
    for attempt in attempts:
        record = load_verified_instance(metadata_path, attempt.instance_id)
        scheduled = supported_perturbations(
            attempt.instance_id,
            record.fail_to_pass,
        )
        tasks.append(
            PhaseDTask(
                attempt=attempt,
                patch_path=resolve_patch_path(phase_c_run, attempt.patch_path),
                work_root=agent_work_root(
                    config.output_dir,
                    attempt.agent,
                    attempt.replicate,
                ),
                scheduled_perturbations=scheduled,
            )
        )
    return tasks


def write_run_manifest(
    output_dir: Path,
    *,
    config: PhaseDRegradeConfig,
    started_at_utc: str,
    completed_at_utc: str | None,
    summary: dict[str, Any],
) -> Path:
    path = output_dir / RUN_MANIFEST_JSON
    payload = {
        "schema_version": PHASE_D_SCHEMA_VERSION,
        "run_id": config.run_id,
        "phase_c_run": str(config.phase_c_run.resolve()),
        "metadata_path": str(config.metadata_path.resolve()),
        "output_dir": str(config.output_dir.resolve()),
        "workers": config.workers,
        "resume": config.resume,
        "dataset_revision": config.dataset_revision,
        "build_missing_images": config.build_missing_images,
        "run_config": {
            "workers": config.run_config.workers,
            "max_parallel_containers": config.run_config.max_parallel_containers,
            "max_parallel_builds": config.run_config.max_parallel_builds,
            "reuse_images": config.run_config.reuse_images,
            "allow_build": config.run_config.allow_build,
            "cache_dir": (
                str(config.run_config.cache_dir)
                if config.run_config.cache_dir is not None
                else None
            ),
            "timeout_seconds": config.run_config.timeout_seconds,
        },
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "provenance": build_provenance(git_commit=resolve_git_commit()).to_dict(),
        "summary": summary,
    }
    _write_json(path, payload)
    return path


def run_phase_d(config: PhaseDRegradeConfig) -> PhaseDRunResult:
    """Re-grade eligible Phase C attempts and write ``agent_results.csv``."""
    phase_c_run = config.phase_c_run.resolve()
    attempts_path = phase_c_run / "attempts.csv"
    all_attempts = load_attempts_csv(attempts_path)
    eligible, skipped_ineligible = filter_eligible_attempts(all_attempts)

    output_dir = config.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    effective_run_id = config.run_id or f"phase_d_{output_dir.name}"
    started_at = utc_timestamp()

    existing_rows = load_agent_results_rows(output_dir / AGENT_RESULTS_CSV) if config.resume else {}
    result_rows: dict[str, dict[str, Any]] = dict(existing_rows)
    failures: list[dict[str, str]] = []
    skipped_resume = 0

    tasks = _build_tasks(config=config, attempts=eligible)
    pending: list[PhaseDTask] = []
    for task in tasks:
        if config.resume and task.task_key in result_rows:
            if _cell_complete(task.work_root, task.attempt.instance_id):
                skipped_resume += 1
                continue
        pending.append(task)

    def _run_one(task: PhaseDTask) -> PhaseDCellResult:
        return run_phase_d_cell(
            task=task,
            metadata_path=config.metadata_path.resolve(),
            run_config=config.run_config,
            run_id=effective_run_id,
            dataset_revision=config.dataset_revision,
            build_missing_images=config.build_missing_images,
            resume=config.resume,
        )

    if config.workers <= 1:
        for task in pending:
            result = _run_one(task)
            if result.row is not None:
                result_rows[result.task_key] = result.row
            if result.failure is not None:
                failures.append(result.failure)
    else:
        with ThreadPoolExecutor(max_workers=config.workers) as pool:
            futures = {pool.submit(_run_one, task): task for task in pending}
            for future in as_completed(futures):
                result = future.result()
                if result.row is not None:
                    result_rows[result.task_key] = result.row
                if result.failure is not None:
                    failures.append(result.failure)

    agent_results_csv = write_agent_results_csv(output_dir, result_rows)
    failures_path = write_failures_csv(output_dir, failures)

    summary = PhaseDSummary(
        run_id=effective_run_id,
        graded_count=len(result_rows),
        failure_count=len(failures),
        skipped_ineligible_count=skipped_ineligible,
        by_agent=_summarize_by_agent(result_rows),
        summarized_at_utc=utc_timestamp(),
    )
    summary_path = output_dir / PHASE_D_SUMMARY_JSON
    summary_path.write_text(
        json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    completed_at = utc_timestamp()
    write_run_manifest(
        output_dir,
        config=config,
        started_at_utc=started_at,
        completed_at_utc=completed_at,
        summary={
            "graded_count": summary.graded_count,
            "failure_count": summary.failure_count,
            "skipped_ineligible_count": summary.skipped_ineligible_count,
            "skipped_resume_count": skipped_resume,
            "eligible_attempt_count": len(eligible),
        },
    )

    return PhaseDRunResult(
        output_dir=output_dir,
        agent_results_csv=agent_results_csv,
        failures_path=failures_path,
        summary_path=summary_path,
        graded_count=len(result_rows),
        failure_count=len(failures),
        skipped_count=skipped_ineligible + skipped_resume,
    )


def _summarize_by_agent(rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    by_agent: dict[str, dict[str, int]] = {}
    for row in rows.values():
        agent = str(row.get("agent", ""))
        bucket = by_agent.setdefault(agent, {"graded": 0, "y0_pass": 0, "ef_defined": 0})
        bucket["graded"] += 1
        if str(row.get("y0", "")).strip().lower() in {"1", "true", "yes"}:
            bucket["y0_pass"] += 1
        if str(row.get("ef_status", "")).strip().lower() == "defined":
            bucket["ef_defined"] += 1
    return by_agent


def summarize_phase_d(*, output_dir: Path) -> PhaseDSummary:
    """Summarize a completed Phase D run directory."""
    output_dir = output_dir.resolve()
    manifest_path = output_dir / RUN_MANIFEST_JSON
    if not manifest_path.is_file():
        msg = f"run manifest not found: {manifest_path}"
        raise PhaseDError(msg)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = load_agent_results_rows(output_dir / AGENT_RESULTS_CSV)
    summary = PhaseDSummary(
        run_id=str(manifest.get("run_id", "")),
        graded_count=len(rows),
        failure_count=_count_failures(output_dir / FAILURES_CSV),
        skipped_ineligible_count=int(
            manifest.get("summary", {}).get("skipped_ineligible_count", 0)
        ),
        by_agent=_summarize_by_agent(rows),
        summarized_at_utc=utc_timestamp(),
    )
    summary_path = output_dir / PHASE_D_SUMMARY_JSON
    summary_path.write_text(
        json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _count_failures(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return 0
        return sum(1 for _ in reader)


__all__ = [
    "AGENT_RESULTS_COLUMNS",
    "AGENT_RESULTS_CSV",
    "PhaseDCellResult",
    "PhaseDError",
    "PhaseDRegradeConfig",
    "PhaseDRunResult",
    "PhaseDSummary",
    "PhaseDTask",
    "agent_work_root",
    "build_agent_result_row",
    "filter_eligible_attempts",
    "load_attempts_csv",
    "resolve_patch_path",
    "run_phase_d",
    "run_phase_d_cell",
    "summarize_phase_d",
    "task_key",
    "write_agent_results_csv",
]
