"""Deterministic Phase A markdown report generation."""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.phase_a_batch import (
    FAILURES_CSV,
    RUN_MANIFEST_JSON,
    STATISTICS_JSON,
    SUMMARY_CSV,
)
from earnbench.reports import EarnedFractionStatus

PHASE_A_REPORT_MD = "phase_a_report.md"

SUMMARY_DISPLAY_COLUMNS = (
    "instance_id",
    "repo",
    "y0",
    "ef_pi",
    "ef_exclude_invalid",
    "ef_invalid_as_fail",
    "ef_sensitivity_gap",
    "invalid_pi_count",
    "invalid_pi_rate",
    "ef_status",
    "retained",
    "exclude_reason",
)

RETAINED_COLUMNS = (
    "instance_id",
    "repo",
    "ef_pi",
    "invalid_pi_count",
    "ef_sensitivity_gap",
)

EXCLUDED_COLUMNS = (
    "instance_id",
    "repo",
    "ef_pi",
    "ef_status",
    "exclude_reason",
    "false_unearned",
)

FAILURE_DISPLAY_COLUMNS = ("instance_id", "stage", "error")

EF_HISTOGRAM_BINS = (
    "0.0000",
    "0.3333",
    "0.6667",
    "1.0000",
    "undefined",
)


@dataclass(frozen=True, slots=True)
class PhaseAReportResult:
    """Paths written by report generation."""

    output_dir: Path
    report_path: Path


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"{path} must contain a JSON object"
        raise ValueError(msg)
    return payload


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _optional_float(value: object) -> float | None:
    if value in ("", None):
        return None
    return float(value)


def _format_float(value: float | None, *, precision: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:.{precision}f}"


def _format_rate(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.6f}"


def _format_bool(value: object) -> str:
    return "yes" if _as_bool(value) else "no"


def _markdown_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _histogram_bar(count: int, *, max_count: int, width: int = 24) -> str:
    if max_count <= 0:
        return ""
    filled = int(round((count / max_count) * width))
    return "█" * filled


def _ef_histogram_rows(summary_rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    counts: Counter[str] = Counter()
    for row in summary_rows:
        status = str(row.get("ef_status", ""))
        if status != EarnedFractionStatus.DEFINED.value:
            counts["undefined"] += 1
            continue
        ef_raw = row.get("ef_pi")
        if ef_raw in ("", None):
            counts["undefined"] += 1
            continue
        key = f"{float(ef_raw):.4f}"
        counts[key] += 1

    ordered_keys = [key for key in EF_HISTOGRAM_BINS if counts[key]]
    for key in sorted(counts, key=lambda item: (item == "undefined", float(item) if item != "undefined" else -1.0)):
        if key not in ordered_keys:
            ordered_keys.append(key)

    max_count = max(counts.values()) if counts else 0
    return [
        (key, str(counts[key]), _histogram_bar(counts[key], max_count=max_count))
        for key in ordered_keys
    ]


def _invalid_histogram_rows(summary_rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    counts: Counter[int] = Counter()
    for row in summary_rows:
        raw = row.get("invalid_pi_count", "0") or "0"
        counts[int(raw)] += 1
    max_count = max(counts.values()) if counts else 0
    return [
        (str(invalid_count), str(counts[invalid_count]), _histogram_bar(counts[invalid_count], max_count=max_count))
        for invalid_count in sorted(counts)
    ]


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
            str(row.get("instance_id", "")),
        )
    )
    rendered: list[tuple[str, ...]] = []
    for row in rows_with_gap:
        rendered.append(
            (
                str(row.get("instance_id", "")),
                str(row.get("repo", "")),
                _format_rate(_optional_float(row.get("ef_exclude_invalid"))),
                _format_rate(_optional_float(row.get("ef_invalid_as_fail"))),
                _format_rate(_optional_float(row.get("ef_sensitivity_gap"))),
                str(row.get("invalid_pi_count", "0") or "0"),
            )
        )
    return rendered


