"""Case-level agreement analysis between external unearned labels and EF outcomes."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.external_unearned.catalog import load_external_unearned_catalog
from earnbench.external_unearned.report import (
    _as_bool,
    _is_detected_unearned,
    _optional_float,
    _parse_failed_mechanisms,
    _rate,
    _write_csv,
    load_external_unearned_results,
)
from earnbench.reports import EarnedFractionStatus

EXTERNAL_UNEARNED_AGREEMENT_CSV = "external_unearned_agreement.csv"
EXTERNAL_UNEARNED_AGREEMENT_JSON = "external_unearned_agreement.json"
EXTERNAL_UNEARNED_AGREEMENT_MD = "external_unearned_agreement.md"

AGREEMENT_CLASSES = (
    "ef_detects",
    "ef_misses_expected",
    "ef_misses_unexpected",
    "ef_undefined",
    "ef_disagrees_with_label",
    "excluded",
)

AGREEMENT_CASE_COLUMNS = (
    "external_id",
    "source",
    "mapped_channel",
    "registry_label",
    "expected_detection",
    "inclusion_decision",
    "external_label",
    "y0",
    "ef_pi",
    "ef_status",
    "failed_mechanisms",
    "detected_unearned",
    "agreement_class",
    "agreement_explanation",
    "results_matched",
)

DECIDABLE_CLASSES = frozenset(
    {
        "ef_detects",
        "ef_misses_expected",
        "ef_misses_unexpected",
        "ef_disagrees_with_label",
    }
)

AGREEING_CLASSES = frozenset({"ef_detects", "ef_misses_expected"})


@dataclass(frozen=True, slots=True)
class ExternalUnearnedAgreementResult:
    output_dir: Path
    agreement_csv: Path
    agreement_json: Path
    agreement_md: Path


def _format_float(value: float | None, *, precision: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:.{precision}f}"


def classify_external_unearned_agreement(
    catalog_row: dict[str, str],
    result_row: dict[str, str] | None,
) -> tuple[str, str, bool, bool]:
    """Return agreement_class, explanation, detected_unearned, results_matched."""
    inclusion = str(catalog_row.get("inclusion_decision", "")).strip()
    if inclusion != "include":
        return (
            "excluded",
            f"inclusion_decision={inclusion!r}; excluded from agreement analysis",
            False,
            result_row is not None,
        )

    external_id = str(catalog_row.get("external_id", "")).strip()
    registry_label = str(catalog_row.get("registry_label", "")).strip().upper()
    expected_detection = str(catalog_row.get("expected_detection", "")).strip()
    mapped_channel = str(catalog_row.get("mapped_channel", "")).strip()
    expected_failed_pi = str(catalog_row.get("expected_failed_pi", "")).strip()

    if result_row is None:
        return (
            "ef_undefined",
            f"no harness results row for external_id={external_id!r}",
            False,
            False,
        )

    y0 = _as_bool(result_row.get("y0"))
    ef_pi = _optional_float(result_row.get("ef_pi"))
    ef_status = str(result_row.get("ef_status", "")).strip()
    failed_mechanisms = _parse_failed_mechanisms(result_row.get("failed_mechanisms"))
    detected = _is_detected_unearned(
        y0=y0,
        ef_pi=ef_pi,
        ef_status=ef_status,
        expected_failed_pi=expected_failed_pi,
        failed_mechanisms=failed_mechanisms,
        mapped_channel=mapped_channel,
    )

    if ef_status != EarnedFractionStatus.DEFINED.value:
        return (
            "ef_undefined",
            f"ef_status={ef_status!r}; EF@Π undefined or invalid for agreement",
            detected,
            True,
        )

    if not y0:
        if expected_detection == "detect":
            return (
                "ef_disagrees_with_label",
                "external label expects Y₀=1 unearned nominal pass but harness reports Y₀=0",
                detected,
                True,
            )
        return (
            "ef_undefined",
            "Y₀=0; external unearned EF agreement not applicable",
            detected,
            True,
        )

    if ef_pi is None:
        return (
            "ef_undefined",
            "ef_pi missing despite ef_status=defined",
            detected,
            True,
        )

    if expected_detection == "unknown" or registry_label == "UNKNOWN":
        return (
            "ef_undefined",
            "registry_label or expected_detection is UNKNOWN; not used in agreement rate",
            detected,
            True,
        )

    if registry_label == "OUT_OF_REGISTRY" and expected_detection == "miss_expected":
        if detected:
            return (
                "ef_disagrees_with_label",
                (
                    "OUT_OF_REGISTRY miss_expected but EF detected unearned "
                    f"(EF={ef_pi:.4f}, failed_mechanisms={','.join(failed_mechanisms) or 'low EF'})"
                ),
                detected,
                True,
            )
        return (
            "ef_misses_expected",
            (
                "OUT_OF_REGISTRY expected false-negative floor: external unearned label "
                f"but EF≈1 (EF={ef_pi:.4f}) as declared for OOR scope"
            ),
            detected,
            True,
        )

    if registry_label == "IN_REGISTRY" and expected_detection == "detect":
        if detected:
            mechanisms = ",".join(failed_mechanisms) or "low EF"
            return (
                "ef_detects",
                (
                    "IN_REGISTRY detection expected and EF flagged unearned "
                    f"(EF={ef_pi:.4f}, signal={mechanisms})"
                ),
                detected,
                True,
            )
        return (
            "ef_misses_unexpected",
            (
                "IN_REGISTRY detection expected but EF did not flag unearned "
                f"(EF={ef_pi:.4f}); instrument miss relative to external label"
            ),
            detected,
            True,
        )

    return (
        "ef_undefined",
        (
            f"unhandled agreement row: registry_label={registry_label}, "
            f"expected_detection={expected_detection}"
        ),
        detected,
        True,
    )


def _group_agreement_rate(rows: list[dict[str, Any]]) -> float | None:
    decidable = [row for row in rows if row["agreement_class"] in DECIDABLE_CLASSES]
    if not decidable:
        return None
    agreeing = sum(1 for row in decidable if row["agreement_class"] in AGREEING_CLASSES)
    return agreeing / len(decidable)


def analyze_external_unearned_agreement(
    catalog_rows: list[dict[str, str]],
    result_rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Join catalog with results and classify EF vs external label agreement."""
    results_by_id = {str(row["external_id"]).strip(): row for row in result_rows}

    case_rows: list[dict[str, Any]] = []
    class_counter: Counter[str] = Counter()

    for catalog_row in catalog_rows:
        external_id = str(catalog_row.get("external_id", "")).strip()
        result_row = results_by_id.get(external_id)
        agreement_class, explanation, detected, matched = classify_external_unearned_agreement(
            catalog_row,
            result_row,
        )
        class_counter[agreement_class] += 1

        y0 = _as_bool(result_row.get("y0")) if result_row is not None else False
        ef_pi = _optional_float(result_row.get("ef_pi")) if result_row is not None else None
        ef_status = str(result_row.get("ef_status", "")).strip() if result_row is not None else ""
        failed_mechanisms = (
            _parse_failed_mechanisms(result_row.get("failed_mechanisms"))
            if result_row is not None
            else ()
        )

        case_rows.append(
            {
                "external_id": external_id,
                "source": catalog_row.get("source", ""),
                "mapped_channel": catalog_row.get("mapped_channel", ""),
                "registry_label": str(catalog_row.get("registry_label", "")).strip().upper(),
                "expected_detection": catalog_row.get("expected_detection", ""),
                "inclusion_decision": catalog_row.get("inclusion_decision", ""),
                "external_label": catalog_row.get("external_label", ""),
                "y0": y0,
                "ef_pi": ef_pi,
                "ef_status": ef_status,
                "failed_mechanisms": ";".join(failed_mechanisms),
                "detected_unearned": detected,
                "agreement_class": agreement_class,
                "agreement_explanation": explanation,
                "results_matched": matched,
            }
        )

    included_rows = [
        row for row in case_rows if row["inclusion_decision"] == "include"
    ]
    decidable_rows = [
        row for row in included_rows if row["agreement_class"] in DECIDABLE_CLASSES
    ]
    agreeing_rows = [
        row for row in decidable_rows if row["agreement_class"] in AGREEING_CLASSES
    ]

    by_source: dict[str, dict[str, Any]] = {}
    source_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in included_rows:
        source_groups[str(row["source"]).strip() or "unknown"].append(row)
    for source, rows in sorted(source_groups.items()):
        source_decidable = [row for row in rows if row["agreement_class"] in DECIDABLE_CLASSES]
        source_agreeing = [
            row for row in source_decidable if row["agreement_class"] in AGREEING_CLASSES
        ]
        by_source[source] = {
            "included_count": len(rows),
            "decidable_count": len(source_decidable),
            "agreeing_count": len(source_agreeing),
            "agreement_rate": _rate(len(source_agreeing), len(source_decidable)),
            "agreement_class_counts": dict(Counter(row["agreement_class"] for row in rows)),
        }

    by_channel: dict[str, dict[str, Any]] = {}
    channel_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in included_rows:
        channel_groups[str(row["mapped_channel"]).strip() or "unknown"].append(row)
    for channel, rows in sorted(channel_groups.items()):
        channel_decidable = [row for row in rows if row["agreement_class"] in DECIDABLE_CLASSES]
        channel_agreeing = [
            row for row in channel_decidable if row["agreement_class"] in AGREEING_CLASSES
        ]
        by_channel[channel] = {
            "included_count": len(rows),
            "decidable_count": len(channel_decidable),
            "agreeing_count": len(channel_agreeing),
            "agreement_rate": _rate(len(channel_agreeing), len(channel_decidable)),
            "agreement_class_counts": dict(Counter(row["agreement_class"] for row in rows)),
        }

    disagreement_cases = [
        {
            "external_id": row["external_id"],
            "agreement_class": row["agreement_class"],
            "agreement_explanation": row["agreement_explanation"],
            "source": row["source"],
            "mapped_channel": row["mapped_channel"],
        }
        for row in included_rows
        if row["agreement_class"] in {"ef_misses_unexpected", "ef_disagrees_with_label"}
    ]

    expected_miss_rows = [
        row
        for row in included_rows
        if row["agreement_class"] == "ef_misses_expected"
    ]
    invalid_undefined_rows = [
        {
            "external_id": row["external_id"],
            "agreement_class": row["agreement_class"],
            "agreement_explanation": row["agreement_explanation"],
            "ef_status": row["ef_status"],
            "y0": row["y0"],
        }
        for row in included_rows
        if row["agreement_class"] == "ef_undefined"
    ]

    return {
        "schema_version": "earnbench.external_unearned_agreement.v1",
        "catalog_row_count": len(catalog_rows),
        "results_row_count": len(result_rows),
        "included_anchor_count": len(included_rows),
        "ef_agreement_rate": _rate(len(agreeing_rows), len(decidable_rows)),
        "agreement_class_counts": dict(class_counter),
        "decidable_case_count": len(decidable_rows),
        "agreeing_case_count": len(agreeing_rows),
        "agreement_by_source": by_source,
        "agreement_by_channel": by_channel,
        "disagreement_cases": disagreement_cases,
        "expected_miss_out_of_registry": {
            "count": len(expected_miss_rows),
            "external_ids": [row["external_id"] for row in expected_miss_rows],
            "rows": expected_miss_rows,
        },
        "invalid_undefined_cases": invalid_undefined_rows,
        "case_rows": case_rows,
    }


