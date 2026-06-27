"""Parallel Phase A execution scheduler for SWE-bench golden validation."""

from __future__ import annotations

import csv
import json
import logging
import signal
import sys
from collections.abc import Iterable
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench import prepare_smoke, supported_perturbations
from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.adapters.swebench_nominal import run_nominal_grading
from earnbench.adapters.swebench_pi_env import run_pi_env_grading
from earnbench.adapters.swebench_pi_verif import run_pi_verif_grading
from earnbench.adapters.swebench_preflight import run_swebench_preflight
from earnbench.metrics import compute_earned_fraction
from earnbench.outcomes import NominalOutcome, OutcomeStatus, PerturbationResult
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1_ID
from earnbench.reports import EarnedFractionStatus

STATE_FILENAME = "phase_a_scheduler_state.json"
CSV_FILENAME = "golden_validation.csv"
CSV_COLUMNS = (
    "instance_id",
    "repo",
    "y0",
    "y_vtest",
    "y_verif",
    "y_env",
    "pi_vtest_status",
    "pi_verif_status",
    "pi_env_status",
    "valid_pi_count",
    "ef_pi",
    "ef_status",
    "false_unearned",
    "retained",
    "exclude_reason",
    "run_id",
    "config_digest",
)
PI_IDS = (PI_VTEST_V1_ID, PI_VERIF_V1_ID, PI_ENV_V1_ID)
STAGE_ORDER = ("prepare", "preflight", "nominal", "aggregate")

logger = logging.getLogger("earnbench.scheduler")


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class PhaseASchedulerConfig:
    """Resolved settings for a Phase A batch run."""

    metadata_path: Path
    output_dir: Path
    instance_ids: tuple[str, ...]
    workers: int
    parallel_perturbations: int
    resume: bool
    retry_failed: bool
    run_config: SWEBenchRunConfig
    run_id: str
    dataset_revision: str = "unpinned"
    build_missing_images: bool = False


@dataclass
class JobRecord:
    job_id: str
    instance_id: str
    stage: str
    status: JobStatus = JobStatus.PENDING
    attempts: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "instance_id": self.instance_id,
            "stage": self.stage,
            "status": self.status.value,
            "attempts": self.attempts,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> JobRecord:
        return cls(
            job_id=str(payload["job_id"]),
            instance_id=str(payload["instance_id"]),
            stage=str(payload["stage"]),
            status=JobStatus(str(payload["status"])),
            attempts=int(payload.get("attempts", 0)),
            error=payload.get("error"),
        )


@dataclass
class SchedulerState:
    run_id: str
    instance_ids: list[str]
    jobs: dict[str, JobRecord] = field(default_factory=dict)
    interrupted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "instance_ids": self.instance_ids,
            "interrupted": self.interrupted,
            "jobs": {key: job.to_dict() for key, job in sorted(self.jobs.items())},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SchedulerState:
        jobs_raw = payload.get("jobs", {})
        jobs = {
            str(key): JobRecord.from_dict(value)
            for key, value in jobs_raw.items()
            if isinstance(value, dict)
        }
        return cls(
            run_id=str(payload["run_id"]),
            instance_ids=[str(item) for item in payload.get("instance_ids", [])],
            jobs=jobs,
            interrupted=bool(payload.get("interrupted", False)),
        )


@dataclass(frozen=True, slots=True)
class InstanceTask:
    """Pickle-friendly payload for one instance pipeline worker."""

    instance_id: str
    metadata_path: str
    output_dir: str
    run_id: str
    dataset_revision: str
    build_missing_images: bool
    parallel_perturbations: int
    resume: bool
    retry_failed: bool
    scheduled_perturbations: tuple[str, ...]
    run_config_payload: dict[str, Any]
    job_states: dict[str, str]


@dataclass(frozen=True, slots=True)
class InstancePipelineResult:
    instance_id: str
    jobs: tuple[dict[str, Any], ...]
    csv_row: dict[str, Any] | None
    error: str | None = None


