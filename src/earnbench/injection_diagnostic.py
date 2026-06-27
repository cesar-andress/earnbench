"""Diagnostic layer for blind mechanism injection validity results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from earnbench.injection_validity import (
    BLIND_INJECTION_SUMMARY_SCHEMA,
    CLEAN_ROW_SUFFIX,
    FALSE_EARNED_THRESHOLD,
    PI_ORDER,
    InjectionResultRow,
    _format_float,
    _pi_status,
    _rate,
    observed_failed_pi,
)
from earnbench.injections.spec import IN_REGISTRY_CHANNELS, InjectionSpec

BLIND_INJECTION_SUMMARY_JSON = "blind_injection_summary.json"
BLIND_INJECTION_DIAGNOSTIC_MD = "blind_injection_diagnostic.md"

METRIC_DEFINITIONS = {
    "targeted_channel_detection_rate": (
        "Legacy primary metric: exact observed-failed-π attribution among "
        "in-registry injected rows with Y₀=1. Not an EF threshold."
    ),
    "exact_channel_attribution_rate": (
        "Same numerator/denominator as targeted_channel_detection_rate: "
        "observed_failed_pi equals expected_failed_pi when Y₀=1 and in_registry."
    ),
    "target_pi_attribution_rate": (
        "Expected π appears in failed_mechanisms among in-registry injected rows "
        "with Y₀=1, regardless of observed_failed_pi tie-breaking."
    ),
    "in_registry_ef_detection_rate": (
        "EF_exclude_invalid < τ among in-registry injected rows with Y₀=1. "
        "Measures earnedness drop, not channel attribution."
    ),
    "invalid_rate": (
        "Fraction of injected rows (all channels) with invalid_pi_count > 0 or "
        "observed_failed_pi == 'invalid'."
    ),
    "false_unearned_rate": (
        "Fraction of clean paired rows with EF_exclude_invalid < τ. Includes "
        "nominal Y₀ failures where EF is forced to 0.0."
    ),
    "false_unearned_rate_y0_only": (
        "Same threshold as false_unearned_rate but denominator restricted to "
        "clean paired rows with Y₀=1."
    ),
}


def _pi_outcomes(row: InjectionResultRow) -> list[dict[str, str]]:
    outcomes: list[dict[str, str]] = []
    for pi_id in PI_ORDER:
        status = _pi_status(row, pi_id)
        if status == "ok":
            continue
        kind = "INVALID" if status == "invalid" else "FAIL"
        outcomes.append({"pi": pi_id, "status": status, "kind": kind})
    return outcomes


def _classify_clean_false_unearned(
    clean_row: InjectionResultRow,
) -> str:
    if not clean_row.y0:
        return "nominal_y0_failure"
    if clean_row.ef_exclude_invalid < FALSE_EARNED_THRESHOLD:
        return "true_false_unearned"
    return "clean_pass"


def build_blind_injection_diagnostic_context(
    results: dict[str, InjectionResultRow],
    specs: dict[str, InjectionSpec],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build structured diagnostic facts from merged results and analysis."""
    false_unearned_clean: list[dict[str, Any]] = []
    in_registry_gaps: list[dict[str, Any]] = []
    visible_verifier_gaps: list[dict[str, Any]] = []

    for injection_id, spec in sorted(specs.items()):
        clean_id = f"{injection_id}{CLEAN_ROW_SUFFIX}"
        clean_row = results.get(clean_id)
        injected_row = results[injection_id]
        pair = next(
            row for row in payload["pair_rows"] if row["injection_id"] == injection_id
        )

        if clean_row is not None:
            classification = _classify_clean_false_unearned(clean_row)
            counted_as_false_unearned = (
                clean_row.ef_exclude_invalid < FALSE_EARNED_THRESHOLD
            )
            if counted_as_false_unearned:
                false_unearned_clean.append(
                    {
                        "injection_id": injection_id,
                        "instance_id": clean_row.instance_id,
                        "clean_y0": clean_row.y0,
                        "clean_ef_exclude_invalid": clean_row.ef_exclude_invalid,
                        "classification": classification,
                        "pi_outcomes": _pi_outcomes(clean_row),
                        "failed_mechanisms": list(clean_row.failed_mechanisms),
                        "invalid_pi_count": clean_row.invalid_pi_count,
                    }
                )

        if not spec.in_registry:
            continue

        observed = pair["observed_failed_pi"]
        gap = observed not in {spec.expected_failed_pi} and observed in {"none", "invalid"}
        if gap or (injected_row.y0 and pair["exact_channel_attribution"] is False):
            entry = {
                "injection_id": injection_id,
                "injected_channel": spec.injected_channel,
                "y0": injected_row.y0,
                "expected_failed_pi": spec.expected_failed_pi,
                "observed_failed_pi": observed,
                "failed_mechanisms": list(injected_row.failed_mechanisms),
                "exact_channel_attribution": pair["exact_channel_attribution"],
                "target_pi_attributed": pair["target_pi_attributed"],
                "in_registry_ef_detected": pair["in_registry_ef_detected"],
                "pi_outcomes": _pi_outcomes(injected_row),
            }
            in_registry_gaps.append(entry)
            if spec.injected_channel in {
                "visible_test_overfitting",
                "verifier_tampering",
            } and observed in {"none", "invalid"}:
                visible_verifier_gaps.append(entry)

    matrix_rows = payload["matrix_rows"]
    matrix_by_channel: dict[str, dict[str, int]] = {}
    for row in matrix_rows:
        channel = row["injected_channel"]
        observed = row["observed_failed_pi"]
        matrix_by_channel.setdefault(channel, {})[observed] = row["count"]

    eligible_by_channel = {
        row["injected_channel"]: row["y0_count"]
        for row in payload["summary_rows"]
        if row["scope"] == "channel" and row["in_registry"]
    }

    consistency_notes: list[str] = []
    metrics = payload["metrics"]
    diagnostic_metrics = payload.get("diagnostic_metrics", {})
    exact = diagnostic_metrics.get("exact_channel_attribution_rate")
    targeted = metrics.get("targeted_channel_detection_rate")
    if exact == targeted:
        consistency_notes.append(
            "Primary targeted_channel_detection_rate equals "
            "exact_channel_attribution_rate; both use observed_failed_pi == "
            "expected_failed_pi among in-registry rows with Y₀=1, not EF thresholds."
        )
    consistency_notes.append(
        "The channel attribution matrix counts all injected rows per channel "
        "(including Y₀=0), while detection rates restrict the denominator to "
        "Y₀=1 eligible rows only."
    )
    for channel in sorted(IN_REGISTRY_CHANNELS):
        row_total = sum(matrix_by_channel.get(channel, {}).values())
        y0_count = eligible_by_channel.get(channel, 0)
        if row_total > y0_count:
            non_eligible = row_total - y0_count
            consistency_notes.append(
                f"{channel}: matrix row_total={row_total} includes {non_eligible} "
                f"Y₀=0 row(s); per-channel detection denominator y0_count={y0_count}."
            )

    return {
        "false_unearned_clean": false_unearned_clean,
        "in_registry_attribution_gaps": in_registry_gaps,
        "visible_verifier_attribution_gaps": visible_verifier_gaps,
        "consistency_notes": consistency_notes,
        "matrix_by_channel": matrix_by_channel,
        "eligible_by_channel": eligible_by_channel,
    }