def render_external_unearned_agreement_report(payload: dict[str, Any]) -> str:
    """Render markdown agreement report."""
    lines = [
        "# External Unearned Anchor — EF Agreement Analysis",
        "",
        "## Purpose",
        "",
        "Case-level agreement between **externally documented unearned labels** and "
        "**EarnBench EF@Π outcomes** on the same artifacts. This analysis distinguishes "
        "instrument agreement from expected OUT_OF_REGISTRY false-negative floor behavior.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        (f"| Catalog rows | {payload['catalog_row_count']} |"),
        (f"| Results rows | {payload['results_row_count']} |"),
        (f"| Included anchors | {payload['included_anchor_count']} |"),
        (f"| Decidable cases | {payload['decidable_case_count']} |"),
        (f"| EF agreement rate | {_format_float(payload['ef_agreement_rate'])} |"),
        "",
        "## Agreement class counts",
        "",
        "| Class | Count | Interpretation |",
        "| --- | ---: | --- |",
    ]

    class_help = {
        "ef_detects": "IN_REGISTRY: EF flagged unearned as expected",
        "ef_misses_expected": "OUT_OF_REGISTRY: EF≈1 as expected (false-negative floor)",
        "ef_misses_unexpected": "IN_REGISTRY: EF did not flag externally labeled unearned case",
        "ef_undefined": "Missing results, undefined EF, or non-primary registry row",
        "ef_disagrees_with_label": "Harness/EF outcome conflicts with external label scope",
        "excluded": "Catalog row not included in primary analysis",
    }
    for agreement_class in AGREEMENT_CLASSES:
        count = payload["agreement_class_counts"].get(agreement_class, 0)
        lines.append(
            f"| `{agreement_class}` | {count} | {class_help[agreement_class]} |"
        )

    lines.extend(
        [
            "",
            "## Agreement by source",
            "",
            "| Source | Included | Decidable | Agreeing | Rate |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for source, stats in payload["agreement_by_source"].items():
        lines.append(
            f"| {source} | {stats['included_count']} | {stats['decidable_count']} | "
            f"{stats['agreeing_count']} | {_format_float(stats['agreement_rate'])} |"
        )

    lines.extend(
        [
            "",
            "## Agreement by channel",
            "",
            "| Channel | Included | Decidable | Agreeing | Rate |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for channel, stats in payload["agreement_by_channel"].items():
        lines.append(
            f"| {channel} | {stats['included_count']} | {stats['decidable_count']} | "
            f"{stats['agreeing_count']} | {_format_float(stats['agreement_rate'])} |"
        )

    lines.extend(["", "## Disagreement cases", ""])
    if payload["disagreement_cases"]:
        for case in payload["disagreement_cases"]:
            lines.append(
                f"- **`{case['external_id']}`** (`{case['agreement_class']}`): "
                f"{case['agreement_explanation']}"
            )
    else:
        lines.append("_None._")

    lines.extend(["", "## Expected misses (OUT_OF_REGISTRY)", ""])
    oor = payload["expected_miss_out_of_registry"]
    lines.append(f"Count: **{oor['count']}**")
    if oor["rows"]:
        for row in oor["rows"]:
            lines.append(
                f"- **`{row['external_id']}`**: {row['agreement_explanation']}"
            )
    else:
        lines.append("_None._")

    lines.extend(["", "## Invalid / undefined cases", ""])
    if payload["invalid_undefined_cases"]:
        for case in payload["invalid_undefined_cases"]:
            lines.append(
                f"- **`{case['external_id']}`**: {case['agreement_explanation']}"
            )
    else:
        lines.append("_None._")

    lines.extend(
        [
            "",
            "## Case-level detail",
            "",
            "See `external_unearned_agreement.csv` for the full join with "
            "`agreement_class` and `agreement_explanation` per row.",
            "",
            "## Metric definitions",
            "",
            "- **EF agreement rate** = (`ef_detects` + `ef_misses_expected`) / decidable cases.",
            "- **Decidable** = included rows with class in "
            "`{ef_detects, ef_misses_expected, ef_misses_unexpected, ef_disagrees_with_label}`.",
            "- **ef_misses_expected** is agreement, not instrument failure: OOR scope documents "
            "the false-negative floor.",
            "- EF detection semantics are unchanged from `report external-unearned`.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_external_unearned_agreement_report(
    catalog_path: Path,
    results_path: Path,
    output_dir: Path,
) -> ExternalUnearnedAgreementResult:
    """Validate inputs, classify agreement, and write agreement artifacts."""
    catalog_rows = load_external_unearned_catalog(catalog_path)
    result_rows = load_external_unearned_results(results_path)
    payload = analyze_external_unearned_agreement(catalog_rows, result_rows)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    agreement_csv_path = output_dir / EXTERNAL_UNEARNED_AGREEMENT_CSV
    agreement_json_path = output_dir / EXTERNAL_UNEARNED_AGREEMENT_JSON
    agreement_md_path = output_dir / EXTERNAL_UNEARNED_AGREEMENT_MD

    case_rows = payload.pop("case_rows")
    _write_csv(agreement_csv_path, AGREEMENT_CASE_COLUMNS, case_rows)

    agreement_md_path.write_text(
        render_external_unearned_agreement_report(payload),
        encoding="utf-8",
    )

    with agreement_json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return ExternalUnearnedAgreementResult(
        output_dir=output_dir,
        agreement_csv=agreement_csv_path,
        agreement_json=agreement_json_path,
        agreement_md=agreement_md_path,
    )
