"""Aggregate Phase D run statistics into ``statistics.json``."""

from __future__ import annotations

import csv
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from earnbench.phase_a_batch import _as_bool
from earnbench.phase_b_batch import BATCH_PI_ORDER
from earnbench.phase_d_diagnostics import (
    FAILURE_EMPTY_PATCH,
    FAILURE_MALFORMED_PATCH,
    FAILURE_NOMINAL_FAILED,
    FAILURE_PATCH_APPLY_FAILED,
    FAILURE_REASONS,
    summarize_failure_reasons,
)
CELLS_DIR = "cells"
FAILURES_CSV = "failures.csv"
FAILURE_COLUMNS = (
    "agent",
    "instance_id",
    "replicate",
    "stage",
    "failure_reason",
    "error",
    "failure_detail",
    "timestamp_utc",
)
from earnbench.provenance import utc_timestamp
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1_ID

STATISTICS_JSON = "statistics.json"
PHASE_D_STATISTICS_SCHEMA_VERSION = "earnbench_phase_d_statistics.v1"

PI_RESULT_COLUMNS = (
    (PI_VTEST_V1_ID, "y_vtest", "pi_vtest_status"),
    (PI_VERIF_V1_ID, "y_verif", "pi_verif_status"),
    (PI_ENV_V1_ID, "y_env", "pi_env_status"),
)

PATCH_NOT_APPLIED_REASONS = frozenset(
    {
        FAILURE_EMPTY_PATCH,
        FAILURE_MALFORMED_PATCH,
        FAILURE_PATCH_APPLY_FAILED,
    }
)


def repo_from_instance_id(instance_id: str) -> str:
    """Infer ``owner/repo`` from a SWE-bench Verified ``instance_id``."""
    if "__" not in instance_id:
        return "unknown"
    owner, rest = instance_id.split("__", 1)
    repo_part = rest.rsplit("-", 1)[0]
    if not owner or not repo_part:
        return "unknown"
    return f"{owner}/{repo_part}"


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _rate_payload(*, numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": _rate(numerator, denominator),
    }


def patch_applied(row: dict[str, Any]) -> bool:
    """Return whether the agent patch reached harness grading."""
    reason = str(row.get("failure_reason", "") or "").strip()
    if reason in PATCH_NOT_APPLIED_REASONS:
        return False

    stage = str(row.get("failure_stage", "") or "").strip()
    if stage in {"validate", "prepare"} and reason:
        return False

    if row.get("y0") not in ("", None):
        return True
    if reason == FAILURE_NOMINAL_FAILED:
        return True
    if stage == "nominal":
        return True
    return any(str(row.get(col, "") or "").strip() for col in ("y_vtest", "y_verif", "y_env"))


def nominal_success(row: dict[str, Any]) -> bool:
    """Return whether nominal grading succeeded (``y0=1``)."""
    return patch_applied(row) and _as_bool(row.get("y0"))


def _pi_attempted(row: dict[str, Any], *, y_col: str, status_col: str) -> bool:
    status = str(row.get(status_col, "") or "").strip().lower()
    if status in {"ok", "invalid"}:
        return True
    y_value = row.get(y_col)
    return y_value not in ("", None)


def _pi_success(row: dict[str, Any], *, y_col: str, status_col: str) -> bool:
    if not _pi_attempted(row, y_col=y_col, status_col=status_col):
        return False
    status = str(row.get(status_col, "") or "").strip().lower()
    if status not in {"ok", "invalid"}:
        return False
    return _as_bool(row.get(y_col))


def count_patch_application(rows: list[dict[str, Any]]) -> tuple[int, int]:
    denominator = len(rows)
    numerator = sum(1 for row in rows if patch_applied(row))
    return numerator, denominator


def count_nominal_success(rows: list[dict[str, Any]]) -> tuple[int, int]:
    applied = [row for row in rows if patch_applied(row)]
    numerator = sum(1 for row in applied if _as_bool(row.get("y0")))
    return numerator, len(applied)


def count_perturbation_success(rows: list[dict[str, Any]]) -> tuple[int, int]:
    numerator = 0
    denominator = 0
    for row in rows:
        if not patch_applied(row):
            continue
        for _pi_id, y_col, status_col in PI_RESULT_COLUMNS:
            if not _pi_attempted(row, y_col=y_col, status_col=status_col):
                continue
            denominator += 1
            if _pi_success(row, y_col=y_col, status_col=status_col):
                numerator += 1
    return numerator, denominator


