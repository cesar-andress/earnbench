"""Phase A experiment batch runner (sequential π pipeline per instance)."""

from __future__ import annotations

import csv
import json
import logging
import shutil
import signal
import sys
import time
from collections.abc import Iterable
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench import prepare_smoke, supported_perturbations
from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.provenance import build_provenance, resolve_git_commit, utc_timestamp
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1_ID
from earnbench.reports import EarnedFractionStatus
from earnbench.scheduler import (
    aggregate_instance,
    artifact_stage_complete,
    run_config_from_payload,
    run_config_to_payload,
    run_nominal_stage,
    run_perturbation_stage,
    run_preflight_stage,
)

SUMMARY_CSV = "summary.csv"
FAILURES_CSV = "failures.csv"
EF_DISTRIBUTION_CSV = "ef_distribution.csv"
RUN_MANIFEST_JSON = "run_manifest.json"
STATISTICS_JSON = "statistics.json"
BATCH_STATE_JSON = "batch_state.json"

SUMMARY_COLUMNS = (
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

FAILURE_COLUMNS = ("instance_id", "stage", "error", "timestamp_utc")

BATCH_PI_ORDER = (PI_VERIF_V1_ID, PI_VTEST_V1_ID, PI_ENV_V1_ID)
PREPARE_STAGES = ("prepare", "preflight", "nominal")

logger = logging.getLogger("earnbench.phase_a_batch")


@dataclass(frozen=True, slots=True)
class PhaseABatchConfig:
    """Resolved settings for a Phase A batch experiment run."""

    manifest_path: Path
    metadata_path: Path
    output_dir: Path
    workers: int
    resume: bool
    run_config: SWEBenchRunConfig
    run_id: str
    dataset_revision: str = "unpinned"
    build_missing_images: bool = False


@dataclass(frozen=True, slots=True)
class BatchInstanceTask:
    """Pickle-friendly payload for one instance batch worker."""

    instance_id: str
    repo: str
    metadata_path: str
    output_dir: str
    run_id: str
    dataset_revision: str
    build_missing_images: bool
    resume: bool
    scheduled_perturbations: tuple[str, ...]
    run_config_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BatchInstanceResult:
    instance_id: str
    csv_row: dict[str, Any] | None
    failure: dict[str, Any] | None = None
    log_path: str | None = None


@dataclass
class BatchProgress:
    total: int
    completed: int = 0
    failed: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)

    def render(self, *, instance_id: str, message: str) -> None:
        elapsed = max(time.monotonic() - self.started_monotonic, 0.001)
        done = self.completed + self.failed
        rate = done / elapsed
        remaining = int((self.total - done) / rate) if rate > 0 else 0
        line = (
            f"[{done}/{self.total}] {instance_id}: {message} "
            f"(ok={self.completed} fail={self.failed} ETA={remaining}s)"
        )
        print(line, file=sys.stderr, flush=True)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_pilot_manifest(path: Path) -> list[dict[str, Any]]:
    """Load and return pilot rows sorted deterministically by instance_id."""
    if not path.is_file():
        msg = f"manifest not found: {path}"
        raise FileNotFoundError(msg)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        msg = "manifest must be a JSON array of instance rows"
        raise ValueError(msg)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            msg = f"manifest[{index}] must be an object"
            raise ValueError(msg)
        if "instance_id" not in item:
            msg = f"manifest[{index}] missing instance_id"
            raise ValueError(msg)
        rows.append(item)
    return sorted(rows, key=lambda row: str(row["instance_id"]))


def instance_log_path(output_dir: Path, instance_id: str) -> Path:
    return output_dir / instance_id / "batch.log"


def instance_complete(output_dir: Path, instance_id: str) -> bool:
    return artifact_stage_complete(output_dir, instance_id, "aggregate")


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = utc_timestamp()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def _copy_reports_and_audits(output_dir: Path, instance_id: str) -> None:
    instance_dir = output_dir / instance_id
    reports_dir = output_dir / "reports"
    audits_root = output_dir / "audits" / instance_id
    reports_dir.mkdir(parents=True, exist_ok=True)
    audits_root.mkdir(parents=True, exist_ok=True)

    report_src = instance_dir / "report.json"
    if report_src.is_file():
        shutil.copy2(report_src, reports_dir / f"{instance_id}.json")

    nominal_audit = instance_dir / "nominal" / "audit.json"
    if nominal_audit.is_file():
        shutil.copy2(nominal_audit, audits_root / "nominal.json")

    for perturbation_id in BATCH_PI_ORDER:
        audit_src = instance_dir / perturbation_id / "audit.json"
        if audit_src.is_file():
            safe_name = perturbation_id.replace(".", "_")
            shutil.copy2(audit_src, audits_root / f"{safe_name}.json")


