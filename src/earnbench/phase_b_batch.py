"""Phase B exploit corpus batch runner (sequential π pipeline per exploit)."""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import signal
import sys
import time
from collections.abc import Iterable
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from math import isclose
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench import prepare_exploit, supported_perturbations
from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.adapters.swebench_nominal import run_nominal_grading
from earnbench.adapters.swebench_pi_env import run_pi_env_grading
from earnbench.adapters.swebench_pi_verif import run_pi_verif_grading
from earnbench.adapters.swebench_pi_vtest import run_pi_vtest_grading
from earnbench.exploits.catalog import ExploitCatalogError, list_exploits
from earnbench.exploits.spec import ExploitSpec
from earnbench.phase_a_batch import (
    METADATA_ENV_VAR,
    BatchProgress,
    _InterruptController,
    _as_bool,
    _write_json,
    build_statistics,
)
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
    run_preflight_stage,
    write_missing_perturbation_artifacts,
)

SUMMARY_CSV = "summary.csv"
FAILURES_CSV = "failures.csv"
CONFUSION_MATRIX_CSV = "confusion_matrix.csv"
REGISTRY_COVERAGE_CSV = "registry_coverage.csv"
RUN_MANIFEST_JSON = "run_manifest.json"
STATISTICS_JSON = "statistics.json"
BATCH_STATE_JSON = "batch_state.json"

DEFAULT_BATCH_OUTPUT_DIR = Path("phase_b")
DEFAULT_EXPLOIT_PATCHES_DIR = "patches"

BATCH_PI_ORDER = (PI_VERIF_V1_ID, PI_VTEST_V1_ID, PI_ENV_V1_ID)

PI_SUCCESS_KEYS = {
    PI_VTEST_V1_ID: "y_vtest",
    PI_VERIF_V1_ID: "y_verif",
    PI_ENV_V1_ID: "y_env",
}

PI_EXPECTED_KEYS = {
    PI_VTEST_V1_ID: "expected_pi_vtest",
    PI_VERIF_V1_ID: "expected_pi_verif",
    PI_ENV_V1_ID: "expected_pi_env",
}

SUMMARY_COLUMNS = (
    "exploit_id",
    "instance_id",
    "repo",
    "channel",
    "family",
    "template_id",
    "predicted_fail_pi",
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
    "expected_nominal",
    "expected_pi_vtest",
    "expected_pi_verif",
    "expected_pi_env",
    "expected_earned_fraction",
    "criterion_hit",
    "targeted_pi_failed",
    "run_id",
    "config_digest",
)

FAILURE_COLUMNS = ("exploit_id", "instance_id", "stage", "error", "timestamp_utc")

logger = logging.getLogger("earnbench.phase_b_batch")


@dataclass(frozen=True, slots=True)
class PhaseBBatchConfig:
    """Resolved settings for a Phase B exploit batch run."""

    exploit_dir: Path
    metadata_path: Path
    output_dir: Path
    workers: int
    resume: bool
    run_config: SWEBenchRunConfig
    run_id: str
    dataset_revision: str = "unpinned"
    build_missing_images: bool = False
    exploit_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class BatchExploitTask:
    """Pickle-friendly payload for one exploit batch worker."""

    exploit_id: str
    instance_id: str
    metadata_path: str
    exploit_dir: str
    output_dir: str
    run_id: str
    dataset_revision: str
    build_missing_images: bool
    resume: bool
    scheduled_perturbations: tuple[str, ...]
    run_config_payload: dict[str, Any]
    spec_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BatchExploitResult:
    exploit_id: str
    csv_row: dict[str, Any] | None
    failure: dict[str, Any] | None = None
    log_path: str | None = None


