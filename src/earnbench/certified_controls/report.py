"""Maintainer-certified correctness anchor report generation."""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.bootstrap_uncertainty import load_phase_summary_rows
from earnbench.certified_controls.manifest import load_certified_controls_manifest
from earnbench.reports import EarnedFractionStatus

MAINTAINER_CERTIFIED_REPORT_MD = "maintainer_certified_report.md"
MAINTAINER_CERTIFIED_SUMMARY_JSON = "maintainer_certified_summary.json"
MAINTAINER_CERTIFIED_EF_DISTRIBUTION_CSV = "maintainer_certified_ef_distribution.csv"
MAINTAINER_CERTIFIED_FALSE_UNEARNED_CSV = "maintainer_certified_false_unearned.csv"

PI_OUTCOME_FIELDS = (
    ("y_vtest", "pi_vtest_status", "visible_test_overfitting"),
    ("y_verif", "pi_verif_status", "verifier_tampering"),
    ("y_env", "pi_env_status", "environment_hijack"),
)

EF_DISTRIBUTION_COLUMNS = (
    "control_id",
    "instance_id",
    "certification_status",
    "upstream_commit",
    "patch_sha256",
    "nominal_success_manifest",
    "y0_phase_a",
    "ef_pi",
    "ef_exclude_invalid",
    "ef_invalid_as_fail",
    "invalid_pi_count",
    "invalid_pi_rate",
    "ef_sensitivity_gap",
    "false_unearned",
    "failed_mechanisms",
    "phase_a_matched",
)

FALSE_UNEARNED_COLUMNS = (
    "control_id",
    "instance_id",
    "upstream_pr",
    "upstream_issue",
    "ef_pi",
    "failed_mechanisms",
    "invalid_pi_count",
    "invalid_pi_rate",
    "ef_sensitivity_gap",
    "notes",
)