def run_instance_batch_pipeline(task: BatchInstanceTask) -> BatchInstanceResult:
    """Run prepare → nominal → π (sequential) → EF for one instance."""
    metadata_path = Path(task.metadata_path)
    output_dir = Path(task.output_dir)
    run_config = run_config_from_payload(task.run_config_payload)
    log_path = instance_log_path(output_dir, task.instance_id)

    if task.resume and instance_complete(output_dir, task.instance_id):
        _append_log(log_path, "skip instance (already complete)")
        try:
            csv_row = aggregate_instance(
                metadata_path=metadata_path,
                output_dir=output_dir,
                instance_id=task.instance_id,
                scheduled_perturbations=task.scheduled_perturbations,
                run_id=task.run_id,
            )
            _copy_reports_and_audits(output_dir, task.instance_id)
            return BatchInstanceResult(
                instance_id=task.instance_id,
                csv_row=csv_row,
                log_path=str(log_path),
            )
        except Exception as exc:
            return BatchInstanceResult(
                instance_id=task.instance_id,
                csv_row=None,
                failure=_failure_row(task.instance_id, "aggregate", str(exc)),
                log_path=str(log_path),
            )

    def skip_stage(stage: str) -> bool:
        return task.resume and artifact_stage_complete(
            output_dir,
            task.instance_id,
            stage,
        )

    try:
        if not skip_stage("prepare"):
            _append_log(log_path, "stage prepare start")
            prepare_smoke(
                metadata_path=metadata_path,
                instance_id=task.instance_id,
                output_dir=output_dir,
                run_id=task.run_id,
                dataset_revision=task.dataset_revision,
            )
            _append_log(log_path, "stage prepare done")
        else:
            _append_log(log_path, "stage prepare skipped")

        if not skip_stage("preflight"):
            _append_log(log_path, "stage preflight start")
            run_preflight_stage(
                metadata_path=metadata_path,
                instance_id=task.instance_id,
                output_dir=output_dir,
                run_config=run_config,
                build_missing_images=task.build_missing_images,
            )
            _append_log(log_path, "stage preflight done")
        else:
            _append_log(log_path, "stage preflight skipped")

        if not skip_stage("nominal"):
            _append_log(log_path, "stage nominal start")
            run_nominal_stage(
                metadata_path=metadata_path,
                instance_id=task.instance_id,
                output_dir=output_dir,
                run_config=run_config,
                run_id=task.run_id,
            )
            _append_log(log_path, "stage nominal done")
        else:
            _append_log(log_path, "stage nominal skipped")

        for perturbation_id in BATCH_PI_ORDER:
            if not skip_stage(perturbation_id):
                _append_log(log_path, f"stage {perturbation_id} start")
                run_perturbation_stage(
                    metadata_path=metadata_path,
                    instance_id=task.instance_id,
                    output_dir=output_dir,
                    perturbation_id=perturbation_id,
                    run_config=run_config,
                    scheduled=task.scheduled_perturbations,
                )
                _append_log(log_path, f"stage {perturbation_id} done")
            else:
                _append_log(log_path, f"stage {perturbation_id} skipped")

        _append_log(log_path, "stage aggregate start")
        csv_row = aggregate_instance(
            metadata_path=metadata_path,
            output_dir=output_dir,
            instance_id=task.instance_id,
            scheduled_perturbations=task.scheduled_perturbations,
            run_id=task.run_id,
        )
        _copy_reports_and_audits(output_dir, task.instance_id)
        _append_log(log_path, "stage aggregate done")
        return BatchInstanceResult(
            instance_id=task.instance_id,
            csv_row=csv_row,
            log_path=str(log_path),
        )
    except Exception as exc:
        stage = _infer_failed_stage(log_path)
        _append_log(log_path, f"failed at {stage}: {exc}")
        return BatchInstanceResult(
            instance_id=task.instance_id,
            csv_row=None,
            failure=_failure_row(task.instance_id, stage, str(exc)),
            log_path=str(log_path),
        )


def _infer_failed_stage(log_path: Path) -> str:
    if not log_path.is_file():
        return "unknown"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        if " stage " in line and line.endswith(" start"):
            return line.split(" stage ", 1)[1].rsplit(" ", 1)[0]
    return "unknown"


def _failure_row(instance_id: str, stage: str, error: str) -> dict[str, Any]:
    return {
        "instance_id": instance_id,
        "stage": stage,
        "error": error,
        "timestamp_utc": utc_timestamp(),
    }