def resolve_metadata_path(
    metadata_parquet: Path | None,
    *,
    base_dir: Path | None = None,
) -> Path:
    """Resolve SWE-bench metadata path from CLI flag, env, or defaults."""
    base = base_dir or Path.cwd()
    if metadata_parquet is not None:
        resolved = (
            metadata_parquet.resolve()
            if metadata_parquet.is_absolute()
            else (base / metadata_parquet).resolve()
        )
        if not resolved.is_file():
            msg = f"--metadata-parquet file not found: {resolved}"
            raise FileNotFoundError(msg)
        return resolved

    env_metadata = os.environ.get(METADATA_ENV_VAR, "").strip()
    if env_metadata:
        resolved = (
            Path(env_metadata).resolve()
            if Path(env_metadata).is_absolute()
            else (base / env_metadata).resolve()
        )
        if resolved.is_file():
            return resolved

    for candidate in (
        "../paper/vendor/swe_verified_test.parquet",
        "../vendor/swe_verified_test.parquet",
        "vendor/swe_verified_test.parquet",
    ):
        resolved = (base / candidate).resolve()
        if resolved.is_file():
            return resolved

    msg = (
        "could not resolve SWE-bench metadata path; pass --metadata-parquet or set "
        f"{METADATA_ENV_VAR}"
    )
    raise FileNotFoundError(msg)


def resolve_selected_exploits(
    exploit_dir: Path,
    exploit_ids: tuple[str, ...] | None = None,
) -> list[ExploitSpec]:
    """Return exploit specs in catalog order, optionally filtered by id."""
    try:
        all_specs = list_exploits(exploit_dir)
    except ExploitCatalogError as exc:
        msg = str(exc)
        raise ValueError(msg) from exc

    if not exploit_ids:
        return all_specs

    catalog_ids = {spec.exploit_id for spec in all_specs}
    missing = sorted(set(exploit_ids) - catalog_ids)
    if missing:
        known = ", ".join(spec.exploit_id for spec in all_specs)
        msg = f"unknown exploit id(s): {', '.join(missing)} (known: {known})"
        raise ValueError(msg)

    selected = set(exploit_ids)
    return [spec for spec in all_specs if spec.exploit_id in selected]


def resolve_exploit_patch(exploit_dir: Path, exploit_id: str) -> Path:
    """Return the unified diff patch path for an exploit id."""
    patch_path = exploit_dir / DEFAULT_EXPLOIT_PATCHES_DIR / f"{exploit_id}.patch"
    if not patch_path.is_file():
        msg = f"exploit patch not found: {patch_path}"
        raise FileNotFoundError(msg)
    return patch_path


def exploit_work_dir(output_dir: Path, exploit_id: str) -> Path:
    return output_dir / exploit_id


def exploit_log_path(output_dir: Path, exploit_id: str) -> Path:
    return exploit_work_dir(output_dir, exploit_id) / "batch.log"


def exploit_complete(
    output_dir: Path,
    exploit_id: str,
    instance_id: str,
) -> bool:
    return artifact_stage_complete(
        exploit_work_dir(output_dir, exploit_id),
        instance_id,
        "aggregate",
    )


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = utc_timestamp()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def _copy_reports_and_audits(
    output_dir: Path,
    exploit_id: str,
    instance_id: str,
) -> None:
    work_root = exploit_work_dir(output_dir, exploit_id)
    instance_dir = work_root / instance_id
    reports_dir = output_dir / "reports"
    audits_root = output_dir / "audits" / exploit_id
    reports_dir.mkdir(parents=True, exist_ok=True)
    audits_root.mkdir(parents=True, exist_ok=True)

    report_src = instance_dir / "report.json"
    if report_src.is_file():
        shutil.copy2(report_src, reports_dir / f"{exploit_id}.json")

    nominal_audit = instance_dir / "nominal" / "audit.json"
    if nominal_audit.is_file():
        shutil.copy2(nominal_audit, audits_root / "nominal.json")

    for perturbation_id in BATCH_PI_ORDER:
        audit_src = instance_dir / perturbation_id / "audit.json"
        if audit_src.is_file():
            safe_name = perturbation_id.replace(".", "_")
            shutil.copy2(audit_src, audits_root / f"{safe_name}.json")


def _nominal_patch_path(instance_dir: Path, y0_policy: str) -> Path:
    if y0_policy == "raw_full":
        return instance_dir / "patch" / "raw.patch"
    return instance_dir / "patch" / "prod_only.patch"


def _pi_patch_path(
    instance_dir: Path,
    perturbation_id: str,
    *,
    y0_policy: str,
    family: str,
) -> Path:
    if perturbation_id == PI_VERIF_V1_ID and (
        y0_policy == "raw_full" or family == "verifier_tamper"
    ):
        return instance_dir / "patch" / "raw.patch"
    return instance_dir / "patch" / "prod_only.patch"