def _parse_utc_timestamp(value: object) -> datetime | None:
    if value in ("", None):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _grade_duration_seconds(grade_path: Path) -> float | None:
    if not grade_path.is_file():
        return None
    try:
        payload = json.loads(grade_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    started = _parse_utc_timestamp(payload.get("started_at_utc"))
    completed = _parse_utc_timestamp(payload.get("completed_at_utc"))
    if started is None or completed is None:
        return None
    return max(0.0, (completed - started).total_seconds())


def _cell_instance_dir(output_dir: Path, row: dict[str, Any]) -> Path | None:
    agent = str(row.get("agent", "") or "").strip()
    instance_id = str(row.get("instance_id", "") or "").strip()
    if not agent or not instance_id:
        return None
    replicate = int(row.get("replicate") or 0)
    work_root = output_dir / CELLS_DIR / agent
    if replicate > 0:
        work_root = work_root / f"r{replicate}"
    instance_dir = work_root / instance_id
    return instance_dir if instance_dir.is_dir() else None


def cell_grading_time_seconds(output_dir: Path, row: dict[str, Any]) -> float | None:
    """Sum grading durations across nominal and π artifacts for one cell."""
    instance_dir = _cell_instance_dir(output_dir, row)
    if instance_dir is None:
        return None

    stage_dirs = ["nominal", *BATCH_PI_ORDER]
    durations: list[float] = []
    for stage in stage_dirs:
        duration = _grade_duration_seconds(instance_dir / stage / "grade.json")
        if duration is not None:
            durations.append(duration)
    if not durations:
        return None
    return sum(durations)


def median_grading_time_seconds(
    output_dir: Path,
    rows: list[dict[str, Any]],
) -> tuple[float | None, int]:
    samples = [
        value
        for row in rows
        if (value := cell_grading_time_seconds(output_dir, row)) is not None
    ]
    if not samples:
        return None, 0
    return statistics.median(samples), len(samples)


def build_failure_taxonomy(
    rows: list[dict[str, Any]],
    failure_rows: list[dict[str, str]],
) -> dict[str, Any]:
    by_failure_reason = summarize_failure_reasons({str(index): row for index, row in enumerate(rows)})
    by_failure_stage: dict[str, int] = {}
    for row in rows:
        stage = str(row.get("failure_stage", "") or "").strip() or "none"
        by_failure_stage[stage] = by_failure_stage.get(stage, 0) + 1

    by_grade_status: dict[str, int] = {}
    for row in rows:
        status = str(row.get("grade_status", "") or "").strip() or "unknown"
        by_grade_status[status] = by_grade_status.get(status, 0) + 1

    by_event_stage: dict[str, int] = {}
    for row in failure_rows:
        stage = str(row.get("stage", "") or "").strip() or "unknown"
        by_event_stage[stage] = by_event_stage.get(stage, 0) + 1

    by_event_reason: dict[str, int] = {}
    for row in failure_rows:
        reason = str(row.get("failure_reason", "") or "").strip() or "unknown"
        by_event_reason[reason] = by_event_reason.get(reason, 0) + 1

    return {
        "failure_reasons": list(FAILURE_REASONS),
        "by_failure_reason": by_failure_reason,
        "by_primary_failure_stage": by_failure_stage,
        "by_grade_status": by_grade_status,
        "failure_events_by_stage": by_event_stage,
        "failure_events_by_reason": by_event_reason,
    }


def _ef_defined_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("ef_status", "") or "").strip().lower() == "defined"
        and row.get("ef_pi") not in ("", None)
    ]


