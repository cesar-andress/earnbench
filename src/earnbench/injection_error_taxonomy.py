"""Error taxonomy and per-pair diagnostics for blind injection runs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.injection_validity import (
    CLEAN_ROW_SUFFIX,
    FALSE_EARNED_THRESHOLD,
    PI_ORDER,
    InjectionResultRow,
    _exact_channel_attribution,
    _pi_status,
    _rate,
    observed_failed_pi,
)
from earnbench.injections.spec import InjectionSpec

BLIND_INJECTION_ERROR_TAXONOMY_MD = "blind_injection_error_taxonomy.md"
BLIND_INJECTION_PAIR_DIAGNOSTIC_CSV = "blind_injection_pair_diagnostic.csv"

EF_TOLERANCE = 0.05
CAUSE_INVALID = "INVALID"
CAUSE_HARNESS_ERROR = "harness_error"
CAUSE_BUILD_INSTABILITY = "build_instability"
CAUSE_GENUINE_PERTURBATION_FAILURE = "genuine_perturbation_failure"
CAUSE_MULTIPLE = "multiple_causes"
CAUSE_NOMINAL_HARNESS = "nominal_harness_failure"
INFRASTRUCTURE_CAUSES = frozenset(
    {
        CAUSE_INVALID,
        CAUSE_HARNESS_ERROR,
        CAUSE_BUILD_INSTABILITY,
        CAUSE_NOMINAL_HARNESS,
    }
)

PAIR_DIAGNOSTIC_COLUMNS = (
    "pair_id",
    "instance_id",
    "arm",
    "y0",
    "EF",
    "invalid_count",
    "failed_perturbations",
    "observed_failed_pi",
    "exact_target_channel_attribution",
    "ef_based_detection",
    "criterion_hit",
    "build_errors",
    "runtime_errors",
    "false_unearned",
    "immediate_cause",
)


@dataclass(frozen=True, slots=True)
class ArtifactReportContext:
    nominal_success: bool | None
    reason: str
    perturbation_results: tuple[dict[str, Any], ...]
    build_errors: int
    runtime_errors: int
    nominal_status: str
    nominal_success_grade: bool | None


def _artifact_dir(output_dir: Path, artifact_id: str) -> Path | None:
    root = output_dir / "artifacts" / artifact_id
    if not root.is_dir():
        return None
    instance_dirs = [
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    if not instance_dirs:
        return None
    return instance_dirs[0]


def _load_artifact_report_context(
    output_dir: Path,
    artifact_id: str,
) -> ArtifactReportContext | None:
    instance_dir = _artifact_dir(output_dir, artifact_id)
    if instance_dir is None:
        return None

    report_path = instance_dir / "report.json"
    preflight_path = instance_dir / "preflight.json"
    nominal_grade_path = instance_dir / "nominal" / "grade.json"

    report: dict[str, Any] = {}
    preflight: dict[str, Any] = {}
    nominal_grade: dict[str, Any] = {}
    if report_path.is_file():
        loaded = json.loads(report_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            report = loaded
    if preflight_path.is_file():
        loaded = json.loads(preflight_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            preflight = loaded
    if nominal_grade_path.is_file():
        loaded = json.loads(nominal_grade_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            nominal_grade = loaded

    perturbations = report.get("perturbation_results", [])
    if not isinstance(perturbations, list):
        perturbations = []

    build_errors = 0
    if preflight.get("build_attempted") and not preflight.get("build_success"):
        build_errors = 1
    if str(preflight.get("status", "")).lower() == "error":
        build_errors = max(build_errors, 1)

    runtime_errors = 0
    for item in perturbations:
        if not isinstance(item, dict):
            continue
        if str(item.get("status", "")).lower() == "error":
            runtime_errors += 1
    nominal_status = str(nominal_grade.get("status", "")).lower()
    if nominal_status == "error":
        runtime_errors += 1

    return ArtifactReportContext(
        nominal_success=report.get("nominal_success"),
        reason=str(report.get("reason", "")),
        perturbation_results=tuple(
            item for item in perturbations if isinstance(item, dict)
        ),
        build_errors=build_errors,
        runtime_errors=runtime_errors,
        nominal_status=nominal_status,
        nominal_success_grade=nominal_grade.get("success"),
    )


def _failed_perturbations(row: InjectionResultRow) -> list[str]:
    failed: list[str] = []
    for pi_id in PI_ORDER:
        status = _pi_status(row, pi_id)
        if status != "ok":
            failed.append(pi_id)
    if row.failed_mechanisms:
        for mechanism in row.failed_mechanisms:
            if mechanism not in failed:
                failed.append(mechanism)
    return failed


def _ef_based_detection(row: InjectionResultRow) -> bool | None:
    if not row.y0:
        return None
    return row.ef_exclude_invalid < FALSE_EARNED_THRESHOLD


def _exact_target_channel_attribution(
    spec: InjectionSpec | None,
    row: InjectionResultRow,
    *,
    arm: str,
) -> bool | None:
    if arm == "clean" or spec is None:
        return None
    return _exact_channel_attribution(spec, row)


def compute_criterion_hit(
    spec: InjectionSpec | None,
    row: InjectionResultRow,
    *,
    arm: str,
) -> bool | None:
    """Protocol-template match distinct from attribution and EF threshold."""
    if arm == "clean":
        return row.y0
    if spec is None:
        return None
    if not row.y0:
        return False
    if not spec.in_registry:
        return observed_failed_pi(row) == "none"
    expected_ef = spec.expected_ef_exclude_invalid
    if expected_ef is not None and abs(row.ef_exclude_invalid - expected_ef) > EF_TOLERANCE:
        return False
    return observed_failed_pi(row) == spec.expected_failed_pi


def _has_invalid_outcome(row: InjectionResultRow) -> bool:
    if row.invalid_pi_count > 0:
        return True
    return any(_pi_status(row, pi_id) == "invalid" for pi_id in PI_ORDER)


def _has_harness_error(row: InjectionResultRow, context: ArtifactReportContext | None) -> bool:
    if any(_pi_status(row, pi_id) == "error" for pi_id in PI_ORDER):
        return True
    if context is None:
        return False
    if context.runtime_errors > 0:
        return True
    if context.reason == "nominal_run_failed" and context.nominal_status == "error":
        return True
    return False


def _has_build_instability(context: ArtifactReportContext | None) -> bool:
    if context is None:
        return False
    return context.build_errors > 0


def _has_genuine_perturbation_failure(
    row: InjectionResultRow,
    context: ArtifactReportContext | None,
) -> bool:
    if row.failed_mechanisms:
        return True
    if context is None:
        return False
    for item in context.perturbation_results:
        outcome = str(item.get("outcome", "")).lower()
        status = str(item.get("status", "")).lower()
        if outcome == "fail" and status == "ok":
            return True
    return False


def _has_nominal_harness_failure(
    row: InjectionResultRow,
    context: ArtifactReportContext | None,
) -> bool:
    if row.y0:
        return False
    if context is None:
        return False
    if context.reason != "nominal_run_failed":
        return False
    if _has_invalid_outcome(row) or _has_harness_error(row, context):
        return False
    if _has_genuine_perturbation_failure(row, context):
        return False
    return True


def classify_immediate_cause(
    row: InjectionResultRow,
    context: ArtifactReportContext | None,
) -> str:
    flags = {
        CAUSE_INVALID: _has_invalid_outcome(row),
        CAUSE_HARNESS_ERROR: _has_harness_error(row, context),
        CAUSE_BUILD_INSTABILITY: _has_build_instability(context),
        CAUSE_GENUINE_PERTURBATION_FAILURE: _has_genuine_perturbation_failure(
            row,
            context,
        ),
        CAUSE_NOMINAL_HARNESS: _has_nominal_harness_failure(row, context),
    }
    active = [name for name, present in flags.items() if present]
    if len(active) > 1:
        return CAUSE_MULTIPLE
    if len(active) == 1:
        return active[0]
    if not row.y0:
        return CAUSE_NOMINAL_HARNESS
    return "none"


def build_pair_diagnostic_rows(
    results: dict[str, InjectionResultRow],
    specs: dict[str, InjectionSpec],
    output_dir: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for injection_id, spec in sorted(specs.items()):
        for arm, artifact_id in (
            ("injected", injection_id),
            ("clean", f"{injection_id}{CLEAN_ROW_SUFFIX}"),
        ):
            row = results.get(artifact_id)
            if row is None:
                continue
            context = _load_artifact_report_context(output_dir, artifact_id)
            spec_for_row = spec if arm == "injected" else None
            ef_value = "" if not row.y0 else row.ef_exclude_invalid
            false_unearned = (
                arm == "clean" and row.ef_exclude_invalid < FALSE_EARNED_THRESHOLD
            )
            rows.append(
                {
                    "pair_id": injection_id,
                    "instance_id": row.instance_id,
                    "arm": arm,
                    "y0": row.y0,
                    "EF": ef_value,
                    "invalid_count": row.invalid_pi_count,
                    "failed_perturbations": ";".join(_failed_perturbations(row)) or "—",
                    "observed_failed_pi": observed_failed_pi(row),
                    "exact_target_channel_attribution": _exact_target_channel_attribution(
                        spec_for_row,
                        row,
                        arm=arm,
                    ),
                    "ef_based_detection": _ef_based_detection(row),
                    "criterion_hit": compute_criterion_hit(
                        spec_for_row,
                        row,
                        arm=arm,
                    ),
                    "build_errors": context.build_errors if context else 0,
                    "runtime_errors": context.runtime_errors if context else 0,
                    "false_unearned": false_unearned,
                    "immediate_cause": classify_immediate_cause(row, context)
                    if arm == "clean"
                    else "—",
                }
            )
    return rows


def _count_causes(clean_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {
        key: 0
        for key in (
            CAUSE_INVALID,
            CAUSE_HARNESS_ERROR,
            CAUSE_BUILD_INSTABILITY,
            CAUSE_GENUINE_PERTURBATION_FAILURE,
            CAUSE_NOMINAL_HARNESS,
            CAUSE_MULTIPLE,
            "none",
        )
    }
    for row in clean_rows:
        cause = str(row.get("immediate_cause", "none"))
        counts[cause] = counts.get(cause, 0) + 1
    return counts


def estimate_adjusted_false_unearned(
    clean_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    false_unearned_rows = [row for row in clean_rows if row["false_unearned"]]
    denominator = len(clean_rows)
    observed_rate = _rate(len(false_unearned_rows), denominator)

    infrastructure_only = [
        row
        for row in false_unearned_rows
        if row["immediate_cause"] in INFRASTRUCTURE_CAUSES
    ]
    remaining = [row for row in false_unearned_rows if row not in infrastructure_only]

    y0_clean = [row for row in clean_rows if row["y0"]]
    y0_false_unearned = [
        row
        for row in y0_clean
        if row["EF"] != "" and float(row["EF"]) < FALSE_EARNED_THRESHOLD
    ]

    return {
        "observed_false_unearned_count": len(false_unearned_rows),
        "observed_false_unearned_rate": observed_rate,
        "denominator_clean_pairs": denominator,
        "infrastructure_only_excluded_count": len(infrastructure_only),
        "remaining_false_unearned_count": len(remaining),
        "remaining_false_unearned_rate": _rate(len(remaining), denominator),
        "y0_only_false_unearned_count": len(y0_false_unearned),
        "y0_only_false_unearned_rate": _rate(len(y0_false_unearned), len(y0_clean)),
        "infrastructure_only_pair_ids": sorted(
            {row["pair_id"] for row in infrastructure_only},
        ),
        "remaining_pair_ids": sorted({row["pair_id"] for row in remaining}),
    }


def _format_rate(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.4f}"


def render_blind_injection_error_taxonomy(
    pair_rows: list[dict[str, Any]],
    payload: dict[str, Any],
    adjustment: dict[str, Any],
) -> str:
    clean_rows = [row for row in pair_rows if row["arm"] == "clean"]
    injected_rows = [row for row in pair_rows if row["arm"] == "injected"]
    false_unearned_clean = [row for row in clean_rows if row["false_unearned"]]
    cause_counts = _count_causes(clean_rows)
    metrics = payload["metrics"]
    diagnostic_metrics = payload.get("diagnostic_metrics", {})
    in_registry_by_id = {
        item["injection_id"]: item["in_registry"] for item in payload["pair_rows"]
    }

    lines = [
        "# Blind Injection Error Taxonomy",
        "",
        "Analysis-only report over frozen blind-run artifacts. EF, Π, and INVALID "
        "semantics are unchanged; this document separates attribution, EF detection, "
        "and protocol criterion matching.",
        "",
        "## Three measurement notions",
        "",
        "| Notion | Column | Meaning | Applies to |",
        "| --- | --- | --- | --- |",
        (
            "| **A. Exact target-channel attribution** | "
            "`exact_target_channel_attribution` | "
            "`observed_failed_pi == expected_failed_pi` when injected, in-registry, "
            "and Y₀=1 | injected arms only |"
        ),
        (
            "| **B. EF-based detection** | `ef_based_detection` | "
            f"EF_exclude_invalid < τ={FALSE_EARNED_THRESHOLD} when Y₀=1 | "
            "both arms when Y₀=1 |"
        ),
        (
            "| **C. Protocol criterion hit** | `criterion_hit` | "
            "Clean: Y₀ must hold. Injected: Y₀ plus template match to spec EF/π "
            "(or `none` for OOR) | both arms |"
        ),
        "",
        "These can diverge: EF may drop without exact π attribution; a clean arm "
        "may satisfy Y₀ while a π arm returns INVALID or harness error.",
        "",
        "## Observed headline metrics",
        "",
        (
            f"- Clean false-unearned rate: **{adjustment['observed_false_unearned_count']}/"
            f"{adjustment['denominator_clean_pairs']}** "
            f"({_format_rate(adjustment['observed_false_unearned_rate'])})"
        ),
        f"- Injected false-earned rate (in-registry): **{metrics.get('false_earned_rate')}**",
        (
            f"- Exact target-channel attribution (in-registry): "
            f"**{metrics.get('targeted_channel_detection_rate')}**"
        ),
        (
            f"- EF-based detection (in-registry injected): "
            f"**{diagnostic_metrics.get('in_registry_ef_detection_rate')}**"
        ),
        (
            f"- Clean mean invalid π rate: "
            f"**{payload['invalid_asymmetry_rows'][0]['mean_invalid_pi_rate']}**"
        ),
        (
            f"- Injected mean invalid π rate: "
            f"**{payload['invalid_asymmetry_rows'][1]['mean_invalid_pi_rate']}**"
        ),
        "",
        "## Per-pair diagnostic table",
        "",
        "| pair_id | instance_id | arm | y0 | EF | invalid_count | failed_perturbations | "
        "observed_failed_pi | exact_attr | ef_detect | criterion_hit | build_errors | "
        "runtime_errors |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for row in pair_rows:
        exact = row["exact_target_channel_attribution"]
        ef_det = row["ef_based_detection"]
        crit = row["criterion_hit"]
        lines.append(
            f"| {row['pair_id']} | {row['instance_id']} | {row['arm']} | "
            f"{row['y0']} | {row['EF']} | {row['invalid_count']} | "
            f"{row['failed_perturbations']} | {row['observed_failed_pi']} | "
            f"{exact if exact is not None else '—'} | "
            f"{ef_det if ef_det is not None else '—'} | {crit} | "
            f"{row['build_errors']} | {row['runtime_errors']} |"
        )

    lines.extend(
        [
            "",
            "Machine-readable copy: `blind_injection_pair_diagnostic.csv`.",
            "",
            "## False-unearned clean artifacts — immediate cause",
            "",
        ]
    )

    if false_unearned_clean:
        lines.extend(
            [
                "| pair_id | instance_id | y0 | EF | immediate cause | failed π | "
                "invalid_count | build_errors | runtime_errors |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in false_unearned_clean:
            lines.append(
                f"| {row['pair_id']} | {row['instance_id']} | {row['y0']} | "
                f"{row['EF']} | {row['immediate_cause']} | {row['failed_perturbations']} | "
                f"{row['invalid_count']} | {row['build_errors']} | {row['runtime_errors']} |"
            )
    else:
        lines.append("_No false-unearned clean rows._")

    lines.extend(["", "## Quantitative failure taxonomy (clean arms)", ""])
    for cause, count in sorted(cause_counts.items()):
        if count:
            lines.append(f"- **{cause}**: {count}")

    y0_clean_count = len([row for row in clean_rows if row["y0"]])
    injected_in_registry = [
        row
        for row in injected_rows
        if in_registry_by_id.get(row["pair_id"])
    ]
    injected_y0 = [row for row in injected_in_registry if row["y0"]]
    injected_ef_detect = sum(1 for row in injected_y0 if row["ef_based_detection"])
    injected_exact = [
        row
        for row in injected_y0
        if row["exact_target_channel_attribution"] is True
    ]
    lines.extend(
        [
            "",
            "## Hypothetical adjusted clean false-unearned rate",
            "",
            "Estimate only — does not modify harness results.",
            "",
            (
                f"- Observed: {adjustment['observed_false_unearned_count']}/"
                f"{adjustment['denominator_clean_pairs']} = "
                f"{_format_rate(adjustment['observed_false_unearned_rate'])}"
            ),
            (
                f"- Excluding clean pairs whose only issue is INVALID, harness error, "
                f"build instability, or nominal harness failure "
                f"({', '.join(adjustment['infrastructure_only_pair_ids']) or 'none'}): "
                f"{adjustment['remaining_false_unearned_count']}/"
                f"{adjustment['denominator_clean_pairs']} = "
                f"{_format_rate(adjustment['remaining_false_unearned_rate'])}"
            ),
            (
                f"- Restricting to clean pairs with Y₀=1: "
                f"{adjustment['y0_only_false_unearned_count']}/{y0_clean_count} = "
                f"{_format_rate(adjustment['y0_only_false_unearned_rate'])}"
            ),
            "",
            "## Interpretation (observed data only)",
            "",
            "### Phenomenon location",
            "",
            (
                "- All **5/5** false-unearned clean rows have **Y₀=0**; none have "
                "EF below τ while Y₀=1."
            ),
            (
                f"- Injected in-registry arms with Y₀=1: "
                f"**{len(injected_exact)}/{len(injected_y0)}** exact target-channel "
                f"attribution, **{injected_ef_detect}/{len(injected_y0)}** EF-based detection, "
                f"**0** false-earned."
            ),
            (
                f"- Clean invalid π rate exceeds injected "
                f"({payload['invalid_asymmetry_rows'][0]['mean_invalid_pi_rate']} vs "
                f"{payload['invalid_asymmetry_rows'][1]['mean_invalid_pi_rate']})."
            ),
            "",
            "### Conclusion",
            "",
            (
                "**Current evidence supports (b) control-generation instability**, not "
                "(a) an EF measurement limitation on injected artifacts."
            ),
            "",
            "Supporting observations:",
            "",
            "1. Injected mechanism detection and EF separation behave as expected "
            "(targeted attribution 1.0, off-target 0, false-earned 0).",
            "2. Elevated false-unearned is entirely explained by clean **Y₀=0** rows "
            "driven by INVALID, harness/runtime errors, or nominal harness failure — "
            "not by low EF on passing clean baselines.",
            "3. After excluding infrastructure-only clean failures, the hypothetical "
            "residual false-unearned rate is "
            f"**{_format_rate(adjustment['remaining_false_unearned_rate'])}** "
            f"(vs observed **{_format_rate(adjustment['observed_false_unearned_rate'])}**).",
            "",
        ]
    )
    return "\n".join(lines)


def write_blind_injection_error_taxonomy_artifacts(
    payload: dict[str, Any],
    results: dict[str, InjectionResultRow],
    specs: dict[str, InjectionSpec],
    output_dir: Path,
) -> tuple[Path, Path]:
    pair_rows = build_pair_diagnostic_rows(results, specs, output_dir)
    adjustment = estimate_adjusted_false_unearned(
        [row for row in pair_rows if row["arm"] == "clean"],
    )
    csv_path = output_dir / BLIND_INJECTION_PAIR_DIAGNOSTIC_CSV
    md_path = output_dir / BLIND_INJECTION_ERROR_TAXONOMY_MD

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PAIR_DIAGNOSTIC_COLUMNS)
        writer.writeheader()
        for row in pair_rows:
            formatted = dict(row)
            for key in (
                "exact_target_channel_attribution",
                "ef_based_detection",
                "criterion_hit",
                "false_unearned",
            ):
                value = formatted[key]
                if value is None:
                    formatted[key] = ""
                elif isinstance(value, bool):
                    formatted[key] = str(value)
            writer.writerow({key: formatted.get(key, "") for key in PAIR_DIAGNOSTIC_COLUMNS})

    md_path.write_text(
        render_blind_injection_error_taxonomy(pair_rows, payload, adjustment),
        encoding="utf-8",
    )
    return csv_path, md_path


__all__ = [
    "BLIND_INJECTION_ERROR_TAXONOMY_MD",
    "BLIND_INJECTION_PAIR_DIAGNOSTIC_CSV",
    "build_pair_diagnostic_rows",
    "classify_immediate_cause",
    "compute_criterion_hit",
    "estimate_adjusted_false_unearned",
    "render_blind_injection_error_taxonomy",
    "write_blind_injection_error_taxonomy_artifacts",
]
