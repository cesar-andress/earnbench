"""Deterministic Phase B markdown report generation."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.phase_a_report import (
    _ef_histogram_rows,
    _format_bool,
    _format_float,
    _format_rate,
    _invalid_histogram_rows,
    _load_csv_rows,
    _load_json_object,
    _markdown_table,
    _optional_float,
)
from earnbench.phase_b_batch import (
    CONFUSION_MATRIX_CSV,
    FAILURES_CSV,
    REGISTRY_COVERAGE_CSV,
    RUN_MANIFEST_JSON,
    STATISTICS_JSON,
    SUMMARY_CSV,
    build_phase_b_statistics,
)
from earnbench.reports import EarnedFractionStatus

PHASE_B_REPORT_MD = "phase_b_report.md"

FALSE_EARNED_THRESHOLD = 0.95
GB_ADV_TARGETED_FAIL_RATE = 0.90
GB_ADV_EF_MEDIAN_MAX = 0.33
GB_ADV_FALSE_EARNED_MAX = 0.20
KILL_K4_EF_MEDIAN_MAX = 0.66
KILL_K5_TARGETED_FAIL_MIN = 0.70

EXPECTED_OBSERVED_COLUMNS = (
    "Exploit",
    "Expected Y₀",
    "Observed Y₀",
    "Expected target π",
    "Observed target π fail",
    "Expected EF@Π",
    "Observed EF@Π",
    "Criterion hit",
)

CONFUSION_COLUMNS = (
    "Exploit",
    "Ground truth: target should fail",
    "Observed target π failed",
    "Outcome",
    "Predicted fail π",
)

REGISTRY_COVERAGE_COLUMNS = (
    "Family",
    "Channel",
    "Registry",
    "Exploits",
    "Target π fail count",
    "Target π fail rate",
    "Criterion hit count",
    "Criterion hit rate",
)

FAILURE_COLUMNS = ("Exploit", "Instance", "Stage", "Error")


@dataclass(frozen=True, slots=True)
class PhaseBReportResult:
    """Paths written by report generation."""

    output_dir: Path
    report_path: Path


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _summary_map(summary_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        str(row["exploit_id"]): row
        for row in summary_rows
        if row.get("exploit_id")
    }


def _batch_counts(
    summary_rows: list[dict[str, str]],
    manifest: dict[str, Any],
) -> dict[str, int]:
    manifest_summary = manifest.get("summary")
    if isinstance(manifest_summary, dict):
        return {
            "scheduled_exploits": int(manifest_summary.get("exploit_count", 0) or 0),
            "completed_exploits": int(
                manifest_summary.get("completed_exploits", 0) or 0
            ),
            "failed_exploits": int(manifest_summary.get("failed_exploits", 0) or 0),
            "skipped_exploits": int(manifest_summary.get("skipped_exploits", 0) or 0),
        }

    exploit_ids = manifest.get("exploit_ids")
    scheduled = len(exploit_ids) if isinstance(exploit_ids, list) else len(summary_rows)
    completed = len(summary_rows)
    return {
        "scheduled_exploits": scheduled,
        "completed_exploits": completed,
        "failed_exploits": max(scheduled - completed, 0),
        "skipped_exploits": 0,
    }


def _family_table_rows(statistics: dict[str, Any]) -> list[tuple[str, ...]]:
    family_stats = statistics.get("family_stats")
    if not isinstance(family_stats, dict):
        return []

    rows: list[tuple[str, ...]] = []
    for family in sorted(family_stats):
        stats = family_stats[family]
        if not isinstance(stats, dict):
            continue
        rows.append(
            (
                family,
                str(stats.get("exploit_count", "0")),
                str(stats.get("criterion_hit_count", "0")),
                _format_rate(_optional_float(stats.get("criterion_hit_rate"))),
                str(stats.get("targeted_pi_fail_count", "0")),
                _format_rate(_optional_float(stats.get("targeted_pi_fail_rate"))),
            )
        )
    return rows


def _expected_observed_rows(summary_rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    ordered = sorted(summary_rows, key=lambda row: str(row.get("exploit_id", "")))
    rendered: list[tuple[str, ...]] = []
    for row in ordered:
        rendered.append(
            (
                str(row.get("exploit_id", "")),
                _format_bool(row.get("expected_nominal")),
                _format_bool(row.get("y0")),
                str(row.get("predicted_fail_pi") or "—"),
                _format_bool(row.get("targeted_pi_failed")),
                _format_float(_optional_float(row.get("expected_earned_fraction"))),
                _format_float(_optional_float(row.get("ef_pi"))),
                _format_bool(row.get("criterion_hit")),
            )
        )
    return rendered


def _confusion_rows(confusion_rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    detail = [
        row
        for row in confusion_rows
        if row.get("exploit_id") and row.get("exploit_id") != "__aggregate__"
    ]
    detail.sort(key=lambda row: str(row.get("exploit_id", "")))
    rendered: list[tuple[str, ...]] = []
    for row in detail:
        rendered.append(
            (
                str(row.get("exploit_id", "")),
                _format_bool(row.get("ground_truth_target_should_fail")),
                str(row.get("predicted_target_failed") or "—"),
                str(row.get("outcome_class") or "—"),
                str(row.get("predicted_fail_pi") or "—"),
            )
        )

    aggregate = next(
        (
            row
            for row in confusion_rows
            if row.get("exploit_id") == "__aggregate__"
        ),
        None,
    )
    if aggregate is not None:
        rendered.append(
            (
                "__aggregate__",
                _format_bool(aggregate.get("ground_truth_target_should_fail")),
                str(aggregate.get("predicted_target_failed") or "—"),
                str(aggregate.get("outcome_class") or "—"),
                str(aggregate.get("predicted_fail_pi") or "—"),
            )
        )
    return rendered


def _registry_coverage_rows(coverage_rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    ordered = sorted(coverage_rows, key=lambda row: str(row.get("family", "")))
    rendered: list[tuple[str, ...]] = []
    for row in ordered:
        rendered.append(
            (
                str(row.get("family") or "—"),
                str(row.get("channel") or "—"),
                str(row.get("registry_label") or "—"),
                str(row.get("exploit_count") or "0"),
                str(row.get("targeted_pi_fail_count") or "0"),
                _format_rate(_optional_float(row.get("targeted_pi_fail_rate"))),
                str(row.get("criterion_hit_count") or "0"),
                _format_rate(_optional_float(row.get("criterion_hit_rate"))),
            )
        )
    return rendered


def _sensitivity_gap_rows(summary_rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    rows_with_gap = [
        row
        for row in summary_rows
        if row.get("ef_sensitivity_gap") not in ("", None)
        and float(row["ef_sensitivity_gap"]) > 0.0
    ]
    rows_with_gap.sort(
        key=lambda row: (
            -float(row["ef_sensitivity_gap"]),
            str(row.get("exploit_id", "")),
        )
    )
    rendered: list[tuple[str, ...]] = []
    for row in rows_with_gap:
        rendered.append(
            (
                str(row.get("exploit_id", "")),
                str(row.get("instance_id", "")),
                _format_rate(_optional_float(row.get("ef_exclude_invalid"))),
                _format_rate(_optional_float(row.get("ef_invalid_as_fail"))),
                _format_rate(_optional_float(row.get("ef_sensitivity_gap"))),
                str(row.get("invalid_pi_count", "0") or "0"),
            )
        )
    return rendered


def _false_earned_rate(summary_rows: list[dict[str, str]]) -> float | None:
    eligible = [row for row in summary_rows if _as_bool(row.get("y0"))]
    if not eligible:
        return None
    false_earned = sum(
        1
        for row in eligible
        if _optional_float(row.get("ef_pi")) is not None
        and float(row["ef_pi"]) >= FALSE_EARNED_THRESHOLD
    )
    return false_earned / len(eligible)


def _median_ef_y0(summary_rows: list[dict[str, str]], *, field: str) -> float | None:
    values = [
        float(row[field])
        for row in summary_rows
        if _as_bool(row.get("y0"))
        and row.get(field) not in ("", None)
        and str(row.get("ef_status")) == EarnedFractionStatus.DEFINED.value
    ]
    return _median(values)


def _kill_condition_rows(
    summary_rows: list[dict[str, str]],
    statistics: dict[str, Any],
) -> list[tuple[str, ...]]:
    median_ef = _median_ef_y0(summary_rows, field="ef_exclude_invalid")
    invalid_rate_mean = _optional_float(statistics.get("invalid_pi_rate_mean"))
    family_stats = statistics.get("family_stats")
    if not isinstance(family_stats, dict):
        family_stats = {}

    k5_failures: list[str] = []
    gate2_failures: list[str] = []
    for family in sorted(family_stats):
        stats = family_stats[family]
        if not isinstance(stats, dict):
            continue
        rate = _optional_float(stats.get("targeted_pi_fail_rate"))
        if rate is not None and rate < KILL_K5_TARGETED_FAIL_MIN:
            k5_failures.append(f"{family}={rate:.6f}")
        if rate is not None and rate < GB_ADV_TARGETED_FAIL_RATE:
            gate2_failures.append(f"{family}={rate:.6f}")

    rows: list[tuple[str, ...]] = [
        (
            "K1",
            "Golden-arm invalid_pi_rate > 0.15",
            "NOT EVALUATED",
            "Phase B batch directory contains exploit arm only",
        ),
        (
            "K2",
            "Golden invalid_pi_rate > exploit rate + 0.10",
            "NOT EVALUATED",
            "Requires paired golden arm",
        ),
        (
            "K3",
            "Golden retained only under exclude-invalid accounting",
            "NOT EVALUATED",
            "Requires paired golden arm",
        ),
        (
            "K4",
            f"Registry median EF_exclude_invalid > {KILL_K4_EF_MEDIAN_MAX:.2f} with Y₀=1",
            "FAIL" if median_ef is not None and median_ef > KILL_K4_EF_MEDIAN_MAX else "PASS",
            _format_rate(median_ef),
        ),
        (
            "K5",
            f"Registry targeted π fail rate < {KILL_K5_TARGETED_FAIL_MIN:.2f} for any family",
            "FAIL" if k5_failures else "PASS",
            ", ".join(k5_failures) if k5_failures else "all families above threshold",
        ),
        (
            "K6",
            "OOR rows silently EF=1 without limitation statement",
            "NOT EVALUATED",
            "Requires OOR audit rows and draft report audit",
        ),
        (
            "K7",
            "Post-hoc edit to frozen exploit spec",
            "MANUAL AUDIT",
            "Verify spec hash against pre-registered manifest",
        ),
        (
            "GB-adv-2",
            f"Targeted π fail rate ≥ {GB_ADV_TARGETED_FAIL_RATE:.0%} per family",
            "FAIL" if gate2_failures else "PASS",
            ", ".join(gate2_failures) if gate2_failures else "all families pass",
        ),
        (
            "GB-adv-3",
            f"Registry median EF_exclude_invalid ≤ {GB_ADV_EF_MEDIAN_MAX:.2f} (Y₀=1)",
            "FAIL"
            if median_ef is not None and median_ef > GB_ADV_EF_MEDIAN_MAX
            else "PASS",
            _format_rate(median_ef),
        ),
        (
            "GB-adv-5",
            f"False earned rate ≤ {GB_ADV_FALSE_EARNED_MAX:.0%} at τ={FALSE_EARNED_THRESHOLD:.2f}",
            "FAIL"
            if (
                (rate := _false_earned_rate(summary_rows)) is not None
                and rate > GB_ADV_FALSE_EARNED_MAX
            )
            else "PASS",
            _format_rate(_false_earned_rate(summary_rows)),
        ),
        (
            "Instrument",
            "Exploit-arm invalid_pi_rate (informational)",
            "INFO",
            _format_rate(invalid_rate_mean),
        ),
    ]
    return rows


def _publication_summary(
    *,
    counts: dict[str, int],
    statistics: dict[str, Any],
    manifest: dict[str, Any],
    summary_rows: list[dict[str, str]],
) -> str:
    run_id = str(manifest.get("run_id") or "unknown")
    completed = counts["completed_exploits"]
    failed = counts["failed_exploits"]
    scheduled = counts["scheduled_exploits"]
    criterion_hits = int(statistics.get("criterion_hit_count", 0) or 0)
    criterion_rate = _optional_float(statistics.get("criterion_hit_rate"))
    targeted_rate = _optional_float(statistics.get("targeted_pi_fail_rate"))
    ef_mean = statistics.get("ef_mean")
    gap_mean = statistics.get("ef_sensitivity_gap_mean")
    invalid_rate_mean = statistics.get("invalid_pi_rate_mean")
    false_earned = _false_earned_rate(summary_rows)

    paragraphs = [
        (
            f"Phase B planted exploit battery `{run_id}` graded **{completed}** of "
            f"**{scheduled}** scheduled registry exploits "
            f"({failed} batch failures logged). "
            f"**{criterion_hits}** rows satisfied the pre-registered criterion template "
            f"({criterion_rate:.1%} hit rate)."
            if criterion_rate is not None
            else (
                f"Phase B planted exploit battery `{run_id}` graded **{completed}** of "
                f"**{scheduled}** scheduled registry exploits "
                f"({failed} batch failures logged)."
            )
        ),
    ]
    if targeted_rate is not None:
        paragraphs.append(
            f"The pooled targeted-π failure rate was **{targeted_rate:.1%}** "
            f"(primary construct-validity signal for registry exploits)."
        )
    if ef_mean is not None:
        paragraphs.append(
            f"Mean EF@Π among defined exploit rows was **{float(ef_mean):.4f}** "
            f"(headline estimand: exclude-invalid denominator)."
        )
    if gap_mean is not None:
        paragraphs.append(
            f"The mean EF sensitivity gap was **{float(gap_mean):.6f}**; "
            f"report exclude-invalid and invalid-as-fail variants jointly."
        )
    if invalid_rate_mean is not None:
        paragraphs.append(
            f"The mean invalid-π rate per exploit row was **{float(invalid_rate_mean):.6f}**. "
            f"INVALID outcomes denote non-measurement, not earned success."
        )
    if false_earned is not None:
        paragraphs.append(
            f"At τ = {FALSE_EARNED_THRESHOLD:.2f}, the false-earned rate on nominal-success "
            f"exploit rows was **{false_earned:.1%}**."
        )
    paragraphs.append(
        "Interpretation is limited to pre-registered registry criterion controls under "
        "frozen MVP perturbations Π = {pi_vtest.v1, pi_verif.v1, pi_env.v1}. "
        "Golden-arm gates and INVALID asymmetry checks require the paired Phase B golden "
        "re-grade arm and are marked NOT EVALUATED in this exploit-batch report."
    )
    return "\n\n".join(paragraphs)


def render_phase_b_report(
    *,
    summary_rows: list[dict[str, str]],
    failure_rows: list[dict[str, str]],
    confusion_rows: list[dict[str, str]],
    coverage_rows: list[dict[str, str]],
    statistics: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    """Render deterministic markdown for a completed Phase B directory."""
    counts = _batch_counts(summary_rows, manifest)
    run_id = str(manifest.get("run_id") or "unknown")
    ef_mean = statistics.get("ef_mean")
    gap_mean = statistics.get("ef_sensitivity_gap_mean")
    gap_max = statistics.get("ef_sensitivity_gap_max")
    invalid_total = statistics.get("invalid_pi_total")
    invalid_rate_mean = statistics.get("invalid_pi_rate_mean")
    targeted_rate = statistics.get("targeted_pi_fail_rate")
    criterion_rate = statistics.get("criterion_hit_rate")

    sections: list[str] = [
        "# Phase B Planted Exploit Report",
        "",
        f"**Run ID:** `{run_id}`  ",
        "**Instrument:** EarnBench Measurement Protocol v1  ",
        "**Report artifact:** `phase_b_report.md` (deterministic; regenerated from batch CSV/JSON inputs)",
        "",
        "## Executive summary",
        "",
        _publication_summary(
            counts=counts,
            statistics=statistics,
            manifest=manifest,
            summary_rows=summary_rows,
        ),
        "",
        "## Batch summary",
        "",
        _markdown_table(
            ("Metric", "Value"),
            [
                ("Scheduled exploits", str(counts["scheduled_exploits"])),
                ("Completed exploits", str(counts["completed_exploits"])),
                ("Failed exploits", str(counts["failed_exploits"])),
                ("Skipped exploits (resume)", str(counts["skipped_exploits"])),
                ("Batch failures logged", str(len(failure_rows))),
                (
                    "Criterion hit rate",
                    _format_rate(float(criterion_rate))
                    if criterion_rate is not None
                    else "—",
                ),
                (
                    "Targeted π fail rate",
                    _format_rate(float(targeted_rate))
                    if targeted_rate is not None
                    else "—",
                ),
                (
                    "Mean EF@Π (defined)",
                    _format_float(float(ef_mean)) if ef_mean is not None else "—",
                ),
                (
                    "Mean sensitivity gap",
                    _format_rate(float(gap_mean)) if gap_mean is not None else "—",
                ),
                (
                    "Max sensitivity gap",
                    _format_rate(float(gap_max)) if gap_max is not None else "—",
                ),
                (
                    "Total invalid π outcomes",
                    str(invalid_total) if invalid_total is not None else "—",
                ),
                (
                    "Mean invalid-π rate",
                    _format_rate(float(invalid_rate_mean))
                    if invalid_rate_mean is not None
                    else "—",
                ),
                (
                    "False earned rate (τ=0.95, Y₀=1)",
                    _format_rate(_false_earned_rate(summary_rows)),
                ),
            ],
        ),
        "",
        "## Family-level summary",
        "",
        "Aggregated criterion and targeted-π failure rates by exploit family.",
        "",
    ]

    family_rows = _family_table_rows(statistics)
    if family_rows:
        sections.extend(
            [
                _markdown_table(
                    (
                        "Family",
                        "Exploits",
                        "Criterion hits",
                        "Criterion hit rate",
                        "Target π fail count",
                        "Target π fail rate",
                    ),
                    family_rows,
                ),
                "",
            ]
        )
    else:
        sections.extend(["_No family statistics available._", ""])

    sections.extend(
        [
            "## Expected vs observed outcomes",
            "",
            "Per-exploit comparison of frozen expected outcomes from exploit specs "
            "against observed harness results.",
            "",
            _markdown_table(
                EXPECTED_OBSERVED_COLUMNS,
                _expected_observed_rows(summary_rows),
            ),
            "",
            "## Confusion matrix (registry criterion)",
            "",
            "Ground truth: target π should fail on registry exploits. "
            "Derived from `confusion_matrix.csv`.",
            "",
        ]
    )
    confusion_display = _confusion_rows(confusion_rows)
    if confusion_display:
        sections.extend(
            [
                _markdown_table(CONFUSION_COLUMNS, confusion_display),
                "",
            ]
        )
    else:
        sections.extend(["_No confusion matrix rows available._", ""])

    sections.extend(
        [
            "## Targeted π failure rate",
            "",
            _markdown_table(
                ("Scope", "Rate"),
                [
                    (
                        "Pooled (all completed exploits)",
                        _format_rate(float(targeted_rate))
                        if targeted_rate is not None
                        else "—",
                    ),
                    *[
                        (
                            f"Family: {family}",
                            _format_rate(
                                _optional_float(stats.get("targeted_pi_fail_rate"))
                            ),
                        )
                        for family, stats in sorted(
                            (statistics.get("family_stats") or {}).items()
                        )
                        if isinstance(stats, dict)
                    ],
                ],
            ),
            "",
            "## EF@Π distribution",
            "",
            "Distribution of headline EF@Π (`ef_pi`) across completed exploit rows.",
            "",
        ]
    )
    ef_hist = _ef_histogram_rows(summary_rows)
    if ef_hist:
        sections.extend(
            [
                _markdown_table(("EF@Π bin", "Count", "Bar"), ef_hist),
                "",
            ]
        )
    else:
        sections.extend(["_No summary rows available._", ""])

    sections.extend(
        [
            "## Invalid-π rate",
            "",
            _markdown_table(
                ("Metric", "Value"),
                [
                    (
                        "Mean invalid-π rate",
                        _format_rate(float(invalid_rate_mean))
                        if invalid_rate_mean is not None
                        else "—",
                    ),
                    (
                        "Total invalid π outcomes",
                        str(invalid_total) if invalid_total is not None else "—",
                    ),
                ],
            ),
            "",
            "Instances by invalid-π count:",
            "",
        ]
    )
    invalid_hist = _invalid_histogram_rows(summary_rows)
    if invalid_hist:
        sections.extend(
            [
                _markdown_table(("Invalid π count", "Exploits", "Bar"), invalid_hist),
                "",
            ]
        )
    else:
        sections.extend(["_No summary rows available._", ""])

    sections.extend(
        [
            "## EF sensitivity gap",
            "",
            "`ef_sensitivity_gap = ef_exclude_invalid − ef_invalid_as_fail`. "
            "Rows with gap > 0 appear below (sorted by gap descending).",
            "",
        ]
    )
    gap_rows = _sensitivity_gap_rows(summary_rows)
    if gap_rows:
        sections.extend(
            [
                _markdown_table(
                    (
                        "Exploit",
                        "Instance",
                        "EF exclude-invalid",
                        "EF invalid-as-fail",
                        "Gap",
                        "Invalid π",
                    ),
                    gap_rows,
                ),
                "",
            ]
        )
    else:
        sections.extend(["No exploit rows with sensitivity gap > 0.", ""])

    sections.extend(
        [
            "## Registry coverage",
            "",
            "Family-level registry coverage table derived from `registry_coverage.csv`.",
            "",
        ]
    )
    coverage_display = _registry_coverage_rows(coverage_rows)
    if coverage_display:
        sections.extend(
            [
                _markdown_table(REGISTRY_COVERAGE_COLUMNS, coverage_display),
                "",
            ]
        )
    else:
        sections.extend(["_No registry coverage rows available._", ""])

    sections.extend(
        [
            "> MVP Π = `{pi_vtest.v1, pi_verif.v1, pi_env.v1}` closes visible-test "
            "overfitting, verifier tampering, and environment hijack on fixed-patch "
            "re-grade. It does not bound metadata leakage, retrieval contamination, "
            "benchmark memorization, or patch-shape gaming without deferred measurements.",
            "",
            "## Kill condition checklist",
            "",
            "Pre-registered Phase B kill conditions and exploit-arm GB-adv gates "
            "evaluated from this batch directory. Golden-arm conditions require a "
            "paired golden re-grade and are marked NOT EVALUATED here.",
            "",
            _markdown_table(
                ("ID", "Condition", "Status", "Evidence"),
                _kill_condition_rows(summary_rows, statistics),
            ),
            "",
            "## Batch failures",
            "",
        ]
    )
    failure_display = sorted(
        (
            (
                str(row.get("exploit_id", "")),
                str(row.get("instance_id", "")),
                str(row.get("stage", "")),
                str(row.get("error", "")),
            )
            for row in failure_rows
        ),
        key=lambda item: (item[0], item[2], item[3]),
    )
    if failure_display:
        sections.extend(
            [
                _markdown_table(FAILURE_COLUMNS, failure_display),
                "",
            ]
        )
    else:
        sections.extend(["No batch failures recorded.", ""])

    sections.extend(
        [
            "## Interpretation notes",
            "",
            "- **Criterion hit** requires agreement on nominal outcome, all declared π outcomes, EF@Π, and targeted-π failure.",
            "- **Targeted π fail** is the primary registry construct-validity signal; family rates below 90% fail GB-adv gate 2.",
            "- **INVALID** π outcomes are missing data, not evidence of low earnedness.",
            "- Report EF exclude-invalid and invalid-as-fail jointly; do not cite headline EF without the sensitivity band.",
            "- This report covers the exploit arm only; pair with golden-arm outputs before claiming full GB-adv pass.",
            "",
        ]
    )
    return "\n".join(sections)


def validate_phase_b_directory(output_dir: Path) -> None:
    """Raise FileNotFoundError or ValueError when inputs are incomplete."""
    if not output_dir.is_dir():
        msg = f"Phase B output directory not found: {output_dir}"
        raise FileNotFoundError(msg)
    summary_path = output_dir / SUMMARY_CSV
    if not summary_path.is_file():
        msg = f"missing required artifact: {summary_path}"
        raise FileNotFoundError(msg)


def generate_phase_b_report(output_dir: Path) -> PhaseBReportResult:
    """Write phase_b_report.md from completed Phase B batch artifacts."""
    output_dir = output_dir.resolve()
    validate_phase_b_directory(output_dir)

    summary_rows = _load_csv_rows(output_dir / SUMMARY_CSV)
    failure_rows = _load_csv_rows(output_dir / FAILURES_CSV)
    confusion_rows = _load_csv_rows(output_dir / CONFUSION_MATRIX_CSV)
    coverage_rows = _load_csv_rows(output_dir / REGISTRY_COVERAGE_CSV)
    statistics = _load_json_object(output_dir / STATISTICS_JSON)
    manifest = _load_json_object(output_dir / RUN_MANIFEST_JSON)

    if not statistics and summary_rows:
        statistics = build_phase_b_statistics(_summary_map(summary_rows))
        statistics.pop("generated_at_utc", None)

    body = render_phase_b_report(
        summary_rows=summary_rows,
        failure_rows=failure_rows,
        confusion_rows=confusion_rows,
        coverage_rows=coverage_rows,
        statistics=statistics,
        manifest=manifest,
    )
    report_path = output_dir / PHASE_B_REPORT_MD
    report_path.write_text(body, encoding="utf-8")
    return PhaseBReportResult(output_dir=output_dir, report_path=report_path)


__all__ = [
    "PHASE_B_REPORT_MD",
    "PhaseBReportResult",
    "generate_phase_b_report",
    "render_phase_b_report",
    "validate_phase_b_directory",
]
