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

EXTERNAL_LABEL_AGREEMENT_JSON = "external_label_agreement.json"
EXTERNAL_LABEL_AGREEMENT_CSV = "external_label_agreement.csv"
EXTERNAL_LABEL_BY_LABEL_CSV = "external_label_by_label.csv"
EXTERNAL_LABEL_AGREEMENT_TABLE_CSV = "external_label_agreement_table.csv"
EXTERNAL_LABEL_DISAGREEMENTS_CSV = "external_label_disagreements.csv"
EXTERNAL_LABEL_AGREEMENT_MD = "external_label_agreement.md"

REQUIRED_LABEL_COLUMNS = (
    "instance_id",
    "source",
    "label_name",
    "label_value",
)

CASE_COLUMNS = (
    "instance_id",
    "source",
    "label_name",
    "label_value",
    "label_category",
    "external_polarity",
    "label_confidence",
    "notes",
    "y0",
    "ef_pi",
    "ef_status",
    "false_unearned",
    "ef_band",
    "low_ef",
    "summary_matched",
    "agreement_cell",
    "disagreement_type",
)

BY_LABEL_COLUMNS = (
    "source",
    "label_name",
    "label_category",
    "label_row_count",
    "overlap_count",
    "ef_mean",
    "low_ef_rate",
    "false_unearned_rate",
)

AGREEMENT_TABLE_COLUMNS = (
    "label_category",
    "ef_band",
    "external_polarity",
    "count",
)