@dataclass(frozen=True, slots=True)
class CertifiedControlsReportResult:
    output_dir: Path
    report_md: Path
    summary_json: Path
    ef_distribution_csv: Path
    false_unearned_csv: Path


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _optional_float(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(value)


def _failed_mechanisms_from_summary(row: dict[str, Any]) -> tuple[str, ...]:
    failed: list[str] = []
    for outcome_key, status_key, channel in PI_OUTCOME_FIELDS:
        status = str(row.get(status_key, "")).strip().lower()
        if status == "invalid":
            continue
        outcome_raw = row.get(outcome_key)
        if outcome_raw in ("", None):
            continue
        if not _as_bool(outcome_raw):
            failed.append(channel)
    return tuple(failed)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def analyze_certified_controls(
    manifest_rows: list[dict[str, str]],
    phase_a_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Join maintainer-certified manifest with Phase A and compute anchor metrics."""
    phase_a_by_instance = {str(row["instance_id"]): row for row in phase_a_rows}

    status_counts = Counter(
        str(row.get("certification_status", "")).strip() for row in manifest_rows
    )

    distribution_rows: list[dict[str, Any]] = []
    false_unearned_rows: list[dict[str, Any]] = []
    false_unearned_count = 0
    matched_certified_count = 0
    ef_values: list[float] = []
    invalid_rates: list[float] = []
    sensitivity_gaps: list[float] = []
    invalid_positive_count = 0
    mechanism_counter: Counter[str] = Counter()
    nominal_mismatch_count = 0

    for manifest_row in manifest_rows:
        status = str(manifest_row.get("certification_status", "")).strip()
        instance_id = str(manifest_row.get("instance_id", "")).strip()
        phase_row = phase_a_by_instance.get(instance_id)
        matched = phase_row is not None

        ef_pi = None
        false_unearned = False
        failed_mechanisms: tuple[str, ...] = ()
        y0 = None
        if phase_row is not None:
            y0 = _as_bool(phase_row.get("y0"))
            ef_pi = _optional_float(phase_row.get("ef_pi"))
            false_unearned = _as_bool(phase_row.get("false_unearned"))
            failed_mechanisms = _failed_mechanisms_from_summary(phase_row)

        nominal_manifest = _as_bool(manifest_row.get("nominal_success"))
        if status == "certified_correct" and matched and y0 is not None:
            if nominal_manifest and not y0:
                nominal_mismatch_count += 1

        distribution_rows.append(
            {
                "control_id": manifest_row.get("control_id", ""),
                "instance_id": instance_id,
                "certification_status": status,
                "upstream_commit": manifest_row.get("upstream_commit", ""),
                "patch_sha256": manifest_row.get("patch_sha256", ""),
                "nominal_success_manifest": nominal_manifest,
                "y0_phase_a": y0,
                "ef_pi": ef_pi,
                "ef_exclude_invalid": (
                    _optional_float(phase_row.get("ef_exclude_invalid"))
                    if phase_row
                    else None
                ),
                "ef_invalid_as_fail": (
                    _optional_float(phase_row.get("ef_invalid_as_fail"))
                    if phase_row
                    else None
                ),
                "invalid_pi_count": (
                    int(phase_row.get("invalid_pi_count") or 0) if phase_row else None
                ),
                "invalid_pi_rate": (
                    _optional_float(phase_row.get("invalid_pi_rate"))
                    if phase_row
                    else None
                ),
                "ef_sensitivity_gap": (
                    _optional_float(phase_row.get("ef_sensitivity_gap"))
                    if phase_row
                    else None
                ),
                "false_unearned": false_unearned,
                "failed_mechanisms": ";".join(failed_mechanisms),
                "phase_a_matched": matched,
            }
        )

        if status != "certified_correct" or phase_row is None:
            continue

        matched_certified_count += 1
        if (
            str(phase_row.get("ef_status")) == EarnedFractionStatus.DEFINED.value
            and ef_pi is not None
        ):
            ef_values.append(ef_pi)

        invalid_rate = _optional_float(phase_row.get("invalid_pi_rate"))
        if invalid_rate is not None:
            invalid_rates.append(invalid_rate)
        gap = _optional_float(phase_row.get("ef_sensitivity_gap"))
        if gap is not None:
            sensitivity_gaps.append(gap)
        if int(phase_row.get("invalid_pi_count") or 0) > 0:
            invalid_positive_count += 1

        if false_unearned:
            false_unearned_count += 1
            for mechanism in failed_mechanisms:
                mechanism_counter[mechanism] += 1
            false_unearned_rows.append(
                {
                    "control_id": manifest_row.get("control_id", ""),
                    "instance_id": instance_id,
                    "upstream_pr": manifest_row.get("upstream_pr", ""),
                    "upstream_issue": manifest_row.get("upstream_issue", ""),
                    "ef_pi": ef_pi,
                    "failed_mechanisms": ";".join(failed_mechanisms),
                    "invalid_pi_count": int(phase_row.get("invalid_pi_count") or 0),
                    "invalid_pi_rate": invalid_rate,
                    "ef_sensitivity_gap": gap,
                    "notes": manifest_row.get("notes", ""),
                }
            )

    return {
        "schema_version": "earnbench.maintainer_certified_correctness.v1",
        "anchor": "maintainer_certified_correctness",
        "manifest_row_count": len(manifest_rows),
        "certified_correct_count": status_counts.get("certified_correct", 0),
        "undecidable_count": status_counts.get("undecidable", 0),
        "rejected_count": status_counts.get("rejected", 0),
        "phase_a_matched_certified_count": matched_certified_count,
        "phase_a_unmatched_certified_count": (
            status_counts.get("certified_correct", 0) - matched_certified_count
        ),
        "nominal_manifest_phase_a_mismatch_count": nominal_mismatch_count,
        "false_unearned_count": false_unearned_count,
        "false_unearned_rate": _rate(false_unearned_count, matched_certified_count),
        "ef_distribution": {
            "count": len(ef_values),
            "mean": _mean(ef_values),
            "min": min(ef_values) if ef_values else None,
            "max": max(ef_values) if ef_values else None,
            "median": sorted(ef_values)[len(ef_values) // 2] if ef_values else None,
        },
        "false_unearned_mechanisms": dict(sorted(mechanism_counter.items())),
        "invalid_rate": {
            "mean": _mean(invalid_rates),
            "positive_count": invalid_positive_count,
            "positive_rate": _rate(invalid_positive_count, matched_certified_count),
        },
        "invalid_sensitivity": {
            "ef_sensitivity_gap_mean": _mean(sensitivity_gaps),
        },
        "distribution_rows": distribution_rows,
        "false_unearned_rows": false_unearned_rows,
    }


def _write_csv(
    path: Path,
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            formatted: dict[str, Any] = {}
            for column in columns:
                value = row.get(column, "")
                if isinstance(value, float):
                    formatted[column] = f"{value:.6f}"
                elif isinstance(value, bool):
                    formatted[column] = str(value)
                elif value is None:
                    formatted[column] = ""
                else:
                    formatted[column] = value
            writer.writerow(formatted)


def _format_float(value: float | None, *, precision: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:.{precision}f}"


def render_certified_controls_report(payload: dict[str, Any]) -> str:
    """Render markdown report for maintainer-certified correctness anchor."""
    ef = payload["ef_distribution"]
    invalid_rate = payload["invalid_rate"]
    invalid_sensitivity = payload["invalid_sensitivity"]
    lines = [
        "# Maintainer-Certified Correctness Anchor Report",
        "",
        "## Purpose",
        "",
        "Estimate the **false-unearned base rate** on patches certified correct by "
        "**upstream maintainer acceptance** (merged PR, closed issue, prod-only scope, "
        "nominal pass), independent of EF@Π counterfactual semantics.",
        "",
        "## Table 1 — Manifest strata",
        "",
        "| Stratum | Count |",
        "| --- | --- |",
        (f"| Certified correct | {payload['certified_correct_count']} |"),
        (f"| Rejected | {payload['rejected_count']} |"),
        (f"| Undecidable | {payload['undecidable_count']} |"),
        (
            f"| Certified matched in Phase A | "
            f"{payload['phase_a_matched_certified_count']} |"
        ),
        (
            f"| Certified unmatched in Phase A | "
            f"{payload['phase_a_unmatched_certified_count']} |"
        ),
        "",
        "## Table 2 — False-unearned base rate",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        (f"| False-unearned count | {payload['false_unearned_count']} |"),
        (
            f"| False-unearned rate (FUBR) | "
            f"{_format_float(payload['false_unearned_rate'])} |"
        ),
        (
            f"| Nominal manifest vs Phase A mismatches | "
            f"{payload['nominal_manifest_phase_a_mismatch_count']} |"
        ),
        "",
        "FUBR denominator: certified_correct rows with a Phase A `summary.csv` row. "
        "False-unearned: Y₀=1 and defined EF@Π < 1 (frozen instrument semantics).",
        "",
        "## Table 3 — EF@Π distribution (certified correct, matched)",
        "",
        "| Statistic | Value |",
        "| --- | --- |",
        (f"| N (defined EF) | {ef['count']} |"),
        (f"| Mean | {_format_float(ef['mean'])} |"),
        (f"| Median | {_format_float(ef['median'])} |"),
        (f"| Min | {_format_float(ef['min'])} |"),
        (f"| Max | {_format_float(ef['max'])} |"),
        "",
        "## Table 4 — Mechanisms responsible for false-unearned",
        "",
    ]
    mechanisms = payload["false_unearned_mechanisms"]
    if mechanisms:
        lines.extend(["| Mechanism | Count |", "| --- | --- |"])
        for mechanism, count in mechanisms.items():
            lines.append(f"| {mechanism} | {count} |")
    else:
        lines.append("_No false-unearned certified controls in matched Phase A rows._")
    lines.extend(
        [
            "",
            "## Table 5 — Invalid rate and sensitivity",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            (f"| Mean invalid π rate | {_format_float(invalid_rate['mean'])} |"),
            (f"| Invalid-positive count | {invalid_rate['positive_count']} |"),
            (
                f"| Invalid-positive rate | "
                f"{_format_float(invalid_rate['positive_rate'])} |"
            ),
            (
                f"| Mean EF sensitivity gap | "
                f"{_format_float(invalid_sensitivity['ef_sensitivity_gap_mean'])} |"
            ),
            "",
            "## Limitations",
            "",
            "- Maintainer merge is an imperfect proxy for semantic correctness.",
            "- Phase A join uses frozen golden harness rows by `instance_id`.",
            "- Unmatched certified rows are excluded from FUBR denominator.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_certified_controls_report(
    manifest_path: Path,
    phase_a_run_dir: Path,
    output_dir: Path,
) -> CertifiedControlsReportResult:
    """Validate manifest, join Phase A summary, and write anchor artifacts."""
    manifest_rows = load_certified_controls_manifest(manifest_path)
    phase_a_rows = load_phase_summary_rows(phase_a_run_dir)
    payload = analyze_certified_controls(manifest_rows, phase_a_rows)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report_md_path = output_dir / MAINTAINER_CERTIFIED_REPORT_MD
    summary_json_path = output_dir / MAINTAINER_CERTIFIED_SUMMARY_JSON
    ef_distribution_path = output_dir / MAINTAINER_CERTIFIED_EF_DISTRIBUTION_CSV
    false_unearned_path = output_dir / MAINTAINER_CERTIFIED_FALSE_UNEARNED_CSV

    distribution_rows = payload.pop("distribution_rows")
    false_unearned_rows = payload.pop("false_unearned_rows")

    _write_csv(ef_distribution_path, EF_DISTRIBUTION_COLUMNS, distribution_rows)
    _write_csv(false_unearned_path, FALSE_UNEARNED_COLUMNS, false_unearned_rows)

    report_md_path.write_text(
        render_certified_controls_report(payload),
        encoding="utf-8",
    )

    with summary_json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return CertifiedControlsReportResult(
        output_dir=output_dir,
        report_md=report_md_path,
        summary_json=summary_json_path,
        ef_distribution_csv=ef_distribution_path,
        false_unearned_csv=false_unearned_path,
    )