def run_exploit_nominal_stage(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    run_config: SWEBenchRunConfig,
    run_id: str,
    y0_policy: str,
) -> None:
    instance_dir = output_dir / instance_id
    patch_path = _nominal_patch_path(instance_dir, y0_policy)
    run_nominal_grading(
        metadata_path=metadata_path,
        instance_id=instance_id,
        patch_path=patch_path,
        output_dir=output_dir,
        run_id=f"nominal_{instance_id}",
        config=run_config,
    )


def run_exploit_perturbation_stage(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    perturbation_id: str,
    run_config: SWEBenchRunConfig,
    scheduled: tuple[str, ...],
    y0_policy: str,
    family: str,
) -> None:
    if perturbation_id not in scheduled:
        write_missing_perturbation_artifacts(
            output_dir=output_dir,
            instance_id=instance_id,
            perturbation_id=perturbation_id,
            message="not scheduled for this instance",
        )
        return

    instance_dir = output_dir / instance_id
    patch_path = _pi_patch_path(
        instance_dir,
        perturbation_id,
        y0_policy=y0_policy,
        family=family,
    )
    common = {
        "metadata_path": metadata_path,
        "instance_id": instance_id,
        "patch_path": patch_path,
        "output_dir": output_dir,
        "config": run_config,
    }
    if perturbation_id == PI_VTEST_V1_ID:
        run_pi_vtest_grading(
            **common,
            run_id=f"pi_vtest_{instance_id}",
        )
        return
    if perturbation_id == PI_VERIF_V1_ID:
        run_pi_verif_grading(
            **common,
            run_id=f"pi_verif_{instance_id}",
        )
        return
    if perturbation_id == PI_ENV_V1_ID:
        run_pi_env_grading(
            **common,
            run_id=f"pi_env_{instance_id}",
        )
        return
    write_missing_perturbation_artifacts(
        output_dir=output_dir,
        instance_id=instance_id,
        perturbation_id=perturbation_id,
        message=f"{perturbation_id} harness executor is not implemented",
    )


def _load_failure_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_exploit_summary_rows(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return {
            str(row["exploit_id"]): dict(row)
            for row in reader
            if row.get("exploit_id")
        }


def write_failures_csv(output_dir: Path, rows: Iterable[dict[str, Any]]) -> Path:
    path = output_dir / FAILURES_CSV
    ordered = sorted(
        rows,
        key=lambda row: (str(row.get("exploit_id", "")), str(row.get("timestamp_utc", ""))),
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FAILURE_COLUMNS)
        writer.writeheader()
        writer.writerows(ordered)
    return path


def _coerce_optional_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"true", "1"}:
        return True
    if lowered in {"false", "0"}:
        return False
    return None


def _targeted_pi_failed(row: dict[str, Any], predicted_fail_pi: str) -> bool | None:
    if not predicted_fail_pi:
        return None
    status_field = {
        PI_VTEST_V1_ID: "pi_vtest_status",
        PI_VERIF_V1_ID: "pi_verif_status",
        PI_ENV_V1_ID: "pi_env_status",
    }.get(predicted_fail_pi)
    success_key = PI_SUCCESS_KEYS.get(predicted_fail_pi)
    if status_field is None or success_key is None:
        return None
    status = str(row.get(status_field, ""))
    if status != "ok":
        return None
    success = _coerce_optional_bool(row.get(success_key))
    if success is None:
        return None
    return not success


def _ef_matches(expected: float | None, actual: object) -> bool:
    if expected is None:
        return True
    if actual in ("", None):
        return False
    return isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-6)


def compute_criterion_hit(row: dict[str, Any], spec: ExploitSpec) -> bool:
    y0 = _coerce_optional_bool(row.get("y0"))
    if y0 is None or y0 != spec.expected_nominal:
        return False

    for pi_id, expected_key in PI_EXPECTED_KEYS.items():
        expected = getattr(spec, expected_key)
        if expected is None:
            continue
        actual = _coerce_optional_bool(row.get(PI_SUCCESS_KEYS[pi_id]))
        if actual is None or actual != expected:
            return False

    if not _ef_matches(spec.expected_earned_fraction, row.get("ef_pi")):
        return False

    targeted = _targeted_pi_failed(row, spec.predicted_fail_pi)
    if targeted is not True:
        return False
    return True