class SchedulerInterrupted(Exception):
    """Raised when the operator requests graceful shutdown."""


def configure_structured_logging(*, verbose: bool = False) -> None:
    """Configure JSON-ish structured logging on stderr."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='{"logger":"%(name)s","level":"%(levelname)s","message":%(message)s}',
        stream=sys.stderr,
        force=True,
    )


def log_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    logger.info("%s", json.dumps(payload, sort_keys=True))


def run_config_to_payload(config: SWEBenchRunConfig) -> dict[str, Any]:
    return {
        "workers": config.workers,
        "max_parallel_containers": config.max_parallel_containers,
        "max_parallel_builds": config.max_parallel_builds,
        "reuse_images": config.reuse_images,
        "allow_build": config.allow_build,
        "cache_dir": str(config.cache_dir) if config.cache_dir is not None else None,
        "timeout_seconds": config.timeout_seconds,
    }


def run_config_from_payload(payload: dict[str, Any]) -> SWEBenchRunConfig:
    cache_raw = payload.get("cache_dir")
    cache_dir = Path(cache_raw) if cache_raw else None
    return SWEBenchRunConfig(
        workers=int(payload["workers"]),
        max_parallel_containers=int(payload["max_parallel_containers"]),
        max_parallel_builds=int(payload["max_parallel_builds"]),
        reuse_images=bool(payload["reuse_images"]),
        allow_build=bool(payload["allow_build"]),
        cache_dir=cache_dir,
        timeout_seconds=int(payload["timeout_seconds"]),
    )


def job_id(instance_id: str, stage: str) -> str:
    return f"{instance_id}:{stage}"


def perturbation_job_id(instance_id: str, perturbation_id: str) -> str:
    return f"{instance_id}:{perturbation_id}"


def state_path(output_dir: Path) -> Path:
    return output_dir / STATE_FILENAME


def csv_path(output_dir: Path) -> Path:
    return output_dir / CSV_FILENAME


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def build_job_graph(instance_ids: Iterable[str]) -> dict[str, JobRecord]:
    jobs: dict[str, JobRecord] = {}
    for instance_id in sorted(instance_ids):
        for stage in STAGE_ORDER:
            jobs[job_id(instance_id, stage)] = JobRecord(
                job_id=job_id(instance_id, stage),
                instance_id=instance_id,
                stage=stage,
            )
        for perturbation_id in PI_IDS:
            jobs[perturbation_job_id(instance_id, perturbation_id)] = JobRecord(
                job_id=perturbation_job_id(instance_id, perturbation_id),
                instance_id=instance_id,
                stage=perturbation_id,
            )
    return jobs


def load_scheduler_state(output_dir: Path) -> SchedulerState | None:
    path = state_path(output_dir)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return SchedulerState.from_dict(payload)


def save_scheduler_state(output_dir: Path, state: SchedulerState) -> None:
    _write_json(state_path(output_dir), state.to_dict())


def artifact_stage_complete(output_dir: Path, instance_id: str, stage: str) -> bool:
    instance_dir = output_dir / instance_id
    if stage == "prepare":
        return (instance_dir / "meta.json").is_file() and (
            instance_dir / "patch" / "prod_only.patch"
        ).is_file()
    if stage == "preflight":
        preflight = instance_dir / "preflight.json"
        if not preflight.is_file():
            return False
        payload = json.loads(preflight.read_text(encoding="utf-8"))
        return isinstance(payload, dict) and payload.get("status") == "ok"
    if stage == "nominal":
        nominal_dir = instance_dir / "nominal"
        return (nominal_dir / "grade.json").is_file() and (
            nominal_dir / "audit.json"
        ).is_file()
    if stage == "aggregate":
        return (instance_dir / "report.json").is_file()
    if stage in PI_IDS:
        artifact_dir = instance_dir / stage
        return (artifact_dir / "grade.json").is_file() and (
            artifact_dir / "audit.json"
        ).is_file()
    return False


def should_run_job(
    record: JobRecord,
    *,
    resume: bool,
    retry_failed: bool,
    output_dir: Path,
) -> bool:
    if record.status is JobStatus.COMPLETED and resume:
        if artifact_stage_complete(output_dir, record.instance_id, record.stage):
            return False
    if record.status is JobStatus.FAILED and resume and not retry_failed:
        return False
    if record.status is JobStatus.SKIPPED and resume:
        return False
    return True


def write_missing_perturbation_artifacts(
    *,
    output_dir: Path,
    instance_id: str,
    perturbation_id: str,
    message: str,
) -> None:
    artifact_dir = output_dir / instance_id / perturbation_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    grade = {
        "instance_id": instance_id,
        "perturbation_id": perturbation_id,
        "status": OutcomeStatus.MISSING.value,
        "success": None,
        "message": message,
    }
    audit = {
        "instance_id": instance_id,
        "perturbation_id": perturbation_id,
        "status": OutcomeStatus.MISSING.value,
        "success": None,
        "message": message,
        "schema_version": "earnbench_audit.v1",
        "config_digest": "",
        "patch_sha256": "",
    }
    _write_json(artifact_dir / "grade.json", grade)
    _write_json(artifact_dir / "audit.json", audit)
    (artifact_dir / "harness.log").write_text(
        f"{perturbation_id} executor not implemented\n{message}\n",
        encoding="utf-8",
    )


def load_grade_status(path: Path) -> str | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    return str(status) if status is not None else None


def load_grade_success(path: Path, status: str) -> bool | None:
    if status != OutcomeStatus.OK.value:
        return None
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    success = payload.get("success")
    return None if success is None else bool(success)


def load_pi_outcome(
    instance_dir: Path,
    perturbation_id: str,
    *,
    scheduled: tuple[str, ...],
) -> PerturbationResult:
    if perturbation_id not in scheduled:
        return PerturbationResult.missing(
            perturbation_id,
            message="not scheduled for this instance",
        )
    grade_path = instance_dir / perturbation_id / "grade.json"
    status_raw = load_grade_status(grade_path)
    if status_raw is None:
        return PerturbationResult.missing(
            perturbation_id,
            message="grade.json missing",
        )
    if status_raw == OutcomeStatus.OK.value:
        success = load_grade_success(grade_path, status_raw)
        if success is None:
            return PerturbationResult.invalid(
                perturbation_id,
                message="grade.json missing success for status=ok",
            )
        return PerturbationResult.ok(perturbation_id, success=success)
    if status_raw == OutcomeStatus.INVALID.value:
        return PerturbationResult.invalid(perturbation_id)
    return PerturbationResult.missing(perturbation_id, message=status_raw)


def build_csv_row(
    *,
    instance_id: str,
    repo: str,
    report_payload: dict[str, Any],
    pi_statuses: dict[str, str],
    pi_successes: dict[str, bool | None],
    run_id: str,
    config_digest: str,
) -> dict[str, Any]:
    y0 = bool(report_payload.get("nominal_success"))
    ef_status = str(report_payload.get("status", EarnedFractionStatus.UNDEFINED.value))
    ef_pi_raw = report_payload.get("earned_fraction")
    ef_pi = None if ef_pi_raw is None else float(ef_pi_raw)
    valid_pi_count = int(report_payload.get("valid_count", 0))
    false_unearned = ef_status == EarnedFractionStatus.DEFINED.value and (
        ef_pi is not None and ef_pi < 1.0
    )
    retained = y0 and ef_status == EarnedFractionStatus.DEFINED.value and ef_pi == 1.0
    exclude_reason: str | None = None
    if not y0:
        exclude_reason = "nominal_failed"
    elif false_unearned:
        exclude_reason = "false_unearned"
    elif ef_status != EarnedFractionStatus.DEFINED.value:
        exclude_reason = str(report_payload.get("reason") or "ef_undefined")

    return {
        "instance_id": instance_id,
        "repo": repo,
        "y0": y0,
        "y_vtest": pi_successes.get(PI_VTEST_V1_ID),
        "y_verif": pi_successes.get(PI_VERIF_V1_ID),
        "y_env": pi_successes.get(PI_ENV_V1_ID),
        "pi_vtest_status": pi_statuses.get(PI_VTEST_V1_ID, OutcomeStatus.MISSING.value),
        "pi_verif_status": pi_statuses.get(PI_VERIF_V1_ID, OutcomeStatus.MISSING.value),
        "pi_env_status": pi_statuses.get(PI_ENV_V1_ID, OutcomeStatus.MISSING.value),
        "valid_pi_count": valid_pi_count,
        "ef_pi": ef_pi,
        "ef_status": ef_status,
        "false_unearned": false_unearned,
        "retained": retained,
        "exclude_reason": exclude_reason,
        "run_id": run_id,
        "config_digest": config_digest,
    }


def aggregate_instance(
    *,
    metadata_path: Path,
    output_dir: Path,
    instance_id: str,
    scheduled_perturbations: tuple[str, ...],
    run_id: str,
) -> dict[str, Any]:
    instance_dir = output_dir / instance_id
    meta = json.loads((instance_dir / "meta.json").read_text(encoding="utf-8"))
    config_digest = str(meta.get("config_digest", ""))
    nominal_grade_path = instance_dir / "nominal" / "grade.json"
    nominal_grade = json.loads(nominal_grade_path.read_text(encoding="utf-8"))
    nominal = NominalOutcome(
        run_id=str(meta.get("run_id") or run_id),
        task_id=instance_id,
        success=bool(nominal_grade.get("success")),
    )
    perturbations = [
        load_pi_outcome(
            instance_dir,
            perturbation_id,
            scheduled=scheduled_perturbations,
        )
        for perturbation_id in PI_IDS
    ]
    report = compute_earned_fraction(nominal, perturbations)
    report_payload = report.to_dict()
    _write_json(instance_dir / "report.json", report_payload)

    pi_statuses = {
        pid: load_grade_status(instance_dir / pid / "grade.json")
        or OutcomeStatus.MISSING.value
        for pid in PI_IDS
    }
    pi_successes = {
        pid: load_grade_success(
            instance_dir / pid / "grade.json",
            pi_statuses[pid],
        )
        for pid in PI_IDS
    }
    csv_row = build_csv_row(
        instance_id=instance_id,
        repo=str(meta.get("repo", "")),
        report_payload=report_payload,
        pi_statuses=pi_statuses,
        pi_successes=pi_successes,
        run_id=str(meta.get("run_id") or run_id),
        config_digest=config_digest,
    )
    return csv_row


def upsert_csv_rows(output_dir: Path, rows: dict[str, dict[str, Any]]) -> None:
    path = csv_path(output_dir)
    ordered_rows = [rows[instance_id] for instance_id in sorted(rows)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(ordered_rows)


def run_prepare_stage(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    run_id: str,
    dataset_revision: str,
) -> None:
    prepare_smoke(
        metadata_path=metadata_path,
        instance_id=instance_id,
        output_dir=output_dir,
        run_id=run_id,
        dataset_revision=dataset_revision,
    )


def run_preflight_stage(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    run_config: SWEBenchRunConfig,
    build_missing_images: bool,
) -> None:
    payload = run_swebench_preflight(
        metadata_path=metadata_path,
        instance_id=instance_id,
        output_dir=output_dir,
        build_missing_images=build_missing_images,
        config=run_config,
    )
    if payload["status"] != "ok":
        msg = f"preflight failed for {instance_id}: {payload['status']}"
        raise RuntimeError(msg)


def run_nominal_stage(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    run_config: SWEBenchRunConfig,
    run_id: str,
) -> None:
    patch_path = output_dir / instance_id / "patch" / "prod_only.patch"
    run_nominal_grading(
        metadata_path=metadata_path,
        instance_id=instance_id,
        patch_path=patch_path,
        output_dir=output_dir,
        run_id=f"nominal_{instance_id}",
        config=run_config,
    )


def run_perturbation_stage(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    perturbation_id: str,
    run_config: SWEBenchRunConfig,
    scheduled: tuple[str, ...],
) -> None:
    if perturbation_id not in scheduled:
        write_missing_perturbation_artifacts(
            output_dir=output_dir,
            instance_id=instance_id,
            perturbation_id=perturbation_id,
            message="not scheduled for this instance",
        )
        return
    if perturbation_id == PI_VERIF_V1_ID:
        patch_path = output_dir / instance_id / "patch" / "prod_only.patch"
        run_pi_verif_grading(
            metadata_path=metadata_path,
            instance_id=instance_id,
            patch_path=patch_path,
            output_dir=output_dir,
            run_id=f"pi_verif_{instance_id}",
            config=run_config,
        )
        return
    if perturbation_id == PI_ENV_V1_ID:
        patch_path = output_dir / instance_id / "patch" / "prod_only.patch"
        run_pi_env_grading(
            metadata_path=metadata_path,
            instance_id=instance_id,
            patch_path=patch_path,
            output_dir=output_dir,
            run_id=f"pi_env_{instance_id}",
            config=run_config,
        )
        return
    write_missing_perturbation_artifacts(
        output_dir=output_dir,
        instance_id=instance_id,
        perturbation_id=perturbation_id,
        message=f"{perturbation_id} harness executor is not implemented",
    )


def run_instance_pipeline(task: InstanceTask) -> InstancePipelineResult:
    """Execute the Phase A pipeline for one instance (process-pool worker)."""
    metadata_path = Path(task.metadata_path)
    output_dir = Path(task.output_dir)
    run_config = run_config_from_payload(task.run_config_payload)
    job_updates: list[dict[str, Any]] = []

    def mark(
        stage: str,
        *,
        status: JobStatus,
        error: str | None = None,
        attempts: int = 1,
    ) -> None:
        jid = (
            perturbation_job_id(task.instance_id, stage)
            if stage in PI_IDS
            else job_id(task.instance_id, stage)
        )
        job_updates.append(
            {
                "job_id": jid,
                "status": status.value,
                "attempts": attempts,
                "error": error,
            }
        )

    def skip_if_done(stage: str) -> bool:
        jid = (
            perturbation_job_id(task.instance_id, stage)
            if stage in PI_IDS
            else job_id(task.instance_id, stage)
        )
        prior = task.job_states.get(jid, JobStatus.PENDING.value)
        if task.resume and prior == JobStatus.COMPLETED.value:
            if artifact_stage_complete(output_dir, task.instance_id, stage):
                mark(stage, status=JobStatus.SKIPPED)
                return True
        if task.resume and prior == JobStatus.FAILED.value and not task.retry_failed:
            mark(stage, status=JobStatus.SKIPPED, error="prior failure retained")
            return True
        return False

    try:
        record = load_verified_instance(metadata_path, task.instance_id)
        scheduled = supported_perturbations(
            task.instance_id,
            record.fail_to_pass,
        )

        if not skip_if_done("prepare"):
            try:
                run_prepare_stage(
                    metadata_path=metadata_path,
                    instance_id=task.instance_id,
                    output_dir=output_dir,
                    run_id=task.run_id,
                    dataset_revision=task.dataset_revision,
                )
                mark("prepare", status=JobStatus.COMPLETED)
            except Exception as exc:
                mark("prepare", status=JobStatus.FAILED, error=str(exc))
                return InstancePipelineResult(
                    task.instance_id, tuple(job_updates), None, str(exc)
                )

        if not skip_if_done("preflight"):
            try:
                run_preflight_stage(
                    metadata_path=metadata_path,
                    instance_id=task.instance_id,
                    output_dir=output_dir,
                    run_config=run_config,
                    build_missing_images=task.build_missing_images,
                )
                mark("preflight", status=JobStatus.COMPLETED)
            except Exception as exc:
                mark("preflight", status=JobStatus.FAILED, error=str(exc))
                return InstancePipelineResult(
                    task.instance_id, tuple(job_updates), None, str(exc)
                )

        if not skip_if_done("nominal"):
            try:
                run_nominal_stage(
                    metadata_path=metadata_path,
                    instance_id=task.instance_id,
                    output_dir=output_dir,
                    run_config=run_config,
                    run_id=task.run_id,
                )
                mark("nominal", status=JobStatus.COMPLETED)
            except Exception as exc:
                mark("nominal", status=JobStatus.FAILED, error=str(exc))
                return InstancePipelineResult(
                    task.instance_id, tuple(job_updates), None, str(exc)
                )

        pi_targets = [pid for pid in PI_IDS if not skip_if_done(pid)]
        if pi_targets:
            workers = max(1, min(task.parallel_perturbations, len(pi_targets)))
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        run_perturbation_stage,
                        metadata_path=metadata_path,
                        instance_id=task.instance_id,
                        output_dir=output_dir,
                        perturbation_id=pid,
                        run_config=run_config,
                        scheduled=scheduled,
                    ): pid
                    for pid in pi_targets
                }
                for future in as_completed(futures):
                    pid = futures[future]
                    try:
                        future.result()
                        mark(pid, status=JobStatus.COMPLETED)
                    except Exception as exc:
                        mark(pid, status=JobStatus.FAILED, error=str(exc))

        if skip_if_done("aggregate"):
            csv_row = aggregate_instance(
                metadata_path=metadata_path,
                output_dir=output_dir,
                instance_id=task.instance_id,
                scheduled_perturbations=scheduled,
                run_id=task.run_id,
            )
        else:
            try:
                csv_row = aggregate_instance(
                    metadata_path=metadata_path,
                    output_dir=output_dir,
                    instance_id=task.instance_id,
                    scheduled_perturbations=scheduled,
                    run_id=task.run_id,
                )
                mark("aggregate", status=JobStatus.COMPLETED)
            except Exception as exc:
                mark("aggregate", status=JobStatus.FAILED, error=str(exc))
                return InstancePipelineResult(
                    task.instance_id,
                    tuple(job_updates),
                    None,
                    str(exc),
                )

        return InstancePipelineResult(task.instance_id, tuple(job_updates), csv_row)
    except Exception as exc:
        return InstancePipelineResult(
            task.instance_id,
            tuple(job_updates),
            None,
            str(exc),
        )


def resolve_instance_ids(
    metadata_path: Path,
    instances: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if instances:
        return tuple(sorted(instances))
    suffix = metadata_path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            ids = [str(row["instance_id"]) for row in payload if "instance_id" in row]
            return tuple(sorted(ids))
    msg = "--instances is required for parquet metadata sources"
    raise ValueError(msg)


def merge_job_updates(state: SchedulerState, updates: Iterable[dict[str, Any]]) -> None:
    for update in updates:
        jid = str(update["job_id"])
        record = state.jobs.get(jid)
        if record is None:
            continue
        record.status = JobStatus(str(update["status"]))
        record.attempts = int(update.get("attempts", record.attempts + 1))
        record.error = update.get("error")


class _InterruptController:
    def __init__(self) -> None:
        self.requested = False
        self._previous: signal.Handlers | None = None

    def install(self) -> None:
        self._previous = signal.getsignal(signal.SIGINT)

        def _handler(signum: int, frame: object | None) -> None:
            del signum, frame
            self.requested = True
            log_event("interrupt_requested")

        signal.signal(signal.SIGINT, _handler)

    def restore(self) -> None:
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)


def run_phase_a_scheduler(config: PhaseASchedulerConfig) -> dict[str, Any]:
    """Run the Phase A batch scheduler and return a summary payload."""
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    instance_ids = resolve_instance_ids(config.metadata_path, config.instance_ids)
    existing = load_scheduler_state(output_dir) if config.resume else None
    if existing and existing.run_id == config.run_id:
        state = existing
        for instance_id in instance_ids:
            if instance_id not in state.instance_ids:
                state.instance_ids.append(instance_id)
    else:
        state = SchedulerState(run_id=config.run_id, instance_ids=list(instance_ids))

    for instance_id in instance_ids:
        graph = build_job_graph([instance_id])
        for jid, record in graph.items():
            state.jobs.setdefault(jid, record)

    save_scheduler_state(output_dir, state)
    interrupt = _InterruptController()
    interrupt.install()

    csv_rows: dict[str, dict[str, Any]] = {}
    if csv_path(output_dir).is_file() and config.resume:
        with csv_path(output_dir).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if row.get("instance_id"):
                    csv_rows[str(row["instance_id"])] = row

    completed_instances = 0
    failed_instances = 0

    try:
        workers = max(1, min(config.workers, len(instance_ids)))
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures: dict[Future[InstancePipelineResult], str] = {}
            for instance_id in instance_ids:
                if interrupt.requested:
                    break
                record = load_verified_instance(config.metadata_path, instance_id)
                scheduled = supported_perturbations(
                    instance_id,
                    record.fail_to_pass,
                )
                task = InstanceTask(
                    instance_id=instance_id,
                    metadata_path=str(config.metadata_path),
                    output_dir=str(output_dir),
                    run_id=config.run_id,
                    dataset_revision=config.dataset_revision,
                    build_missing_images=config.build_missing_images,
                    parallel_perturbations=config.parallel_perturbations,
                    resume=config.resume,
                    retry_failed=config.retry_failed,
                    scheduled_perturbations=scheduled,
                    run_config_payload=run_config_to_payload(config.run_config),
                    job_states={
                        jid: job.status.value for jid, job in state.jobs.items()
                    },
                )
                log_event(
                    "instance_submitted",
                    instance_id=instance_id,
                    scheduled_perturbations=list(scheduled),
                )
                futures[pool.submit(run_instance_pipeline, task)] = instance_id

            for future in as_completed(futures):
                if interrupt.requested:
                    future.cancel()
                    continue
                instance_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    failed_instances += 1
                    log_event(
                        "instance_failed",
                        instance_id=instance_id,
                        error=str(exc),
                    )
                    continue

                merge_job_updates(state, result.jobs)
                save_scheduler_state(output_dir, state)

                if result.error:
                    failed_instances += 1
                    log_event(
                        "instance_failed",
                        instance_id=instance_id,
                        error=result.error,
                    )
                else:
                    completed_instances += 1
                    if result.csv_row is not None:
                        csv_rows[instance_id] = result.csv_row
                        upsert_csv_rows(output_dir, csv_rows)
                    log_event(
                        "instance_completed",
                        instance_id=instance_id,
                        jobs=len(result.jobs),
                    )

                if interrupt.requested:
                    break
    finally:
        state.interrupted = interrupt.requested
        save_scheduler_state(output_dir, state)
        interrupt.restore()

    summary = {
        "run_id": config.run_id,
        "instance_count": len(instance_ids),
        "completed_instances": completed_instances,
        "failed_instances": failed_instances,
        "interrupted": state.interrupted,
        "state_path": str(state_path(output_dir)),
        "csv_path": str(csv_path(output_dir)),
    }
    log_event("scheduler_finished", **summary)
    return summary


__all__ = [
    "CSV_COLUMNS",
    "CSV_FILENAME",
    "PhaseASchedulerConfig",
    "SchedulerState",
    "STATE_FILENAME",
    "aggregate_instance",
    "build_csv_row",
    "build_job_graph",
    "configure_structured_logging",
    "load_scheduler_state",
    "log_event",
    "run_phase_a_scheduler",
    "save_scheduler_state",
    "state_path",
]