def _load_csv_rows(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return {
            str(row["instance_id"]): dict(row)
            for row in reader
            if row.get("instance_id")
        }


def _load_failure_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_summary_csv(output_dir: Path, rows: dict[str, dict[str, Any]]) -> Path:
    path = output_dir / SUMMARY_CSV
    ordered = [rows[instance_id] for instance_id in sorted(rows)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(ordered)
    return path


def write_failures_csv(output_dir: Path, rows: Iterable[dict[str, Any]]) -> Path:
    path = output_dir / FAILURES_CSV
    ordered = sorted(rows, key=lambda row: (row["instance_id"], row["timestamp_utc"]))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FAILURE_COLUMNS)
        writer.writeheader()
        writer.writerows(ordered)
    return path


def write_ef_distribution_csv(
    output_dir: Path,
    summary_rows: dict[str, dict[str, Any]],
) -> Path:
    buckets: dict[str, int] = {}
    undefined = 0
    for row in summary_rows.values():
        status = str(row.get("ef_status", ""))
        if status != EarnedFractionStatus.DEFINED.value:
            undefined += 1
            continue
        ef_raw = row.get("ef_pi")
        if ef_raw in ("", None):
            undefined += 1
            continue
        key = f"{float(ef_raw):.4f}"
        buckets[key] = buckets.get(key, 0) + 1

    path = output_dir / EF_DISTRIBUTION_CSV
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("earned_fraction", "count"))
        writer.writeheader()
        for ef_value in sorted(buckets, key=float):
            writer.writerow({"earned_fraction": ef_value, "count": buckets[ef_value]})
        if undefined:
            writer.writerow({"earned_fraction": "undefined", "count": undefined})
    return path


def build_statistics(summary_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total = len(summary_rows)
    retained = sum(1 for row in summary_rows.values() if _as_bool(row.get("retained")))
    false_unearned = sum(
        1 for row in summary_rows.values() if _as_bool(row.get("false_unearned"))
    )
    ef_defined_rows = [
        row
        for row in summary_rows.values()
        if str(row.get("ef_status")) == EarnedFractionStatus.DEFINED.value
        and row.get("ef_pi") not in ("", None)
    ]
    ef_values = [float(row["ef_pi"]) for row in ef_defined_rows]
    ef_mean = sum(ef_values) / len(ef_values) if ef_values else None
    return {
        "instance_count": total,
        "retained_count": retained,
        "false_unearned_count": false_unearned,
        "ef_defined_count": len(ef_defined_rows),
        "ef_undefined_count": total - len(ef_defined_rows),
        "ef_mean": ef_mean,
        "ef_min": min(ef_values) if ef_values else None,
        "ef_max": max(ef_values) if ef_values else None,
        "generated_at_utc": utc_timestamp(),
    }


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def write_run_manifest(
    output_dir: Path,
    *,
    config: PhaseABatchConfig,
    instance_ids: list[str],
    started_at_utc: str,
    completed_at_utc: str | None,
    summary: dict[str, Any],
) -> Path:
    path = output_dir / RUN_MANIFEST_JSON
    payload = {
        "run_id": config.run_id,
        "manifest_path": str(config.manifest_path),
        "metadata_path": str(config.metadata_path),
        "output_dir": str(config.output_dir),
        "instance_ids": instance_ids,
        "workers": config.workers,
        "resume": config.resume,
        "dataset_revision": config.dataset_revision,
        "build_missing_images": config.build_missing_images,
        "run_config": run_config_to_payload(config.run_config),
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "provenance": build_provenance(
            git_commit=resolve_git_commit(),
        ).to_dict(),
        "summary": summary,
    }
    _write_json(path, payload)
    return path


class _InterruptController:
    def __init__(self) -> None:
        self.requested = False
        self._previous: signal.Handlers | None = None

    def install(self) -> None:
        self._previous = signal.getsignal(signal.SIGINT)

        def _handler(signum: int, frame: object | None) -> None:
            del signum, frame
            self.requested = True

        signal.signal(signal.SIGINT, _handler)

    def restore(self) -> None:
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)