def enrich_summary_row(row: dict[str, Any], spec: ExploitSpec) -> dict[str, Any]:
    enriched = dict(row)
    enriched["exploit_id"] = spec.exploit_id
    enriched["instance_id"] = spec.instance_id
    enriched["channel"] = spec.channel
    enriched["family"] = spec.family
    enriched["template_id"] = spec.template_id
    enriched["predicted_fail_pi"] = spec.predicted_fail_pi
    enriched["expected_nominal"] = spec.expected_nominal
    enriched["expected_pi_vtest"] = spec.expected_pi_vtest
    enriched["expected_pi_verif"] = spec.expected_pi_verif
    enriched["expected_pi_env"] = spec.expected_pi_env
    enriched["expected_earned_fraction"] = spec.expected_earned_fraction
    targeted = _targeted_pi_failed(enriched, spec.predicted_fail_pi)
    enriched["targeted_pi_failed"] = targeted
    enriched["criterion_hit"] = compute_criterion_hit(enriched, spec)
    return enriched


def run_exploit_batch_pipeline(task: BatchExploitTask) -> BatchExploitResult:
    """Run prepare → nominal → π (sequential) → EF for one exploit."""
    spec = ExploitSpec.from_dict(task.spec_payload)
    metadata_path = Path(task.metadata_path)
    batch_output = Path(task.output_dir)
    exploit_dir = Path(task.exploit_dir)
    work_root = exploit_work_dir(batch_output, task.exploit_id)
    run_config = run_config_from_payload(task.run_config_payload)
    log_path = exploit_log_path(batch_output, task.exploit_id)
    instance_id = task.instance_id

    if task.resume and exploit_complete(batch_output, task.exploit_id, instance_id):
        _append_log(log_path, "skip exploit (already complete)")
        try:
            csv_row = aggregate_instance(
                metadata_path=metadata_path,
                output_dir=work_root,
                instance_id=instance_id,
                scheduled_perturbations=task.scheduled_perturbations,
                run_id=task.run_id,
            )
            enriched = enrich_summary_row(csv_row, spec)
            _copy_reports_and_audits(batch_output, task.exploit_id, instance_id)
            return BatchExploitResult(
                exploit_id=task.exploit_id,
                csv_row=enriched,
                log_path=str(log_path),
            )
        except Exception as exc:
            return BatchExploitResult(
                exploit_id=task.exploit_id,
                csv_row=None,
                failure=_failure_row_exploit(task.exploit_id, instance_id, "aggregate", str(exc)),
                log_path=str(log_path),
            )

    def skip_stage(stage: str) -> bool:
        return task.resume and artifact_stage_complete(
            work_root,
            instance_id,
            stage,
        )

    y0_policy = spec.y0_policy or "prod_only"
    family = spec.family

    try:
        patch_path = resolve_exploit_patch(exploit_dir, task.exploit_id)
        patch_content = patch_path.read_text(encoding="utf-8")

        if not skip_stage("prepare"):
            _append_log(log_path, "stage prepare start")
            prepare_exploit(
                metadata_path=metadata_path,
                instance_id=instance_id,
                exploit_id=task.exploit_id,
                patch_content=patch_content,
                output_dir=work_root,
                run_id=task.run_id,
                dataset_revision=task.dataset_revision,
                patch_class="exploit_planted",
                y0_policy=y0_policy,
                channel=spec.channel,
                family=spec.family,
                template_id=spec.template_id,
                predicted_fail_pi=spec.predicted_fail_pi,
            )
            _append_log(log_path, "stage prepare done")
        else:
            _append_log(log_path, "stage prepare skipped")

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
        else:
            _append_log(log_path, "stage preflight skipped")

        if not skip_stage("nominal"):
            _append_log(log_path, "stage nominal start")
            run_exploit_nominal_stage(
                metadata_path=metadata_path,
                instance_id=instance_id,
                output_dir=work_root,
                run_config=run_config,
                run_id=task.run_id,
                y0_policy=y0_policy,
            )
            _append_log(log_path, "stage nominal done")
        else:
            _append_log(log_path, "stage nominal skipped")

        for perturbation_id in BATCH_PI_ORDER:
            if not skip_stage(perturbation_id):
                _append_log(log_path, f"stage {perturbation_id} start")
                run_exploit_perturbation_stage(
                    metadata_path=metadata_path,
                    instance_id=instance_id,
                    output_dir=work_root,
                    perturbation_id=perturbation_id,
                    run_config=run_config,
                    scheduled=task.scheduled_perturbations,
                    y0_policy=y0_policy,
                    family=family,
                )
                _append_log(log_path, f"stage {perturbation_id} done")
            else:
                _append_log(log_path, f"stage {perturbation_id} skipped")

        _append_log(log_path, "stage aggregate start")
        csv_row = aggregate_instance(
            metadata_path=metadata_path,
            output_dir=work_root,
            instance_id=instance_id,
            scheduled_perturbations=task.scheduled_perturbations,
            run_id=task.run_id,
        )
        enriched = enrich_summary_row(csv_row, spec)
        _copy_reports_and_audits(batch_output, task.exploit_id, instance_id)
        _append_log(log_path, "stage aggregate done")
        return BatchExploitResult(
            exploit_id=task.exploit_id,
            csv_row=enriched,
            log_path=str(log_path),
        )
    except Exception as exc:
        stage = _infer_failed_stage(log_path)
        _append_log(log_path, f"failed at {stage}: {exc}")
        return BatchExploitResult(
            exploit_id=task.exploit_id,
            csv_row=None,
            failure=_failure_row_exploit(
                task.exploit_id,
                instance_id,
                stage,
                str(exc),
            ),
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


def _failure_row_exploit(
    exploit_id: str,
    instance_id: str,
    stage: str,
    error: str,
) -> dict[str, Any]:
    return {
        "exploit_id": exploit_id,
        "instance_id": instance_id,
        "stage": stage,
        "error": error,
        "timestamp_utc": utc_timestamp(),
    }


def write_summary_csv(output_dir: Path, rows: dict[str, dict[str, Any]]) -> Path:
    path = output_dir / SUMMARY_CSV
    ordered = [rows[exploit_id] for exploit_id in sorted(rows)]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)
    return path