def _summary_table_rows(summary_rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    ordered = sorted(summary_rows, key=lambda row: str(row.get("instance_id", "")))
    rendered: list[tuple[str, ...]] = []
    for row in ordered:
        rendered.append(
            (
                str(row.get("instance_id", "")),
                str(row.get("repo", "")),
                _format_bool(row.get("y0")),
                _format_float(_optional_float(row.get("ef_pi"))),
                _format_float(_optional_float(row.get("ef_exclude_invalid"))),
                _format_float(_optional_float(row.get("ef_invalid_as_fail"))),
                _format_rate(_optional_float(row.get("ef_sensitivity_gap"))),
                str(row.get("invalid_pi_count", "0") or "0"),
                _format_rate(_optional_float(row.get("invalid_pi_rate"))),
                str(row.get("ef_status", "")),
                _format_bool(row.get("retained")),
                str(row.get("exclude_reason") or "—"),
            )
        )
    return rendered


def _retained_rows(summary_rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    retained = [row for row in summary_rows if _as_bool(row.get("retained"))]
    retained.sort(key=lambda row: str(row.get("instance_id", "")))
    return [
        (
            str(row.get("instance_id", "")),
            str(row.get("repo", "")),
            _format_float(_optional_float(row.get("ef_pi"))),
            str(row.get("invalid_pi_count", "0") or "0"),
            _format_rate(_optional_float(row.get("ef_sensitivity_gap"))),
        )
        for row in retained
    ]


def _excluded_rows(summary_rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    excluded = [row for row in summary_rows if not _as_bool(row.get("retained"))]
    excluded.sort(
        key=lambda row: (
            str(row.get("exclude_reason") or ""),
            str(row.get("instance_id", "")),
        )
    )
    return [
        (
            str(row.get("instance_id", "")),
            str(row.get("repo", "")),
            _format_float(_optional_float(row.get("ef_pi"))),
            str(row.get("ef_status", "")),
            str(row.get("exclude_reason") or "—"),
            _format_bool(row.get("false_unearned")),
        )
        for row in excluded
    ]


def _top_failure_rows(failure_rows: list[dict[str, str]], *, limit: int = 20) -> list[tuple[str, ...]]:
    ordered = sorted(
        failure_rows,
        key=lambda row: (str(row.get("instance_id", "")), str(row.get("stage", "")), str(row.get("error", ""))),
    )
    return [
        (
            str(row.get("instance_id", "")),
            str(row.get("stage", "")),
            str(row.get("error", "")),
        )
        for row in ordered[:limit]
    ]


def _aggregate_counts(summary_rows: list[dict[str, str]]) -> dict[str, int]:
    retained = sum(1 for row in summary_rows if _as_bool(row.get("retained")))
    excluded = len(summary_rows) - retained
    false_unearned = sum(1 for row in summary_rows if _as_bool(row.get("false_unearned")))
    ef_defined = sum(
        1
        for row in summary_rows
        if str(row.get("ef_status")) == EarnedFractionStatus.DEFINED.value
    )
    return {
        "instance_count": len(summary_rows),
        "retained_count": retained,
        "excluded_count": excluded,
        "false_unearned_count": false_unearned,
        "ef_defined_count": ef_defined,
        "ef_undefined_count": len(summary_rows) - ef_defined,
    }


def _publication_summary(
    *,
    counts: dict[str, int],
    statistics: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    run_id = str(manifest.get("run_id") or "unknown")
    instance_count = counts["instance_count"]
    retained_count = counts["retained_count"]
    excluded_count = counts["excluded_count"]
    false_unearned_count = counts["false_unearned_count"]
    retention_rate = retained_count / instance_count if instance_count else 0.0
    ef_mean = statistics.get("ef_mean")
    gap_mean = statistics.get("ef_sensitivity_gap_mean")
    invalid_rate_mean = statistics.get("invalid_pi_rate_mean")

    paragraphs = [
        (
            f"Phase A golden validation run `{run_id}` graded **{instance_count}** "
            f"instances under EarnBench Measurement Protocol v1. "
            f"**{retained_count}** instances ({retention_rate:.1%}) were retained "
            f"(nominal success with EF@Π = 1.0000); **{excluded_count}** were excluded "
            f"from the retained pilot set."
        ),
    ]
    if false_unearned_count:
        paragraphs.append(
            f"Among excluded instances, **{false_unearned_count}** exhibited "
            f"**false unearned** behavior (defined EF@Π < 1.0000 on golden patches)."
        )
    if ef_mean is not None:
        paragraphs.append(
            f"The mean EF@Π among defined runs was **{float(ef_mean):.4f}** "
            f"(headline estimand: exclude-invalid denominator)."
        )
    if gap_mean is not None:
        paragraphs.append(
            f"The mean EF sensitivity gap (exclude-invalid minus invalid-as-fail) "
            f"was **{float(gap_mean):.6f}**; report the dual EF band when this gap "
            f"exceeds 0.05 on golden arms."
        )
    if invalid_rate_mean is not None:
        paragraphs.append(
            f"The mean invalid-π rate per instance was **{float(invalid_rate_mean):.6f}**. "
            f"INVALID outcomes denote non-measurement and are excluded from the primary "
            f"EF denominator."
        )
    paragraphs.append(
        "Interpretation is limited to shortcut-sensitive survival under the frozen "
        "MVP perturbation registry Π = {pi_vtest.v1, pi_verif.v1, pi_env.v1} on "
        "fixed golden patch artifacts."
    )
    return "\n\n".join(paragraphs)


def render_phase_a_report(
    *,
    summary_rows: list[dict[str, str]],
    failure_rows: list[dict[str, str]],
    statistics: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    """Render deterministic markdown for a completed Phase A directory."""
    counts = _aggregate_counts(summary_rows)
    run_id = str(manifest.get("run_id") or "unknown")
    ef_mean = statistics.get("ef_mean")
    gap_mean = statistics.get("ef_sensitivity_gap_mean")
    gap_max = statistics.get("ef_sensitivity_gap_max")
    invalid_total = statistics.get("invalid_pi_total")
    invalid_rate_mean = statistics.get("invalid_pi_rate_mean")

    sections: list[str] = [
        "# Phase A Golden Validation Report",
        "",
        f"**Run ID:** `{run_id}`  ",
        "**Instrument:** EarnBench Measurement Protocol v1  ",
        "**Report artifact:** `phase_a_report.md` (deterministic; regenerated from batch CSV/JSON inputs)",
        "",
        "## Executive summary",
        "",
        _publication_summary(counts=counts, statistics=statistics, manifest=manifest),
        "",
        "## Batch summary",
        "",
        _markdown_table(
            ("Metric", "Value"),
            [
                ("Instances graded", str(counts["instance_count"])),
                ("Retained (EF@Π = 1)", str(counts["retained_count"])),
                ("Excluded", str(counts["excluded_count"])),
                ("False unearned", str(counts["false_unearned_count"])),
                ("EF defined", str(counts["ef_defined_count"])),
                ("EF undefined", str(counts["ef_undefined_count"])),
                ("Mean EF@Π (defined)", _format_float(float(ef_mean)) if ef_mean is not None else "—"),
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
                ("Batch failures logged", str(len(failure_rows))),
            ],
        ),
        "",
        "## EF@Π histogram",
        "",
        "Distribution of headline EF@Π (`ef_pi`) across instances. "
        "Undefined runs are grouped separately.",
        "",
    ]

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
            "## Invalid-π histogram",
            "",
            "Count of instances by number of perturbation outcomes marked INVALID "
            "or ERROR (non-measurement).",
            "",
        ]
    )
    invalid_hist = _invalid_histogram_rows(summary_rows)
    if invalid_hist:
        sections.extend(
            [
                _markdown_table(("Invalid π count", "Instances", "Bar"), invalid_hist),
                "",
            ]
        )
    else:
        sections.extend(["_No summary rows available._", ""])

    sections.extend(
        [
            "## EF sensitivity gap",
            "",
            "Primary EF@Π uses exclude-invalid accounting. "
            "`ef_sensitivity_gap = ef_exclude_invalid − ef_invalid_as_fail`. "
            "Instances with gap > 0 appear below (sorted by gap descending).",
            "",
        ]
    )
    gap_rows = _sensitivity_gap_rows(summary_rows)
    if gap_rows:
        sections.extend(
            [
                _markdown_table(
                    (
                        "Instance",
                        "Repo",
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
        sections.extend(["No instances with sensitivity gap > 0.", ""])

    sections.extend(["## Retained instances", ""])
    retained_rows = _retained_rows(summary_rows)
    if retained_rows:
        sections.extend(
            [
                _markdown_table(RETAINED_COLUMNS, retained_rows),
                "",
            ]
        )
    else:
        sections.extend(["_No retained instances._", ""])

    sections.extend(["## Excluded instances", ""])
    excluded_rows = _excluded_rows(summary_rows)
    if excluded_rows:
        sections.extend(
            [
                _markdown_table(EXCLUDED_COLUMNS, excluded_rows),
                "",
            ]
        )
    else:
        sections.extend(["_No excluded instances._", ""])

    sections.extend(
        [
            "## Top batch failures",
            "",
            "Worker or stage failures recorded in `failures.csv` "
            "(sorted lexicographically; first 20 shown).",
            "",
        ]
    )
    failure_display = _top_failure_rows(failure_rows)
    if failure_display:
        sections.extend(
            [
                _markdown_table(FAILURE_DISPLAY_COLUMNS, failure_display),
                "",
            ]
        )
    else:
        sections.extend(["No batch failures recorded.", ""])

    sections.extend(
        [
            "## Instance summary table",
            "",
            "Full per-instance view derived from `summary.csv`.",
            "",
            _markdown_table(SUMMARY_DISPLAY_COLUMNS, _summary_table_rows(summary_rows)),
            "",
            "## Interpretation notes",
            "",
            "- **Retained** instances satisfy nominal success and EF@Π = 1.0000 under the primary estimand.",
            "- **False unearned** marks golden patches with defined EF@Π < 1.0000.",
            "- **INVALID** π outcomes are missing data, not evidence of low earnedness.",
            "- Report EF | pass jointly with pass rate when comparing agent arms; EF alone is conditional.",
            "",
        ]
    )
    return "\n".join(sections)


def validate_phase_a_directory(output_dir: Path) -> None:
    """Raise FileNotFoundError or ValueError when inputs are incomplete."""
    if not output_dir.is_dir():
        msg = f"Phase A output directory not found: {output_dir}"
        raise FileNotFoundError(msg)
    summary_path = output_dir / SUMMARY_CSV
    if not summary_path.is_file():
        msg = f"missing required artifact: {summary_path}"
        raise FileNotFoundError(msg)


def generate_phase_a_report(output_dir: Path) -> PhaseAReportResult:
    """Write phase_a_report.md from completed Phase A batch artifacts."""
    output_dir = output_dir.resolve()
    validate_phase_a_directory(output_dir)

    summary_rows = _load_csv_rows(output_dir / SUMMARY_CSV)
    failure_rows = _load_csv_rows(output_dir / FAILURES_CSV)
    statistics = _load_json_object(output_dir / STATISTICS_JSON)
    manifest = _load_json_object(output_dir / RUN_MANIFEST_JSON)

    if not statistics and summary_rows:
        from earnbench.phase_a_batch import build_statistics

        summary_map = {
            str(row["instance_id"]): row
            for row in summary_rows
            if row.get("instance_id")
        }
        statistics = build_statistics(summary_map)
        # Drop non-deterministic field for report rendering consistency.
        statistics.pop("generated_at_utc", None)

    body = render_phase_a_report(
        summary_rows=summary_rows,
        failure_rows=failure_rows,
        statistics=statistics,
        manifest=manifest,
    )
    report_path = output_dir / PHASE_A_REPORT_MD
    report_path.write_text(body, encoding="utf-8")
    return PhaseAReportResult(output_dir=output_dir, report_path=report_path)


__all__ = [
    "PHASE_A_REPORT_MD",
    "PhaseAReportResult",
    "generate_phase_a_report",
    "render_phase_a_report",
    "validate_phase_a_directory",
]