def _repository_bucket(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        repo = repo_from_instance_id(str(row.get("instance_id", "") or ""))
        buckets.setdefault(repo, []).append(row)
    return buckets


def build_repository_statistics(
    *,
    output_dir: Path,
    rows: list[dict[str, Any]],
    failure_rows: list[dict[str, str]],
) -> dict[str, Any]:
    repo_stats: dict[str, Any] = {}
    for repo, repo_rows in sorted(_repository_bucket(rows).items()):
        patch_num, patch_den = count_patch_application(repo_rows)
        nominal_num, nominal_den = count_nominal_success(repo_rows)
        pi_num, pi_den = count_perturbation_success(repo_rows)
        median_time, time_samples = median_grading_time_seconds(output_dir, repo_rows)
        ef_rows = _ef_defined_rows(repo_rows)
        ef_values = [float(row["ef_pi"]) for row in ef_rows]
        repo_instance_ids = {
            str(row.get("instance_id", "") or "")
            for row in repo_rows
            if row.get("instance_id")
        }
        repo_failures = [
            row
            for row in failure_rows
            if repo_from_instance_id(str(row.get("instance_id", "") or "")) == repo
        ]
        repo_stats[repo] = {
            "instance_count": len(repo_instance_ids),
            "graded_count": len(repo_rows),
            "patch_application_rate": _rate_payload(
                numerator=patch_num,
                denominator=patch_den,
            ),
            "nominal_success_rate": _rate_payload(
                numerator=nominal_num,
                denominator=nominal_den,
            ),
            "perturbation_success_rate": _rate_payload(
                numerator=pi_num,
                denominator=pi_den,
            ),
            "failure_taxonomy": build_failure_taxonomy(repo_rows, repo_failures),
            "median_grading_time_seconds": median_time,
            "grading_time_sample_count": time_samples,
            "ef_defined_count": len(ef_rows),
            "ef_mean": (
                sum(ef_values) / len(ef_values) if ef_values else None
            ),
        }
    return repo_stats


def build_phase_d_statistics(
    *,
    output_dir: Path,
    rows: dict[str, dict[str, Any]],
    failure_rows: list[dict[str, str]] | None = None,
    run_id: str = "",
    skipped_ineligible_count: int = 0,
) -> dict[str, Any]:
    """Build the Phase D ``statistics.json`` payload."""
    ordered_rows = list(rows.values())
    failures = failure_rows if failure_rows is not None else load_failures_csv(
        output_dir / FAILURES_CSV,
    )

    patch_num, patch_den = count_patch_application(ordered_rows)
    nominal_num, nominal_den = count_nominal_success(ordered_rows)
    pi_num, pi_den = count_perturbation_success(ordered_rows)
    median_time, time_samples = median_grading_time_seconds(output_dir, ordered_rows)

    by_agent: dict[str, dict[str, int]] = {}
    for row in ordered_rows:
        agent = str(row.get("agent", "") or "")
        bucket = by_agent.setdefault(
            agent,
            {"graded": 0, "y0_pass": 0, "ef_defined": 0},
        )
        bucket["graded"] += 1
        if _as_bool(row.get("y0")):
            bucket["y0_pass"] += 1
        if str(row.get("ef_status", "") or "").strip().lower() == "defined":
            bucket["ef_defined"] += 1

    return {
        "schema_version": PHASE_D_STATISTICS_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at_utc": utc_timestamp(),
        "graded_count": len(ordered_rows),
        "skipped_ineligible_count": skipped_ineligible_count,
        "patch_application_rate": _rate_payload(
            numerator=patch_num,
            denominator=patch_den,
        ),
        "nominal_success_rate": _rate_payload(
            numerator=nominal_num,
            denominator=nominal_den,
        ),
        "perturbation_success_rate": _rate_payload(
            numerator=pi_num,
            denominator=pi_den,
        ),
        "failure_taxonomy": build_failure_taxonomy(ordered_rows, failures),
        "median_grading_time_seconds": median_time,
        "grading_time_sample_count": time_samples,
        "by_agent": by_agent,
        "by_repository": build_repository_statistics(
            output_dir=output_dir,
            rows=ordered_rows,
            failure_rows=failures,
        ),
    }


def load_failures_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        for raw in reader:
            rows.append({col: str(raw.get(col, "") or "") for col in FAILURE_COLUMNS})
    return rows


def write_phase_d_statistics(output_dir: Path, payload: dict[str, Any]) -> Path:
    path = output_dir / STATISTICS_JSON
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


__all__ = [
    "PHASE_D_STATISTICS_SCHEMA_VERSION",
    "STATISTICS_JSON",
    "build_failure_taxonomy",
    "build_phase_d_statistics",
    "build_repository_statistics",
    "cell_grading_time_seconds",
    "count_nominal_success",
    "count_patch_application",
    "count_perturbation_success",
    "load_failures_csv",
    "median_grading_time_seconds",
    "patch_applied",
    "repo_from_instance_id",
    "write_phase_d_statistics",
]