def build_phase_b_statistics(summary_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    base = build_statistics(
        {
            exploit_id: {
                **row,
                "instance_id": row.get("instance_id", exploit_id),
            }
            for exploit_id, row in summary_rows.items()
        }
    )
    criterion_hits = sum(
        1 for row in summary_rows.values() if _as_bool(row.get("criterion_hit"))
    )
    targeted_failures = [
        row
        for row in summary_rows.values()
        if _as_bool(row.get("targeted_pi_failed"))
    ]
    families: dict[str, list[dict[str, Any]]] = {}
    for row in summary_rows.values():
        family = str(row.get("family") or "unknown")
        families.setdefault(family, []).append(row)

    family_stats = {}
    for family, rows in sorted(families.items()):
        hits = sum(1 for row in rows if _as_bool(row.get("criterion_hit")))
        targeted = sum(1 for row in rows if _as_bool(row.get("targeted_pi_failed")))
        family_stats[family] = {
            "exploit_count": len(rows),
            "criterion_hit_count": hits,
            "criterion_hit_rate": hits / len(rows) if rows else None,
            "targeted_pi_fail_count": targeted,
            "targeted_pi_fail_rate": targeted / len(rows) if rows else None,
        }

    return {
        **base,
        "exploit_count": len(summary_rows),
        "criterion_hit_count": criterion_hits,
        "criterion_hit_rate": (
            criterion_hits / len(summary_rows) if summary_rows else None
        ),
        "targeted_pi_fail_count": len(targeted_failures),
        "targeted_pi_fail_rate": (
            len(targeted_failures) / len(summary_rows) if summary_rows else None
        ),
        "family_stats": family_stats,
        "generated_at_utc": utc_timestamp(),
    }


def write_confusion_matrix_csv(
    output_dir: Path,
    summary_rows: dict[str, dict[str, Any]],
) -> Path:
    counts = {"tp": 0, "fn": 0, "indeterminate": 0}
    detail_rows: list[dict[str, Any]] = []
    for exploit_id in sorted(summary_rows):
        row = summary_rows[exploit_id]
        targeted = _targeted_pi_failed(row, str(row.get("predicted_fail_pi", "")))
        if targeted is True:
            counts["tp"] += 1
            outcome = "tp"
        elif targeted is False:
            counts["fn"] += 1
            outcome = "fn"
        else:
            counts["indeterminate"] += 1
            outcome = "indeterminate"
        detail_rows.append(
            {
                "exploit_id": exploit_id,
                "ground_truth_target_should_fail": True,
                "predicted_target_failed": targeted,
                "outcome_class": outcome,
                "predicted_fail_pi": row.get("predicted_fail_pi"),
            }
        )

    path = output_dir / CONFUSION_MATRIX_CSV
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "exploit_id",
                "ground_truth_target_should_fail",
                "predicted_target_failed",
                "outcome_class",
                "predicted_fail_pi",
            ),
        )
        writer.writeheader()
        writer.writerows(detail_rows)
        writer.writerow(
            {
                "exploit_id": "__aggregate__",
                "ground_truth_target_should_fail": True,
                "predicted_target_failed": "",
                "outcome_class": f"tp={counts['tp']};fn={counts['fn']};indeterminate={counts['indeterminate']}",
                "predicted_fail_pi": "",
            }
        )
    return path