DISAGREEMENT_COLUMNS = (
    "instance_id",
    "source",
    "label_name",
    "label_category",
    "label_value",
    "ef_pi",
    "ef_band",
    "external_polarity",
    "disagreement_type",
    "notes",
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
    agreement_json: Path
    agreement_csv: Path
    by_label_csv: Path
    agreement_table_csv: Path
    disagreements_csv: Path
    agreement_md: Path


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


def _format_float(value: float | None, *, precision: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:.{precision}f}"


def _normalize_label_name(label_name: str) -> str:
    return label_name.strip().lower().replace("-", "_").replace(" ", "_")


def _parse_label_polarity(label_name: str, label_value: object) -> str:
    normalized_name = _normalize_label_name(label_name)
    if normalized_name in CLEAN_LABEL_NAMES:
        return "clean"
    text = str(label_value).strip().lower()
    if text in POSITIVE_VALUES:
        return "concern"
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


def _classify_ef_band(
    *,
    y0: bool,
    ef_pi: float | None,
    ef_status: str,
    false_unearned: bool,
) -> str:
    if not y0:
        return "undefined"
    if ef_status != EarnedFractionStatus.DEFINED.value or ef_pi is None:
        return "undefined"
    if false_unearned or ef_pi < FALSE_EARNED_THRESHOLD:
        return "low"
    return "high"


def _is_low_ef(
    *,
    y0: bool,
    ef_pi: float | None,
    ef_status: str,
    false_unearned: bool,
) -> bool:
    return _classify_ef_band(
        y0=y0,
        ef_pi=ef_pi,
        ef_status=ef_status,
        false_unearned=false_unearned,
    ) == "low"


def _agreement_cell(ef_band: str, external_polarity: str) -> str:
    if ef_band == "undefined" or external_polarity == "unknown":
        return "non_decidable"
    if ef_band == "low" and external_polarity == "concern":
        return "concordant_low_concern"
    if ef_band == "high" and external_polarity == "clean":
        return "concordant_high_clean"
    if ef_band == "high" and external_polarity == "concern":
        return "external_flag_ef_high"
    if ef_band == "low" and external_polarity == "clean":
        return "ef_low_external_clean"
    return "other"


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


def analyze_external_label_agreement(
    summary_rows: list[dict[str, Any]],
    label_rows: list[dict[str, str]],
) -> dict[str, Any]:
    summary_by_id = _summary_index(summary_rows)
    summary_ids = set(summary_by_id)
    label_ids = {str(row["instance_id"]).strip() for row in label_rows}
    overlap_ids = sorted(summary_ids & label_ids)

    case_rows: list[dict[str, Any]] = []
    disagreement_rows: list[dict[str, Any]] = []

    for label_row in label_rows:
        instance_id = str(label_row.get("instance_id", "")).strip()
        source = str(label_row.get("source", "")).strip()
        label_name = str(label_row.get("label_name", "")).strip()
        label_value = str(label_row.get("label_value", "")).strip()
        label_category = _label_category(label_name)
        external_polarity = _parse_label_polarity(label_name, label_value)
        summary_row = summary_by_id.get(instance_id)

        if summary_row is None:
            case_rows.append(
                {
                    "instance_id": instance_id,
                    "source": source,
                    "label_name": label_name,
                    "label_value": label_value,
                    "label_category": label_category,
                    "external_polarity": external_polarity,
                    "label_confidence": label_row.get("label_confidence", ""),
                    "notes": label_row.get("notes", ""),
                    "y0": "",
                    "ef_pi": "",
                    "ef_status": "",
                    "false_unearned": "",
                    "ef_band": "no_summary_match",
                    "low_ef": "",
                    "summary_matched": False,
                    "agreement_cell": "no_summary_match",
                    "disagreement_type": "",
                }
            )
            continue

        y0 = _as_bool(summary_row.get("y0"))
        ef_pi = _optional_float(summary_row.get("ef_pi"))
        ef_status = str(summary_row.get("ef_status", "")).strip()
        false_unearned = _as_bool(summary_row.get("false_unearned"))
        ef_band = _classify_ef_band(
            y0=y0,
            ef_pi=ef_pi,
            ef_status=ef_status,
            false_unearned=false_unearned,
        )
        low_ef = _is_low_ef(
            y0=y0,
            ef_pi=ef_pi,
            ef_status=ef_status,
            false_unearned=false_unearned,
        )
        agreement_cell = _agreement_cell(ef_band, external_polarity)
        disagreement_type = ""
        if agreement_cell == "external_flag_ef_high":
            disagreement_type = "external_flag_ef_high"
        elif agreement_cell == "ef_low_external_clean":
            disagreement_type = "ef_low_external_clean"

        case_rows.append(
            {
                "instance_id": instance_id,
                "source": source,
                "label_name": label_name,
                "label_value": label_value,
                "label_category": label_category,
                "external_polarity": external_polarity,
                "label_confidence": label_row.get("label_confidence", ""),
                "notes": label_row.get("notes", ""),
                "y0": y0,
                "ef_pi": ef_pi,
                "ef_status": ef_status,
                "false_unearned": false_unearned,
                "ef_band": ef_band,
                "low_ef": low_ef,
                "summary_matched": True,
                "agreement_cell": agreement_cell,
                "disagreement_type": disagreement_type,
            }
        )
        if disagreement_type:
            disagreement_rows.append(
                {
                    "instance_id": instance_id,
                    "source": source,
                    "label_name": label_name,
                    "label_category": label_category,
                    "label_value": label_value,
                    "ef_pi": ef_pi if ef_pi is not None else "",
                    "ef_band": ef_band,
                    "external_polarity": external_polarity,
                    "disagreement_type": disagreement_type,
                    "notes": label_row.get("notes", ""),
                }
            )

    matched_cases = [row for row in case_rows if row["summary_matched"]]
    by_label_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in matched_cases:
        by_label_key[
            (str(row["source"]), str(row["label_name"]), str(row["label_category"]))
        ].append(row)

    by_label_rows: list[dict[str, Any]] = []
    for (source, label_name, label_category), rows in sorted(by_label_key.items()):
        ef_values = [float(row["ef_pi"]) for row in rows if row["ef_pi"] not in ("", None)]
        low_ef_count = sum(1 for row in rows if row["low_ef"] is True)
        false_unearned_count = sum(
            1 for row in rows if _as_bool(row.get("false_unearned"))
        )
        by_label_rows.append(
            {
                "source": source,
                "label_name": label_name,
                "label_category": label_category,
                "label_row_count": len(rows),
                "overlap_count": len({row["instance_id"] for row in rows}),
                "ef_mean": (sum(ef_values) / len(ef_values) if ef_values else None),
                "low_ef_rate": _rate(low_ef_count, len(rows)),
                "false_unearned_rate": _rate(false_unearned_count, len(rows)),
            }
        )

    agreement_table_counter: Counter[tuple[str, str, str]] = Counter()
    for row in matched_cases:
        if row["external_polarity"] == "unknown":
            continue
        agreement_table_counter[
            (
                str(row["label_category"]),
                str(row["ef_band"]),
                str(row["external_polarity"]),
            )
        ] += 1

    agreement_table_rows = [
        {
            "label_category": label_category,
            "ef_band": ef_band,
            "external_polarity": external_polarity,
            "count": count,
        }
        for (label_category, ef_band, external_polarity), count in sorted(
            agreement_table_counter.items()
        )
    ]

    agreement_class_counts = Counter(row["agreement_cell"] for row in matched_cases)
    decidable_cases = [
        row
        for row in matched_cases
        if row["agreement_cell"]
        in {
            "concordant_low_concern",
            "concordant_high_clean",
            "external_flag_ef_high",
            "ef_low_external_clean",
        }
    ]
    concordant_cases = [
        row
        for row in decidable_cases
        if row["agreement_cell"] in {"concordant_low_concern", "concordant_high_clean"}
    ]

    by_source: dict[str, dict[str, Any]] = {}
    source_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in matched_cases:
        source_groups[str(row["source"]).strip() or "unknown"].append(row)
    for source, rows in sorted(source_groups.items()):
        source_decidable = [
            row
            for row in rows
            if row["agreement_cell"]
            in {
                "concordant_low_concern",
                "concordant_high_clean",
                "external_flag_ef_high",
                "ef_low_external_clean",
            }
        ]
        source_concordant = [
            row
            for row in source_decidable
            if row["agreement_cell"] in {"concordant_low_concern", "concordant_high_clean"}
        ]
        by_source[source] = {
            "label_row_count": len(rows),
            "overlap_instance_count": len({row["instance_id"] for row in rows}),
            "decidable_count": len(source_decidable),
            "concordant_count": len(source_concordant),
            "concordance_rate": _rate(len(source_concordant), len(source_decidable)),
            "disagreement_count": sum(
                1
                for row in rows
                if row["disagreement_type"] in {"external_flag_ef_high", "ef_low_external_clean"}
            ),
        }

    return {
        "schema_version": "earnbench.external_label_agreement.v1",
        "summary_instance_count": len(summary_rows),
        "label_row_count": len(label_rows),
        "label_instance_count": len(label_ids),
        "overlap_instance_count": len(overlap_ids),
        "overlap_instance_ids": overlap_ids,
        "matched_label_row_count": len(matched_cases),
        "low_ef_threshold": FALSE_EARNED_THRESHOLD,
        "concordance_rate": _rate(len(concordant_cases), len(decidable_cases)),
        "decidable_case_count": len(decidable_cases),
        "concordant_case_count": len(concordant_cases),
        "agreement_class_counts": dict(agreement_class_counts),
        "disagreement_count": len(disagreement_rows),
        "by_source": by_source,
        "by_label_rows": by_label_rows,
        "agreement_table_rows": agreement_table_rows,
        "disagreement_rows": disagreement_rows,
        "case_rows": case_rows,
    }


def render_external_label_agreement_report(payload: dict[str, Any]) -> str:
    lines = [
        "# External Label Agreement — EF@Π vs SWE-bench Validity Audits",
        "",
        "## Purpose",
        "",
        "Post-hoc agreement analysis crossing **frozen EarnBench summary rows** with "
        "**externally sourced instance labels** (PatchDiff, SWE-bench+, SWE-ABS, "
        "SWE-Shield, or similar audits). This scaffold does **not** modify EF@Π, Π, "
        "INVALID semantics, or frozen Phase A/B results.",
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
        "",
        "## Concordance summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        (f"| Low-EF threshold τ | {payload['low_ef_threshold']} |"),
        (f"| Decidable cases | {payload['decidable_case_count']} |"),
        (f"| Concordant cases | {payload['concordant_case_count']} |"),
        (f"| Concordance rate | {_format_float(payload['concordance_rate'])} |"),
        (f"| Disagreement cases | {payload['disagreement_count']} |"),
        "",
        "## EF mean and low-EF rates by external label",
        "",
        "| Source | Label | Category | Rows | Overlap | EF mean | Low-EF rate | "
        "False-unearned rate |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["by_label_rows"]:
        lines.append(
            f"| {row['source']} | {row['label_name']} | {row['label_category']} | "
            f"{row['label_row_count']} | {row['overlap_count']} | "
            f"{_format_float(row['ef_mean'])} | {_format_float(row['low_ef_rate'])} | "
            f"{_format_float(row['false_unearned_rate'])} |"
        )
    if not payload["by_label_rows"]:
        lines.append("| _none_ | — | — | 0 | 0 | — | — | — |")

    lines.extend(
        [
            "",
            "## Agreement table (EF band × external polarity)",
            "",
            "| Label category | EF band | External polarity | Count |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for row in payload["agreement_table_rows"]:
        lines.append(
            f"| {row['label_category']} | {row['ef_band']} | "
            f"{row['external_polarity']} | {row['count']} |"
        )
    if not payload["agreement_table_rows"]:
        lines.append("| _none_ | — | — | 0 |")

    lines.extend(["", "## Disagreement cases", ""])
    if payload["disagreement_rows"]:
        for row in payload["disagreement_rows"]:
            lines.append(
                f"- **`{row['instance_id']}`** ({row['disagreement_type']}, "
                f"{row['source']}/{row['label_name']}): EF={_format_float(row['ef_pi'])} "
                f"vs external `{row['label_value']}`"
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
            "- Partial label coverage is expected; overlap footer must be reported.",
            "- `external_flag_ef_high` = external concern label but EF≥τ on Y₀=1.",
            "- `ef_low_external_clean` = EF<τ but external label reads clean.",
            "",
            "## Artifacts",
            "",
            "- `external_label_agreement.csv` — case-level join",
            "- `external_label_by_label.csv` — aggregates by source/label",
            "- `external_label_agreement_table.csv` — EF band × external polarity counts",
            "- `external_label_disagreements.csv` — flagged disagreement rows only",
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
) -> ExternalLabelAgreementResult:
    summary_rows = load_phase_summary_rows(summary_path)
    label_rows = load_external_label_rows(labels_path)
    payload = analyze_external_label_agreement(summary_rows, label_rows)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    agreement_csv_path = output_dir / EXTERNAL_LABEL_AGREEMENT_CSV
    by_label_csv_path = output_dir / EXTERNAL_LABEL_BY_LABEL_CSV
    agreement_table_csv_path = output_dir / EXTERNAL_LABEL_AGREEMENT_TABLE_CSV
    disagreements_csv_path = output_dir / EXTERNAL_LABEL_DISAGREEMENTS_CSV
    agreement_json_path = output_dir / EXTERNAL_LABEL_AGREEMENT_JSON
    agreement_md_path = output_dir / EXTERNAL_LABEL_AGREEMENT_MD

    case_rows = payload.pop("case_rows")
    by_label_rows = payload.pop("by_label_rows")
    agreement_table_rows = payload.pop("agreement_table_rows")
    disagreement_rows = payload.pop("disagreement_rows")

    report_payload = {
        **payload,
        "by_label_rows": by_label_rows,
        "agreement_table_rows": agreement_table_rows,
        "disagreement_rows": disagreement_rows,
    }
    agreement_md_path.write_text(
        render_external_label_agreement_report(report_payload),
        encoding="utf-8",
    )

    _write_csv(agreement_csv_path, CASE_COLUMNS, case_rows)
    _write_csv(by_label_csv_path, BY_LABEL_COLUMNS, by_label_rows)
    _write_csv(agreement_table_csv_path, AGREEMENT_TABLE_COLUMNS, agreement_table_rows)
    _write_csv(disagreements_csv_path, DISAGREEMENT_COLUMNS, disagreement_rows)

    with agreement_json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return ExternalLabelAgreementResult(
        output_dir=output_dir,
        agreement_json=agreement_json_path,
        agreement_csv=agreement_csv_path,
        by_label_csv=by_label_csv_path,
        agreement_table_csv=agreement_table_csv_path,
        disagreements_csv=disagreements_csv_path,
        agreement_md=agreement_md_path,
    )