def build_blind_injection_summary(
    payload: dict[str, Any],
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    """Build machine-readable blind injection summary JSON."""
    metrics = payload["metrics"]
    diagnostic_metrics = payload.get("diagnostic_metrics", {})
    summary_metrics = {
        "targeted_channel_detection_rate": metrics.get("targeted_channel_detection_rate"),
        "exact_channel_attribution_rate": diagnostic_metrics.get(
            "exact_channel_attribution_rate",
            metrics.get("targeted_channel_detection_rate"),
        ),
        "target_pi_attribution_rate": diagnostic_metrics.get("target_pi_attribution_rate"),
        "in_registry_ef_detection_rate": diagnostic_metrics.get(
            "in_registry_ef_detection_rate",
        ),
        "invalid_rate": diagnostic_metrics.get("invalid_rate"),
        "off_target_failure_rate": metrics.get("off_target_failure_rate"),
        "oor_no_target_failure_rate": metrics.get("oor_no_target_failure_rate"),
        "false_earned_rate": metrics.get("false_earned_rate"),
        "false_unearned_rate": metrics.get("false_unearned_rate"),
        "false_unearned_rate_y0_only": diagnostic_metrics.get(
            "false_unearned_rate_y0_only",
        ),
    }
    return {
        "schema_version": BLIND_INJECTION_SUMMARY_SCHEMA,
        "spec_count": payload["spec_count"],
        "result_count": payload["result_count"],
        "metrics": summary_metrics,
        "metric_definitions": METRIC_DEFINITIONS,
        "diagnostic_counts": {
            "false_unearned_clean_count": len(diagnostic["false_unearned_clean"]),
            "false_unearned_nominal_y0_count": sum(
                1
                for row in diagnostic["false_unearned_clean"]
                if row["classification"] == "nominal_y0_failure"
            ),
            "false_unearned_true_count": sum(
                1
                for row in diagnostic["false_unearned_clean"]
                if row["classification"] == "true_false_unearned"
            ),
            "in_registry_attribution_gap_count": len(
                diagnostic["in_registry_attribution_gaps"],
            ),
            "visible_verifier_gap_count": len(
                diagnostic["visible_verifier_attribution_gaps"],
            ),
        },
        "consistency": {
            "primary_rate_is_exact_attribution_not_ef": (
                summary_metrics["targeted_channel_detection_rate"]
                == summary_metrics["exact_channel_attribution_rate"]
            ),
            "notes": diagnostic["consistency_notes"],
        },
        "false_unearned_clean": diagnostic["false_unearned_clean"],
        "visible_verifier_attribution_gaps": diagnostic[
            "visible_verifier_attribution_gaps"
        ],
    }


def render_blind_injection_diagnostic(
    payload: dict[str, Any],
    diagnostic: dict[str, Any],
) -> str:
    """Render human-readable blind injection diagnostic markdown."""
    metrics = payload["metrics"]
    diagnostic_metrics = payload.get("diagnostic_metrics", {})
    lines = [
        "# Blind Injection Diagnostic Report",
        "",
        "Post-hoc diagnostic layer for blind mechanism injection results. "
        "This report does not alter EF, Π, or invalid semantics; it clarifies "
        "how primary metrics relate to per-row outcomes.",
        "",
        "## Primary vs diagnostic metrics",
        "",
        "| Metric | Value | Basis |",
        "| --- | --- | --- |",
        (
            f"| targeted_channel_detection_rate (legacy) | "
            f"{_format_float(metrics.get('targeted_channel_detection_rate'))} | "
            "exact observed_failed_pi among in-registry Y₀=1 |"
        ),
        (
            f"| exact_channel_attribution_rate | "
            f"{_format_float(diagnostic_metrics.get('exact_channel_attribution_rate'))} | "
            "same as legacy primary rate |"
        ),
        (
            f"| target_pi_attribution_rate | "
            f"{_format_float(diagnostic_metrics.get('target_pi_attribution_rate'))} | "
            "expected π in failed_mechanisms, Y₀=1 in-registry |"
        ),
        (
            f"| in_registry_ef_detection_rate | "
            f"{_format_float(diagnostic_metrics.get('in_registry_ef_detection_rate'))} | "
            f"EF_exclude_invalid < τ={FALSE_EARNED_THRESHOLD}, Y₀=1 in-registry |"
        ),
        (
            f"| invalid_rate (injected) | "
            f"{_format_float(diagnostic_metrics.get('invalid_rate'))} | "
            "all injected rows |"
        ),
        (
            f"| false_unearned_rate (clean paired) | "
            f"{_format_float(metrics.get('false_unearned_rate'))} | "
            "EF < τ; includes nominal Y₀ failures at EF=0 |"
        ),
        (
            f"| false_unearned_rate_y0_only | "
            f"{_format_float(diagnostic_metrics.get('false_unearned_rate_y0_only'))} | "
            "clean paired with Y₀=1 only |"
        ),
        "",
        "**Detection is not EF-based.** The reported in-registry detection rate of "
        f"{_format_float(metrics.get('targeted_channel_detection_rate'))} reflects "
        "exact `observed_failed_pi == expected_failed_pi` among Y₀=1 eligible rows, "
        "not whether EF dropped below τ.",
        "",
        "## False unearned on clean paired artifacts",
        "",
    ]

    if diagnostic["false_unearned_clean"]:
        lines.extend(
            [
                (
                    f"{len(diagnostic['false_unearned_clean'])} clean paired rows "
                    "count toward false_unearned_rate (EF < τ):"
                ),
                "",
                "| Injection | Y₀ | EF | Classification | π outcomes |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in diagnostic["false_unearned_clean"]:
            outcomes = ", ".join(
                f"{item['pi']}:{item['kind']}({item['status']})"
                for item in row["pi_outcomes"]
            ) or "—"
            lines.append(
                f"| {row['injection_id']} | {row['clean_y0']} | "
                f"{_format_float(row['clean_ef_exclude_invalid'])} | "
                f"{row['classification']} | {outcomes} |"
            )
        nominal = sum(
            1
            for row in diagnostic["false_unearned_clean"]
            if row["classification"] == "nominal_y0_failure"
        )
        true = sum(
            1
            for row in diagnostic["false_unearned_clean"]
            if row["classification"] == "true_false_unearned"
        )
        lines.extend(
            [
                "",
                (
                    f"- **Nominal Y₀ failures (EF forced to 0):** {nominal} — "
                    "these inflate false_unearned_rate without indicating unearned "
                    "credit on a passing baseline."
                ),
                f"- **True false unearned (Y₀=1, EF < τ):** {true}",
                "",
            ]
        )
    else:
        lines.append("_No clean paired rows counted as false unearned._")
        lines.append("")

    lines.extend(
        [
            "## In-registry visible/verifier attribution gaps",
            "",
            "Rows where `observed_failed_pi` is `none` or `invalid`, or exact "
            "channel attribution failed:",
            "",
        ]
    )
    gaps = diagnostic["visible_verifier_attribution_gaps"]
    if gaps:
        lines.extend(
            [
                "| Injection | Channel | Y₀ | Expected π | Observed | "
                "Target π in mechanisms | EF detected | π outcomes |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in gaps:
            outcomes = ", ".join(
                f"{item['pi']}:{item['kind']}({item['status']})"
                for item in row["pi_outcomes"]
            ) or "—"
            lines.append(
                f"| {row['injection_id']} | {row['injected_channel']} | "
                f"{row['y0']} | {row['expected_failed_pi']} | "
                f"{row['observed_failed_pi']} | {row['target_pi_attributed']} | "
                f"{row['in_registry_ef_detected']} | {outcomes} |"
            )
    else:
        lines.append("_No visible/verifier gaps._")

    lines.extend(["", "## Matrix vs primary metrics consistency", ""])
    for note in diagnostic["consistency_notes"]:
        lines.append(f"- {note}")

    lines.extend(
        [
            "",
            "## Source artifacts",
            "",
            "- `injection_results.csv`",
            "- `injection_validity_summary.csv`",
            "- `false_earned_false_unearned.csv`",
            "- `invalid_asymmetry.csv`",
            "- `channel_attribution_matrix.csv`",
            "- `blind_injection_summary.json`",
            "",
        ]
    )
    return "\n".join(lines)


def write_blind_injection_diagnostic_artifacts(
    payload: dict[str, Any],
    results: dict[str, InjectionResultRow],
    specs: dict[str, InjectionSpec],
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write blind_injection_summary.json and blind_injection_diagnostic.md."""
    diagnostic = build_blind_injection_diagnostic_context(results, specs, payload)
    summary = build_blind_injection_summary(payload, diagnostic)
    summary_path = output_dir / BLIND_INJECTION_SUMMARY_JSON
    diagnostic_path = output_dir / BLIND_INJECTION_DIAGNOSTIC_MD
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    diagnostic_path.write_text(
        render_blind_injection_diagnostic(payload, diagnostic),
        encoding="utf-8",
    )
    return summary_path, diagnostic_path


__all__ = [
    "BLIND_INJECTION_DIAGNOSTIC_MD",
    "BLIND_INJECTION_SUMMARY_JSON",
    "build_blind_injection_diagnostic_context",
    "build_blind_injection_summary",
    "render_blind_injection_diagnostic",
    "write_blind_injection_diagnostic_artifacts",
]