def write_registry_coverage_csv(
    output_dir: Path,
    summary_rows: dict[str, dict[str, Any]],
) -> Path:
    families: dict[str, list[dict[str, Any]]] = {}
    for row in summary_rows.values():
        key = str(row.get("family") or "unknown")
        families.setdefault(key, []).append(row)

    path = output_dir / REGISTRY_COVERAGE_CSV
    rows_out: list[dict[str, Any]] = []
    for family in sorted(families):
        group = families[family]
        channel = str(group[0].get("channel") or "")
        targeted = sum(1 for row in group if _as_bool(row.get("targeted_pi_failed")))
        hits = sum(1 for row in group if _as_bool(row.get("criterion_hit")))
        count = len(group)
        rows_out.append(
            {
                "family": family,
                "channel": channel,
                "registry_label": "IN-REGISTRY",
                "exploit_count": count,
                "targeted_pi_fail_count": targeted,
                "targeted_pi_fail_rate": targeted / count if count else None,
                "criterion_hit_count": hits,
                "criterion_hit_rate": hits / count if count else None,
            }
        )

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "family",
                "channel",
                "registry_label",
                "exploit_count",
                "targeted_pi_fail_count",
                "targeted_pi_fail_rate",
                "criterion_hit_count",
                "criterion_hit_rate",
            ),
        )
        writer.writeheader()
        writer.writerows(rows_out)
    return path


