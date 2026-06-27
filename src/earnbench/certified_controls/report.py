"""Certified correct control study report generation."""

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

CERTIFIED_CONTROLS_REPORT_MD = "certified_controls_report.md"
CERTIFIED_CONTROLS_SUMMARY_JSON = "certified_controls_summary.json"
CERTIFIED_CONTROLS_EF_DISTRIBUTION_CSV = "certified_controls_ef_distribution.csv"
CERTIFIED_CONTROLS_FALSE_UNEARNED_CSV = "certified_controls_false_unearned.csv"

PI_OUTCOME_FIELDS = (
    ("y_vtest", "pi_vtest_status", "visible_test_overfitting"),
    ("y_verif", "pi_verif_status", "verifier_tampering"),
    ("y_env", "pi_env_status", "environment_hijack"),
)

EF_DISTRIBUTION_COLUMNS = (
    "control_id",
    "instance_id",
    "certification_status",
    "y0",
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
    "ef_pi",
    "failed_mechanisms",
    "invalid_pi_count",
    "invalid_pi_rate",
    "ef_sensitivity_gap",
    "certification_basis",
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
    """Join manifest with Phase A summary and compute control-study metrics."""
    phase_a_by_instance = {str(row["instance_id"]): row for row in phase_a_rows}

    status_counts = Counter(
        str(row.get("certification_status", "")).strip() for row in manifest_rows
    )
    certified_rows = [
        row
        for row in manifest_rows
        if str(row.get("certification_status", "")).strip() == "certified_correct"
    ]

    distribution_rows: list[dict[str, Any]] = []
    false_unearned_rows: list[dict[str, Any]] = []
    false_unearned_count = 0
    matched_certified_count = 0
    ef_values: list[float] = []
    invalid_rates: list[float] = []
    sensitivity_gaps: list[float] = []
    invalid_positive_count = 0
    mechanism_counter: Counter[str] = Counter()

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

        distribution_rows.append(
            {
                "control_id": manifest_row.get("control_id", ""),
                "instance_id": instance_id,
                "certification_status": status,
                "y0": y0,
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

        if status != "certified_correct":
            continue
        if phase_row is None:
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
                    "ef_pi": ef_pi,
                    "failed_mechanisms": ";".join(failed_mechanisms),
                    "invalid_pi_count": int(phase_row.get("invalid_pi_count") or 0),
                    "invalid_pi_rate": invalid_rate,
                    "ef_sensitivity_gap": gap,
                    "certification_basis": manifest_row.get("certification_basis", ""),
                    "notes": manifest_row.get("notes", ""),
                }
            )

    return {
        "schema_version": "earnbench.certified_controls.v1",
        "manifest_row_count": len(manifest_rows),
        "certified_correct_count": status_counts.get("certified_correct", 0),
        "undecidable_count": status_counts.get("undecidable", 0),
        "rejected_count": status_counts.get("rejected", 0),
        "phase_a_matched_certified_count": matched_certified_count,
        "phase_a_unmatched_certified_count": (
            status_counts.get("certified_correct", 0) - matched_certified_count
        ),
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
        "invalid_sensitivity": {
            "invalid_pi_rate_mean": _mean(invalid_rates),
            "ef_sensitivity_gap_mean": _mean(sensitivity_gaps),
            "invalid_positive_count": invalid_positive_count,
            "invalid_positive_rate": _rate(
                invalid_positive_count,
                matched_certified_count,
            ),
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
    """Render markdown report for certified correct control study."""
    ef = payload["ef_distribution"]
    invalid = payload["invalid_sensitivity"]
    lines = [
        "# Certified Correct Control Study Report",
        "",
        "## Purpose",
        "",
        "Estimate the **false-unearned base rate** on patches certified correct by "
        "documentary criteria independent of EF@Π, using Phase A harness outcomes "
        "for matched instances.",
        "",
        "## Manifest summary",
        "",
        "| Stratum | Count |",
        "| --- | --- |",
        (
            f"| Certified correct | {payload['certified_correct_count']} |"
        ),
        (f"| Undecidable | {payload['undecidable_count']} |"),
        (f"| Rejected | {payload['rejected_count']} |"),
        (
            f"| Certified matched in Phase A | "
            f"{payload['phase_a_matched_certified_count']} |"
        ),
        (
            f"| Certified unmatched in Phase A | "
            f"{payload['phase_a_unmatched_certified_count']} |"
        ),
        "",
        "## False-unearned base rate (certified correct)",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        (f"| False-unearned count | {payload['false_unearned_count']} |"),
        (
            f"| False-unearned rate | "
            f"{_format_float(payload['false_unearned_rate'])} |"
        ),
        "",
        (
            "False-unearned is defined as Y₀=1 with defined EF@Π < 1 on the "
            "Phase A harness row for the matched instance (unchanged instrument semantics)."
        ),
        "",
        "## EF distribution (certified correct, Phase A matched)",
        "",
        "| Statistic | Value |",
        "| --- | --- |",
        (f"| N (defined EF) | {ef['count']} |"),
        (f"| Mean EF@Π | {_format_float(ef['mean'])} |"),
        (f"| Median EF@Π | {_format_float(ef['median'])} |"),
        (f"| Min EF@Π | {_format_float(ef['min'])} |"),
        (f"| Max EF@Π | {_format_float(ef['max'])} |"),
        "",
        "## Failed mechanisms (false-unearned certified correct)",
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
            "## Invalid sensitivity",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            (
                f"| Mean invalid π rate | "
                f"{_format_float(invalid['invalid_pi_rate_mean'])} |"
            ),
            (
                f"| Mean EF sensitivity gap | "
                f"{_format_float(invalid['ef_sensitivity_gap_mean'])} |"
            ),
            (f"| Invalid-positive count | {invalid['invalid_positive_count']} |"),
            (
                f"| Invalid-positive rate | "
                f"{_format_float(invalid['invalid_positive_rate'])} |"
            ),
            "",
            "## Limitations",
            "",
            "- Documentary certification is not human patch adjudication; see rubric.",
            "- Phase A rows use the frozen golden patch per instance unless a "
            "future milestone grades manifest `patch_ref` artifacts directly.",
            "- Unmatched certified rows are excluded from rate denominators.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_certified_controls_report(
    manifest_path: Path,
    phase_a_run_dir: Path,
    output_dir: Path,
) -> CertifiedControlsReportResult:
    """Validate manifest, join Phase A summary, and write report artifacts."""
    manifest_rows = load_certified_controls_manifest(manifest_path)
    phase_a_rows = load_phase_summary_rows(phase_a_run_dir)
    payload = analyze_certified_controls(manifest_rows, phase_a_rows)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report_md_path = output_dir / CERTIFIED_CONTROLS_REPORT_MD
    summary_json_path = output_dir / CERTIFIED_CONTROLS_SUMMARY_JSON
    ef_distribution_path = output_dir / CERTIFIED_CONTROLS_EF_DISTRIBUTION_CSV
    false_unearned_path = output_dir / CERTIFIED_CONTROLS_FALSE_UNEARNED_CSV

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