def run_phase_a_batch(config: PhaseABatchConfig) -> dict[str, Any]:
    """Execute the Phase A batch experiment and write batch artifacts."""
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reports").mkdir(parents=True, exist_ok=True)
    (output_dir / "audits").mkdir(parents=True, exist_ok=True)

    manifest_rows = load_pilot_manifest(config.manifest_path)
    instance_ids = [str(row["instance_id"]) for row in manifest_rows]
    repos = {str(row["instance_id"]): str(row.get("repo", "")) for row in manifest_rows}

    started_at = utc_timestamp()
    summary_rows = (
        _load_csv_rows(output_dir / SUMMARY_CSV) if config.resume else {}
    )
    failure_rows = (
        _load_failure_rows(output_dir / FAILURES_CSV) if config.resume else []
    )

    if config.resume:
        failure_rows = [
            row
            for row in failure_rows
            if row.get("instance_id") not in summary_rows
            or not instance_complete(output_dir, str(row["instance_id"]))
        ]

    progress = BatchProgress(total=len(instance_ids))
    interrupt = _InterruptController()
    interrupt.install()

    completed_instances = 0
    failed_instances = 0
    skipped_instances = 0

    try:
        pending_ids = [
            instance_id
            for instance_id in instance_ids
            if not (config.resume and instance_complete(output_dir, instance_id))
        ]
        skipped_instances = len(instance_ids) - len(pending_ids)
        progress.completed = skipped_instances

        workers = max(1, min(config.workers, len(pending_ids) or 1))
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures: dict[Future[BatchInstanceResult], str] = {}
            for instance_id in pending_ids:
                if interrupt.requested:
                    break
                record = load_verified_instance(config.metadata_path, instance_id)
                scheduled = supported_perturbations(
                    instance_id,
                    record.fail_to_pass,
                )
                task = BatchInstanceTask(
                    instance_id=instance_id,
                    repo=repos.get(instance_id, record.repo),
                    metadata_path=str(config.metadata_path),
                    output_dir=str(output_dir),
                    run_id=config.run_id,
                    dataset_revision=config.dataset_revision,
                    build_missing_images=config.build_missing_images,
                    resume=config.resume,
                    scheduled_perturbations=scheduled,
                    run_config_payload=run_config_to_payload(config.run_config),
                )
                futures[pool.submit(run_instance_batch_pipeline, task)] = instance_id

            for future in as_completed(futures):
                if interrupt.requested:
                    future.cancel()
                    continue
                instance_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    failed_instances += 1
                    progress.failed += 1
                    failure_rows.append(
                        _failure_row(instance_id, "worker", str(exc)),
                    )
                    progress.render(instance_id=instance_id, message="worker error")
                    write_failures_csv(output_dir, failure_rows)
                    continue

                if result.failure is not None:
                    failed_instances += 1
                    progress.failed += 1
                    failure_rows.append(result.failure)
                    progress.render(
                        instance_id=instance_id,
                        message=f"failed ({result.failure['stage']})",
                    )
                elif result.csv_row is not None:
                    completed_instances += 1
                    progress.completed += 1
                    summary_rows[instance_id] = result.csv_row
                    progress.render(instance_id=instance_id, message="complete")
                else:
                    failed_instances += 1
                    progress.failed += 1
                    progress.render(instance_id=instance_id, message="missing csv row")

                write_summary_csv(output_dir, summary_rows)
                write_failures_csv(output_dir, failure_rows)
                write_ef_distribution_csv(output_dir, summary_rows)
                _write_json(
                    output_dir / STATISTICS_JSON,
                    build_statistics(summary_rows),
                )
    finally:
        interrupt.restore()

    completed_at = utc_timestamp()
    summary = {
        "instance_count": len(instance_ids),
        "completed_instances": completed_instances,
        "failed_instances": failed_instances,
        "skipped_instances": skipped_instances,
        "interrupted": interrupt.requested,
    }
    write_run_manifest(
        output_dir,
        config=config,
        instance_ids=instance_ids,
        started_at_utc=started_at,
        completed_at_utc=completed_at,
        summary=summary,
    )
    _write_json(
        output_dir / BATCH_STATE_JSON,
        {
            "run_id": config.run_id,
            "updated_at_utc": completed_at,
            "summary": summary,
        },
    )
    return {
        **summary,
        "run_id": config.run_id,
        "summary_csv": str(output_dir / SUMMARY_CSV),
        "failures_csv": str(output_dir / FAILURES_CSV),
        "ef_distribution_csv": str(output_dir / EF_DISTRIBUTION_CSV),
        "run_manifest_json": str(output_dir / RUN_MANIFEST_JSON),
        "statistics_json": str(output_dir / STATISTICS_JSON),
    }


__all__ = [
    "BATCH_PI_ORDER",
    "PhaseABatchConfig",
    "build_statistics",
    "load_pilot_manifest",
    "run_instance_batch_pipeline",
    "run_phase_a_batch",
]