def write_run_manifest(
    output_dir: Path,
    *,
    config: PhaseBBatchConfig,
    exploit_ids: list[str],
    started_at_utc: str,
    completed_at_utc: str | None,
    summary: dict[str, Any],
) -> Path:
    path = output_dir / RUN_MANIFEST_JSON
    payload = {
        "run_id": config.run_id,
        "exploit_dir": str(config.exploit_dir),
        "metadata_path": str(config.metadata_path),
        "output_dir": str(config.output_dir),
        "exploit_ids": exploit_ids,
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


def run_phase_b_batch(config: PhaseBBatchConfig) -> dict[str, Any]:
    """Execute the Phase B exploit batch and write batch artifacts."""
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reports").mkdir(parents=True, exist_ok=True)
    (output_dir / "audits").mkdir(parents=True, exist_ok=True)

    specs = resolve_selected_exploits(config.exploit_dir, config.exploit_ids)
    exploit_ids = [spec.exploit_id for spec in specs]
    spec_by_id = {spec.exploit_id: spec for spec in specs}

    started_at = utc_timestamp()
    summary_rows = (
        _load_exploit_summary_rows(output_dir / SUMMARY_CSV) if config.resume else {}
    )
    if config.resume:
        summary_rows = {
            key: row for key, row in summary_rows.items() if key in spec_by_id
        }

    failure_rows: list[dict[str, Any]] = (
        list(_load_failure_rows(output_dir / FAILURES_CSV)) if config.resume else []
    )
    if config.resume:
        failure_rows = [
            row
            for row in failure_rows
            if row.get("exploit_id") not in summary_rows
            or not exploit_complete(
                output_dir,
                str(row.get("exploit_id")),
                str(row.get("instance_id", "")),
            )
        ]

    progress = BatchProgress(total=len(exploit_ids))
    interrupt = _InterruptController()
    interrupt.install()

    completed_exploits = 0
    failed_exploits = 0
    skipped_exploits = 0

    try:
        pending = [
            spec
            for spec in specs
            if not (
                config.resume
                and exploit_complete(output_dir, spec.exploit_id, spec.instance_id)
            )
        ]
        skipped_exploits = len(specs) - len(pending)
        progress.completed = skipped_exploits

        workers = max(1, min(config.workers, len(pending) or 1))
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures: dict[Future[BatchExploitResult], str] = {}
            for spec in pending:
                if interrupt.requested:
                    break
                record = load_verified_instance(config.metadata_path, spec.instance_id)
                scheduled = supported_perturbations(
                    spec.instance_id,
                    record.fail_to_pass,
                )
                task = BatchExploitTask(
                    exploit_id=spec.exploit_id,
                    instance_id=spec.instance_id,
                    metadata_path=str(config.metadata_path),
                    exploit_dir=str(config.exploit_dir),
                    output_dir=str(output_dir),
                    run_id=config.run_id,
                    dataset_revision=config.dataset_revision,
                    build_missing_images=config.build_missing_images,
                    resume=config.resume,
                    scheduled_perturbations=scheduled,
                    run_config_payload=run_config_to_payload(config.run_config),
                    spec_payload=spec.to_dict(),
                )
                futures[pool.submit(run_exploit_batch_pipeline, task)] = spec.exploit_id

            for future in as_completed(futures):
                if interrupt.requested:
                    future.cancel()
                    continue
                exploit_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    failed_exploits += 1
                    progress.failed += 1
                    spec = spec_by_id[exploit_id]
                    failure_rows.append(
                        _failure_row_exploit(
                            exploit_id,
                            spec.instance_id,
                            "worker",
                            str(exc),
                        )
                    )
                    progress.render(instance_id=exploit_id, message="worker error")
                    write_failures_csv(output_dir, failure_rows)
                    continue

                if result.failure is not None:
                    failed_exploits += 1
                    progress.failed += 1
                    failure_rows.append(result.failure)
                    progress.render(
                        instance_id=exploit_id,
                        message=f"failed ({result.failure['stage']})",
                    )
                elif result.csv_row is not None:
                    completed_exploits += 1
                    progress.completed += 1
                    summary_rows[exploit_id] = result.csv_row
                    progress.render(instance_id=exploit_id, message="complete")
                else:
                    failed_exploits += 1
                    progress.failed += 1
                    progress.render(instance_id=exploit_id, message="missing csv row")

                write_summary_csv(output_dir, summary_rows)
                write_failures_csv(output_dir, failure_rows)
                _write_json(
                    output_dir / STATISTICS_JSON,
                    build_phase_b_statistics(summary_rows),
                )
                write_confusion_matrix_csv(output_dir, summary_rows)
                write_registry_coverage_csv(output_dir, summary_rows)
    finally:
        interrupt.restore()

    completed_at = utc_timestamp()
    batch_summary = {
        "exploit_count": len(exploit_ids),
        "completed_exploits": completed_exploits,
        "failed_exploits": failed_exploits,
        "skipped_exploits": skipped_exploits,
        "interrupted": interrupt.requested,
    }
    write_run_manifest(
        output_dir,
        config=config,
        exploit_ids=exploit_ids,
        started_at_utc=started_at,
        completed_at_utc=completed_at,
        summary=batch_summary,
    )
    _write_json(
        output_dir / BATCH_STATE_JSON,
        {
            "run_id": config.run_id,
            "updated_at_utc": completed_at,
            "summary": batch_summary,
        },
    )
    return {
        **batch_summary,
        "run_id": config.run_id,
        "summary_csv": str(output_dir / SUMMARY_CSV),
        "failures_csv": str(output_dir / FAILURES_CSV),
        "confusion_matrix_csv": str(output_dir / CONFUSION_MATRIX_CSV),
        "registry_coverage_csv": str(output_dir / REGISTRY_COVERAGE_CSV),
        "run_manifest_json": str(output_dir / RUN_MANIFEST_JSON),
        "statistics_json": str(output_dir / STATISTICS_JSON),
    }


__all__ = [
    "BATCH_PI_ORDER",
    "DEFAULT_BATCH_OUTPUT_DIR",
    "PhaseBBatchConfig",
    "build_phase_b_statistics",
    "compute_criterion_hit",
    "enrich_summary_row",
    "resolve_exploit_patch",
    "resolve_metadata_path",
    "resolve_selected_exploits",
    "run_exploit_batch_pipeline",
    "run_phase_b_batch",
    "write_confusion_matrix_csv",
    "write_registry_coverage_csv",
]
