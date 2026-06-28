"""Post-hoc agreement analysis between EarnBench EF outcomes and external audit labels."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.bootstrap_uncertainty import load_phase_summary_rows
from earnbench.injection_validity import FALSE_EARNED_THRESHOLD
from earnbench.reports import EarnedFractionStatus

SUMMARY_JSON = "external_label_agreement_summary.json"
BY_SOURCE_CSV = "external_label_agreement_by_source.csv"
BY_LABEL_CSV = "external_label_agreement_by_label.csv"
CONFUSION_CSV = "external_label_agreement_confusion.csv"
DISAGREEMENTS_CSV = "external_label_agreement_disagreements.csv"
REPORT_MD = "external_label_agreement_report.md"

DEFAULT_EF_THRESHOLD = FALSE_EARNED_THRESHOLD

REQUIRED_LABEL_COLUMNS = (
    "instance_id",
    "source",
    "label_name",
    "label_value",
)

BY_SOURCE_COLUMNS = (
    "source",
    "label_row_count",
    "label_instance_count",
    "overlap_instance_count",
    "summary_coverage_rate",
    "source_match_rate",
    "decidable_case_count",
    "concordant_case_count",
    "concordance_rate",
    "disagreement_count",
)

BY_LABEL_COLUMNS = (
    "source",
    "label_name",
    "label_category",
    "label_row_count",
    "overlap_instance_count",
    "ef_mean",
    "low_ef_rate",
    "false_unearned_rate",
    "retained_rate",
    "invalid_pi_rate_mean",
    "failed_mechanism_row_count",
)

CONFUSION_COLUMNS = (
    "ef_band",
    "external_polarity",
    "agreement_cell",
    "count",
)

DISAGREEMENT_COLUMNS = (
    "instance_id",
    "source",
    "label_name",
    "label_category",
    "label_value",
    "ef_pi",
    "ef_status",
    "ef_band",
    "external_polarity",
    "disagreement_type",
    "failed_mechanisms",
    "notes",
    "citation_key",
    "url",
)

WEAK_TEST_LABELS = frozenset(
    {"weak_test", "weak_tests", "test_weakness", "harness_false_pass"}
)
OVERFIT_LABELS = frozenset({"overfit", "overfitting", "gold_overfit", "abs_reject"})
DESIGN_VIOLATION_LABELS = frozenset(
    {"design_violation", "design_constraint_violation", "constraint_violation"}
)
OTHER_CONCERN_LABELS = frozenset(
    {
        "patch_incorrect",
        "behavioral_divergence",
        "dev_test_fail",
        "solution_leakage",
        "oracle_mismatch",
        "validity_concern",
        "benchmark_inflation",
        "retrieval_exploit",
        "runtime_leakage",
        "cursor_audit_flag",
    }
)
CLEAN_LABEL_NAMES = frozenset(
    {"clean", "no_issue", "oracle_match", "passes_dev_tests", "consistent"}
)
POSITIVE_VALUES = frozenset(
    {
        "1",
        "true",
        "yes",
        "y",
        "flag",
        "flagged",
        "positive",
        "concern",
        "fail",
        "failed",
        "rejected",
        "violation",
        "weak",
        "overfit",
        "divergent",
    }
)
NEGATIVE_VALUES = frozenset(
    {"0", "false", "no", "n", "clean", "pass", "passed", "accepted", "consistent", "none"}
)

CONFUSION_CELLS = (
    ("low", "flagged", "ef_low_vs_external_flagged"),
    ("high", "clean", "ef_high_vs_external_clean"),
    ("low", "clean", "ef_low_vs_external_clean"),
    ("high", "flagged", "ef_high_vs_external_flagged"),
)


@dataclass(frozen=True, slots=True)
class ExternalLabelValidationResult:
    path: Path
    row_count: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class ExternalLabelAgreementResult:
    output_dir: Path
    summary_json: Path
    by_source_csv: Path
    by_label_csv: Path
    confusion_csv: Path
    disagreements_csv: Path
    report_md: Path


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _optional_float(value: object) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _format_float(value: float | None, *, precision: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:.{precision}f}"


def _normalize_label_name(label_name: str) -> str:
    return label_name.strip().lower().replace("-", "_").replace(" ", "_")


def _parse_external_polarity(label_name: str, label_value: object) -> str:
    """Return external_polarity: flagged, clean, or unknown."""
    normalized_name = _normalize_label_name(label_name)
    if normalized_name in CLEAN_LABEL_NAMES:
        return "clean"
    text = str(label_value).strip().lower()
    if text in POSITIVE_VALUES:
        return "flagged"
    if text in NEGATIVE_VALUES:
        return "clean"
    return "unknown"


def _label_category(label_name: str) -> str:
    normalized = _normalize_label_name(label_name)
    if normalized in CLEAN_LABEL_NAMES:
        return "clean"
    if normalized in WEAK_TEST_LABELS:
        return "weak_test"
    if normalized in OVERFIT_LABELS:
        return "overfit"
    if normalized in DESIGN_VIOLATION_LABELS:
        return "design_violation"
    if normalized in OTHER_CONCERN_LABELS:
        return "other_concern"
    return "other_concern"


def _parse_failed_mechanisms(summary_row: dict[str, Any]) -> tuple[str, ...]:
    raw = summary_row.get("failed_mechanisms")
    if raw in (None, ""):
        return ()
    return tuple(part.strip() for part in str(raw).split(";") if part.strip())


def _classify_ef_band(
    *,
    y0: bool,
    ef_pi: float | None,
    ef_status: str,
    false_unearned: bool,
    ef_threshold: float,
) -> str:
    if not y0:
        return "undefined"
    if ef_status != EarnedFractionStatus.DEFINED.value or ef_pi is None:
        return "undefined"
    if false_unearned or ef_pi < ef_threshold:
        return "low"
    return "high"


def _is_low_ef(
    *,
    y0: bool,
    ef_pi: float | None,
    ef_status: str,
    false_unearned: bool,
    ef_threshold: float,
) -> bool:
    return (
        _classify_ef_band(
            y0=y0,
            ef_pi=ef_pi,
            ef_status=ef_status,
            false_unearned=false_unearned,
            ef_threshold=ef_threshold,
        )
        == "low"
    )


def _confusion_cell_name(ef_band: str, external_polarity: str) -> str:
    if ef_band == "undefined" or external_polarity == "unknown":
        return "non_decidable"
    mapping = {
        ("low", "flagged"): "ef_low_vs_external_flagged",
        ("high", "clean"): "ef_high_vs_external_clean",
        ("low", "clean"): "ef_low_vs_external_clean",
        ("high", "flagged"): "ef_high_vs_external_flagged",
    }
    return mapping.get((ef_band, external_polarity), "other")


def _disagreement_type(agreement_cell: str) -> str:
    if agreement_cell == "ef_high_vs_external_flagged":
        return "external_flagged_ef_high"
    if agreement_cell == "ef_low_vs_external_clean":
        return "ef_low_external_clean"
    return ""


def validate_external_labels_table(path: Path) -> ExternalLabelValidationResult:
    resolved = path.resolve()
    if not resolved.is_file():
        return ExternalLabelValidationResult(
            path=resolved,
            row_count=0,
            errors=(f"labels file not found: {resolved}",),
        )

    errors: list[str] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return ExternalLabelValidationResult(
                path=resolved,
                row_count=0,
                errors=(f"{resolved}: empty file or missing header row",),
            )

        header = [name.strip() for name in reader.fieldnames if name is not None]
        missing = [column for column in REQUIRED_LABEL_COLUMNS if column not in header]
        if missing:
            errors.append(
                f"{resolved}: missing required columns: {', '.join(missing)}"
            )

        row_count = 0
        for line_number, raw in enumerate(reader, start=2):
            row_count += 1
            prefix = f"{resolved}:{line_number}"
            if not str(raw.get("instance_id", "")).strip():
                errors.append(f"{prefix}: instance_id must be non-empty")
            if not str(raw.get("source", "")).strip():
                errors.append(f"{prefix}: source must be non-empty")
            if not str(raw.get("label_name", "")).strip():
                errors.append(f"{prefix}: label_name must be non-empty")
            if str(raw.get("label_value", "")).strip() == "":
                errors.append(f"{prefix}: label_value must be non-empty")

            confidence = raw.get("label_confidence")
            if confidence not in (None, "") and _optional_float(confidence) is None:
                errors.append(f"{prefix}: label_confidence must be numeric when present")

    return ExternalLabelValidationResult(
        path=resolved,
        row_count=row_count,
        errors=tuple(errors),
    )


def load_external_label_rows(path: Path) -> list[dict[str, str]]:
    validation = validate_external_labels_table(path)
    if not validation.ok:
        raise ValueError("; ".join(validation.errors))
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _summary_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["instance_id"]).strip(): row for row in rows}


def _label_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ef_values: list[float] = []
    invalid_rates: list[float] = []
    low_ef_count = 0
    false_unearned_count = 0
    retained_count = 0
    failed_mechanism_count = 0

    for row in rows:
        if row.get("ef_pi") not in ("", None):
            ef_values.append(float(row["ef_pi"]))
        if row.get("invalid_pi_rate") not in ("", None):
            invalid_rates.append(float(row["invalid_pi_rate"]))
        if row.get("low_ef") is True:
            low_ef_count += 1
        if _as_bool(row.get("false_unearned")):
            false_unearned_count += 1
        if _as_bool(row.get("retained")):
            retained_count += 1
        if row.get("failed_mechanisms"):
            failed_mechanism_count += 1

    total = len(rows)
    return {
        "ef_mean": _mean(ef_values),
        "low_ef_rate": _rate(low_ef_count, total),
        "false_unearned_rate": _rate(false_unearned_count, total),
        "retained_rate": _rate(retained_count, total),
        "invalid_pi_rate_mean": _mean(invalid_rates),
        "failed_mechanism_row_count": failed_mechanism_count,
    }


def analyze_external_label_agreement(
    summary_rows: list[dict[str, Any]],
    label_rows: list[dict[str, str]],
    *,
    ef_threshold: float = DEFAULT_EF_THRESHOLD,
) -> dict[str, Any]:
    """Join summary EF outcomes with external audit labels for agreement analysis."""
    if ef_threshold <= 0.0 or ef_threshold > 1.0:
        msg = f"ef_threshold must be in (0, 1], got {ef_threshold}"
        raise ValueError(msg)

    summary_by_id = _summary_index(summary_rows)
    summary_ids = set(summary_by_id)
    summary_count = len(summary_rows)

    label_ids = {str(row["instance_id"]).strip() for row in label_rows}
    overlap_ids = sorted(summary_ids & label_ids)

    matched_cases: list[dict[str, Any]] = []
    disagreement_rows: list[dict[str, Any]] = []

    for label_row in label_rows:
        instance_id = str(label_row.get("instance_id", "")).strip()
        source = str(label_row.get("source", "")).strip()
        label_name = str(label_row.get("label_name", "")).strip()
        label_value = str(label_row.get("label_value", "")).strip()
        label_category = _label_category(label_name)
        external_polarity = _parse_external_polarity(label_name, label_value)
        summary_row = summary_by_id.get(instance_id)

        if summary_row is None:
            continue

        y0 = _as_bool(summary_row.get("y0"))
        ef_pi = _optional_float(summary_row.get("ef_pi"))
        ef_status = str(summary_row.get("ef_status", "")).strip()
        false_unearned = _as_bool(summary_row.get("false_unearned"))
        retained = _as_bool(summary_row.get("retained"))
        invalid_pi_rate = _optional_float(summary_row.get("invalid_pi_rate"))
        failed_mechanisms = _parse_failed_mechanisms(summary_row)
        ef_band = _classify_ef_band(
            y0=y0,
            ef_pi=ef_pi,
            ef_status=ef_status,
            false_unearned=false_unearned,
            ef_threshold=ef_threshold,
        )
        low_ef = _is_low_ef(
            y0=y0,
            ef_pi=ef_pi,
            ef_status=ef_status,
            false_unearned=false_unearned,
            ef_threshold=ef_threshold,
        )
        agreement_cell = _confusion_cell_name(ef_band, external_polarity)
        disagreement_type = _disagreement_type(agreement_cell)

        case = {
            "instance_id": instance_id,
            "source": source,
            "label_name": label_name,
            "label_value": label_value,
            "label_category": label_category,
            "external_polarity": external_polarity,
            "label_confidence": label_row.get("label_confidence", ""),
            "notes": label_row.get("notes", ""),
            "citation_key": label_row.get("citation_key", ""),
            "url": label_row.get("url", ""),
            "y0": y0,
            "ef_pi": ef_pi,
            "ef_status": ef_status,
            "false_unearned": false_unearned,
            "retained": retained,
            "invalid_pi_rate": invalid_pi_rate,
            "failed_mechanisms": ";".join(failed_mechanisms),
            "ef_band": ef_band,
            "low_ef": low_ef,
            "agreement_cell": agreement_cell,
            "disagreement_type": disagreement_type,
        }
        matched_cases.append(case)

        if disagreement_type:
            disagreement_rows.append(
                {
                    "instance_id": instance_id,
                    "source": source,
                    "label_name": label_name,
                    "label_category": label_category,
                    "label_value": label_value,
                    "ef_pi": ef_pi if ef_pi is not None else "",
                    "ef_status": ef_status,
                    "ef_band": ef_band,
                    "external_polarity": external_polarity,
                    "disagreement_type": disagreement_type,
                    "failed_mechanisms": ";".join(failed_mechanisms),
                    "notes": label_row.get("notes", ""),
                    "citation_key": label_row.get("citation_key", ""),
                    "url": label_row.get("url", ""),
                }
            )

    by_source_rows: list[dict[str, Any]] = []
    source_label_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in label_rows:
        source_label_rows[str(row.get("source", "")).strip() or "unknown"].append(row)

    for source, rows in sorted(source_label_rows.items()):
        source_instance_ids = {str(row["instance_id"]).strip() for row in rows}
        overlap_for_source = sorted(summary_ids & source_instance_ids)
        source_matched = [case for case in matched_cases if case["source"] == source]
        decidable = [
            case
            for case in source_matched
            if case["agreement_cell"]
            in {
                "ef_low_vs_external_flagged",
                "ef_high_vs_external_clean",
                "ef_low_vs_external_clean",
                "ef_high_vs_external_flagged",
            }
        ]
        concordant = [
            case
            for case in decidable
            if case["agreement_cell"]
            in {"ef_low_vs_external_flagged", "ef_high_vs_external_clean"}
        ]
        by_source_rows.append(
            {
                "source": source,
                "label_row_count": len(rows),
                "label_instance_count": len(source_instance_ids),
                "overlap_instance_count": len(overlap_for_source),
                "summary_coverage_rate": _rate(len(overlap_for_source), summary_count),
                "source_match_rate": _rate(len(overlap_for_source), len(source_instance_ids)),
                "decidable_case_count": len(decidable),
                "concordant_case_count": len(concordant),
                "concordance_rate": _rate(len(concordant), len(decidable)),
                "disagreement_count": sum(
                    1
                    for case in source_matched
                    if case["disagreement_type"]
                    in {"external_flagged_ef_high", "ef_low_external_clean"}
                ),
            }
        )

    by_label_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for case in matched_cases:
        by_label_key[
            (str(case["source"]), str(case["label_name"]), str(case["label_category"]))
        ].append(case)

    by_label_rows: list[dict[str, Any]] = []
    for (source, label_name, label_category), rows in sorted(by_label_key.items()):
        metrics = _label_metrics(rows)
        by_label_rows.append(
            {
                "source": source,
                "label_name": label_name,
                "label_category": label_category,
                "label_row_count": len(rows),
                "overlap_instance_count": len({row["instance_id"] for row in rows}),
                **metrics,
            }
        )

    confusion_counter: Counter[tuple[str, str, str]] = Counter()
    for case in matched_cases:
        if case["external_polarity"] == "unknown":
            continue
        confusion_counter[
            (
                str(case["ef_band"]),
                str(case["external_polarity"]),
                str(case["agreement_cell"]),
            )
        ] += 1

    confusion_rows = [
        {
            "ef_band": ef_band,
            "external_polarity": external_polarity,
            "agreement_cell": agreement_cell,
            "count": count,
        }
        for (ef_band, external_polarity, agreement_cell), count in sorted(
            confusion_counter.items()
        )
    ]

    for ef_band, external_polarity, agreement_cell in CONFUSION_CELLS:
        if not any(
            row["ef_band"] == ef_band
            and row["external_polarity"] == external_polarity
            and row["agreement_cell"] == agreement_cell
            for row in confusion_rows
        ):
            confusion_rows.append(
                {
                    "ef_band": ef_band,
                    "external_polarity": external_polarity,
                    "agreement_cell": agreement_cell,
                    "count": 0,
                }
            )
    confusion_rows.sort(key=lambda row: (row["ef_band"], row["external_polarity"]))

    decidable_cases = [
        case
        for case in matched_cases
        if case["agreement_cell"]
        in {
            "ef_low_vs_external_flagged",
            "ef_high_vs_external_clean",
            "ef_low_vs_external_clean",
            "ef_high_vs_external_flagged",
        }
    ]
    concordant_cases = [
        case
        for case in decidable_cases
        if case["agreement_cell"]
        in {"ef_low_vs_external_flagged", "ef_high_vs_external_clean"}
    ]

    return {
        "schema_version": "earnbench.external_label_agreement.v2",
        "ef_threshold": ef_threshold,
        "summary_instance_count": summary_count,
        "label_row_count": len(label_rows),
        "label_instance_count": len(label_ids),
        "overlap_instance_count": len(overlap_ids),
        "overlap_instance_ids": overlap_ids,
        "matched_label_row_count": len(matched_cases),
        "undefined_ef_case_count": sum(
            1 for case in matched_cases if case["ef_band"] == "undefined"
        ),
        "concordance_rate": _rate(len(concordant_cases), len(decidable_cases)),
        "decidable_case_count": len(decidable_cases),
        "concordant_case_count": len(concordant_cases),
        "disagreement_count": len(disagreement_rows),
        "by_source_rows": by_source_rows,
        "by_label_rows": by_label_rows,
        "confusion_rows": confusion_rows,
        "disagreement_rows": disagreement_rows,
    }


def render_external_label_agreement_report(payload: dict[str, Any]) -> str:
    lines = [
        "# External Label Agreement Report",
        "",
        "Post-hoc agreement analysis crossing **frozen EarnBench summary rows** with "
        "**externally sourced instance labels** (PatchDiff, SWE-bench+, SWE-ABS, "
        "SWE-Shield, Cursor-style audits, or compatible sources). Does **not** modify "
        "EF@Π, Π, INVALID semantics, or frozen results.",
        "",
        "## Overlap",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        (f"| Summary instances | {payload['summary_instance_count']} |"),
        (f"| Label rows | {payload['label_row_count']} |"),
        (f"| Distinct labeled instances | {payload['label_instance_count']} |"),
        (f"| Overlap instances | {payload['overlap_instance_count']} |"),
        (f"| Matched label rows | {payload['matched_label_row_count']} |"),
        (f"| EF undefined (excluded from low/high) | {payload['undefined_ef_case_count']} |"),
        "",
        "## Concordance",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        (f"| Low-EF threshold τ | {payload['ef_threshold']} |"),
        (f"| Decidable cases | {payload['decidable_case_count']} |"),
        (f"| Concordant cases | {payload['concordant_case_count']} |"),
        (f"| Concordance rate | {_format_float(payload['concordance_rate'])} |"),
        (f"| Disagreement cases | {payload['disagreement_count']} |"),
        "",
        "## Overlap and coverage by source",
        "",
        "| Source | Label rows | Label instances | Overlap | Summary coverage | Source match | "
        "Concordance | Disagreements |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in payload["by_source_rows"]:
        lines.append(
            f"| {row['source']} | {row['label_row_count']} | {row['label_instance_count']} | "
            f"{row['overlap_instance_count']} | "
            f"{_format_float(row['summary_coverage_rate'])} | "
            f"{_format_float(row['source_match_rate'])} | "
            f"{_format_float(row['concordance_rate'])} | {row['disagreement_count']} |"
        )
    if not payload["by_source_rows"]:
        lines.append("| _none_ | 0 | 0 | 0 | — | — | — | 0 |")

    lines.extend(
        [
            "",
            "## Metrics by external label",
            "",
            "| Source | Label | Category | Rows | EF mean | Low-EF | False-unearned | "
            "Retained | Invalid-π mean | Failed-mech rows |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["by_label_rows"]:
        lines.append(
            f"| {row['source']} | {row['label_name']} | {row['label_category']} | "
            f"{row['label_row_count']} | {_format_float(row['ef_mean'])} | "
            f"{_format_float(row['low_ef_rate'])} | "
            f"{_format_float(row['false_unearned_rate'])} | "
            f"{_format_float(row['retained_rate'])} | "
            f"{_format_float(row['invalid_pi_rate_mean'])} | "
            f"{row['failed_mechanism_row_count']} |"
        )
    if not payload["by_label_rows"]:
        lines.append("| _none_ | — | — | 0 | — | — | — | — | — | 0 |")

    lines.extend(
        [
            "",
            "## Confusion table (EF band × external polarity)",
            "",
            "| EF band | External | Cell | Count |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for row in payload["confusion_rows"]:
        lines.append(
            f"| {row['ef_band']} | {row['external_polarity']} | "
            f"{row['agreement_cell']} | {row['count']} |"
        )

    lines.extend(["", "## Disagreement cases", ""])
    if payload["disagreement_rows"]:
        for row in payload["disagreement_rows"]:
            lines.append(
                f"- **`{row['instance_id']}`** ({row['disagreement_type']}, "
                f"{row['source']}/{row['label_name']}): EF={_format_float(row['ef_pi'])} "
                f"({row['ef_status']}) vs `{row['label_value']}`"
            )
    else:
        lines.append("_None among matched rows._")

    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- External labels are **convergent contextual evidence**, not validation of "
            "EarnBench operating characteristics.",
            "- Partial label coverage is expected; always report overlap denominators.",
            "- EF **undefined** rows are excluded from low/high bands (not treated as zero).",
            "- `external_flagged_ef_high` = external flagged label but EF ≥ τ on Y₀=1.",
            "- `ef_low_external_clean` = EF < τ but external label reads clean.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_csv(
    path: Path,
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            serialized = {
                key: (
                    ""
                    if value is None
                    else value
                    if isinstance(value, (int, float, str))
                    else str(value)
                )
                for key, value in row.items()
            }
            writer.writerow(serialized)


def generate_external_label_agreement_report(
    summary_path: Path,
    labels_path: Path,
    output_dir: Path,
    *,
    ef_threshold: float = DEFAULT_EF_THRESHOLD,
) -> ExternalLabelAgreementResult:
    """Load inputs, analyze agreement, and write summary CSV/JSON/Markdown artifacts."""
    summary_rows = load_phase_summary_rows(summary_path)
    label_rows = load_external_label_rows(labels_path)
    payload = analyze_external_label_agreement(
        summary_rows,
        label_rows,
        ef_threshold=ef_threshold,
    )

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_json_path = output_dir / SUMMARY_JSON
    by_source_csv_path = output_dir / BY_SOURCE_CSV
    by_label_csv_path = output_dir / BY_LABEL_CSV
    confusion_csv_path = output_dir / CONFUSION_CSV
    disagreements_csv_path = output_dir / DISAGREEMENTS_CSV
    report_md_path = output_dir / REPORT_MD

    by_source_rows = payload.pop("by_source_rows")
    by_label_rows = payload.pop("by_label_rows")
    confusion_rows = payload.pop("confusion_rows")
    disagreement_rows = payload.pop("disagreement_rows")

    report_payload = {
        **payload,
        "by_source_rows": by_source_rows,
        "by_label_rows": by_label_rows,
        "confusion_rows": confusion_rows,
        "disagreement_rows": disagreement_rows,
    }
    report_md_path.write_text(
        render_external_label_agreement_report(report_payload),
        encoding="utf-8",
    )

    _write_csv(by_source_csv_path, BY_SOURCE_COLUMNS, by_source_rows)
    _write_csv(by_label_csv_path, BY_LABEL_COLUMNS, by_label_rows)
    _write_csv(confusion_csv_path, CONFUSION_COLUMNS, confusion_rows)
    _write_csv(disagreements_csv_path, DISAGREEMENT_COLUMNS, disagreement_rows)

    with summary_json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return ExternalLabelAgreementResult(
        output_dir=output_dir,
        summary_json=summary_json_path,
        by_source_csv=by_source_csv_path,
        by_label_csv=by_label_csv_path,
        confusion_csv=confusion_csv_path,
        disagreements_csv=disagreements_csv_path,
        report_md=report_md_path,
    )
