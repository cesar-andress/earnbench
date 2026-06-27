"""Validation 11: Registry Structure Validation (post-hoc Π channel analysis)."""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.registry_geometry import (
    CHANNELS,
    HIGH_COFAILURE_JACCARD,
    ParsedRow,
    _ablated_ef,
    _channel_outcome,
    _full_ef,
    _only_failed_channel,
    _pairwise_failure_stats,
    _parse_row,
    _profile_label,
    _write_csv,
)

REGISTRY_STRUCTURE_REPORT_MD = "registry_structure_report.md"
REGISTRY_STRUCTURE_SUMMARY_JSON = "registry_structure_summary.json"
REGISTRY_COFAILURE_MATRIX_CSV = "registry_cofailure_matrix.csv"
REGISTRY_OVERLAP_CSV = "registry_overlap.csv"
REGISTRY_UNIQUE_DETECTION_CSV = "registry_unique_detection.csv"
REGISTRY_INFORMATION_CONTENT_CSV = "registry_information_content.csv"
REGISTRY_SAME_EF_PROFILES_CSV = "registry_same_ef_profiles.csv"
REGISTRY_DIMENSIONALITY_JSON = "registry_dimensionality.json"
REGISTRY_INVALID_DISTRIBUTION_CSV = "registry_invalid_distribution.csv"

COFAILURE_COLUMNS = (
    "channel_a",
    "channel_b",
    "both_fail_count",
    "either_fail_count",
    "jaccard_overlap",
    "phi_correlation",
    "odds_ratio",
)

OVERLAP_COLUMNS = (
    "channel_a",
    "channel_b",
    "jaccard_overlap",
    "phi_correlation",
    "odds_ratio",
    "redundancy_estimate",
    "high_co_failure",
)

UNIQUE_DETECTION_COLUMNS = (
    "channel",
    "fail_count",
    "unique_fail_count",
    "shared_fail_count",
    "unique_detection_fraction",
    "example_instance_ids",
)

INFORMATION_COLUMNS = (
    "channel",
    "ef_change_count",
    "mean_ef_delta_under_ablation",
    "unique_information_count",
    "redundancy_ratio",
)

SAME_EF_PROFILE_COLUMNS = (
    "ef_pi",
    "profile_label",
    "instance_id",
    "y_vtest",
    "y_verif",
    "y_env",
)

INVALID_DISTRIBUTION_COLUMNS = (
    "channel",
    "invalid_count",
    "error_count",
    "measured_count",
    "invalid_rate",
    "error_rate",
    "bias_risk_note",
)


@dataclass(frozen=True, slots=True)
class RegistryStructureReportResult:
    output_dir: Path
    report_md: Path
    summary_json: Path
    cofailure_matrix_csv: Path
    overlap_csv: Path
    unique_detection_csv: Path
    information_content_csv: Path
    same_ef_profiles_csv: Path
    dimensionality_json: Path
    invalid_distribution_csv: Path


def _odds_ratio(both_fail: int, only_a: int, only_b: int, neither: int) -> float | None:
    if only_a == 0 or only_b == 0:
        return None
    numerator = both_fail * neither
    denominator = only_a * only_b
    if denominator == 0:
        return None
    return numerator / denominator


def _cofailure_rows(primary_rows: list[ParsedRow]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, channel_a in enumerate(CHANNELS):
        for channel_b in CHANNELS[index:]:
            stats = _pairwise_failure_stats(primary_rows, channel_a, channel_b)
            both_fail = stats["count_both_fail"]
            either_fail = stats["count_either_fail"]
            only_a = stats["unique_detections_a"]
            only_b = stats["unique_detections_b"]
            neither = len(primary_rows) - either_fail
            rows.append(
                {
                    "channel_a": channel_a,
                    "channel_b": channel_b,
                    "both_fail_count": both_fail,
                    "either_fail_count": either_fail,
                    "jaccard_overlap": stats["jaccard"],
                    "phi_correlation": stats["phi"],
                    "odds_ratio": _odds_ratio(both_fail, only_a, only_b, neither),
                }
            )
    return rows


def _overlap_rows(cofailure_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overlap: list[dict[str, Any]] = []
    for row in cofailure_rows:
        if row["channel_a"] == row["channel_b"]:
            continue
        jaccard = row["jaccard_overlap"]
        overlap.append(
            {
                "channel_a": row["channel_a"],
                "channel_b": row["channel_b"],
                "jaccard_overlap": jaccard,
                "phi_correlation": row["phi_correlation"],
                "odds_ratio": row["odds_ratio"],
                "redundancy_estimate": jaccard,
                "high_co_failure": (
                    jaccard is not None and jaccard >= HIGH_COFAILURE_JACCARD
                ),
            }
        )
    return overlap


def _unique_detection_rows(primary_rows: list[ParsedRow]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for channel in CHANNELS:
        fail_count = 0
        unique_fail_count = 0
        examples: list[str] = []
        for row in primary_rows:
            outcome = _channel_outcome(row, channel)
            if outcome is not False:
                continue
            fail_count += 1
            if _only_failed_channel(row, channel):
                unique_fail_count += 1
                examples.append(row.instance_id)
        shared_fail_count = fail_count - unique_fail_count
        rows.append(
            {
                "channel": channel,
                "fail_count": fail_count,
                "unique_fail_count": unique_fail_count,
                "shared_fail_count": shared_fail_count,
                "unique_detection_fraction": (
                    unique_fail_count / fail_count if fail_count else None
                ),
                "example_instance_ids": ";".join(examples[:5]),
            }
        )
    return rows


def _information_content_rows(
    primary_rows: list[ParsedRow],
    unique_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for channel in CHANNELS:
        ef_change = 0
        deltas: list[float] = []
        for row in primary_rows:
            full = _full_ef(row)
            ablated = _ablated_ef(row, exclude_channel=channel)
            if full is None or ablated is None:
                continue
            delta = ablated - full
            if not math.isclose(full, ablated, rel_tol=0.0, abs_tol=1e-9):
                ef_change += 1
            deltas.append(delta)
        unique = next(item for item in unique_rows if item["channel"] == channel)
        fail_count = unique["fail_count"]
        shared = unique["shared_fail_count"]
        rows.append(
            {
                "channel": channel,
                "ef_change_count": ef_change,
                "mean_ef_delta_under_ablation": (
                    statistics.fmean(deltas) if deltas else None
                ),
                "unique_information_count": unique["unique_fail_count"],
                "redundancy_ratio": (
                    shared / fail_count if fail_count else None
                ),
            }
        )
    return rows


def _same_ef_profile_rows(primary_rows: list[ParsedRow]) -> list[dict[str, Any]]:
    by_ef: dict[float, list[ParsedRow]] = defaultdict(list)
    for row in primary_rows:
        ef = _full_ef(row)
        if ef is None:
            continue
        by_ef[round(ef, 6)].append(row)

    exported: list[dict[str, Any]] = []
    for ef_key in sorted(by_ef):
        group = by_ef[ef_key]
        profiles = {
            _profile_label(row.y_vtest, row.y_verif, row.y_env)  # type: ignore[arg-type]
            for row in group
        }
        if len(profiles) <= 1:
            continue
        for row in group:
            assert row.y_vtest is not None
            assert row.y_verif is not None
            assert row.y_env is not None
            exported.append(
                {
                    "ef_pi": ef_key,
                    "profile_label": _profile_label(
                        row.y_vtest, row.y_verif, row.y_env
                    ),
                    "instance_id": row.instance_id,
                    "y_vtest": int(row.y_vtest),
                    "y_verif": int(row.y_verif),
                    "y_env": int(row.y_env),
                }
            )
    return exported


def _failure_vectors(primary_rows: list[ParsedRow]) -> list[tuple[int, int, int]]:
    vectors: list[tuple[int, int, int]] = []
    for row in primary_rows:
        assert row.y_vtest is not None
        assert row.y_verif is not None
        assert row.y_env is not None
        vectors.append(
            (
                int(not row.y_vtest),
                int(not row.y_verif),
                int(not row.y_env),
            )
        )
    return vectors


def _covariance_matrix(vectors: list[tuple[int, int, int]]) -> list[list[float]]:
    if not vectors:
        return [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    n = len(vectors)
    means = [sum(component[i] for component in vectors) / n for i in range(3)]
    cov = [[0.0] * 3 for _ in range(3)]
    for vector in vectors:
        for i in range(3):
            for j in range(i, 3):
                cov[i][j] += (vector[i] - means[i]) * (vector[j] - means[j])
    for i in range(3):
        for j in range(i, 3):
            value = cov[i][j] / n if n else 0.0
            cov[i][j] = value
            cov[j][i] = value
    return cov


def _jacobi_eigenvalues(
    matrix: list[list[float]],
    *,
    max_iter: int = 50,
    tol: float = 1e-10,
) -> list[float]:
    size = len(matrix)
    a = [row[:] for row in matrix]
    for _ in range(max_iter):
        pivot = 0.0
        p = 0
        q = 1
        for i in range(size):
            for j in range(i + 1, size):
                if abs(a[i][j]) > pivot:
                    pivot = abs(a[i][j])
                    p, q = i, j
        if pivot < tol:
            break
        app = a[p][p]
        aqq = a[q][q]
        apq = a[p][q]
        if apq == 0.0:
            continue
        phi = 0.5 * math.atan2(2.0 * apq, app - aqq)
        c = math.cos(phi)
        s = math.sin(phi)
        for i in range(size):
            if i not in (p, q):
                aip = a[i][p]
                aiq = a[i][q]
                a[i][p] = c * aip - s * aiq
                a[p][i] = a[i][p]
                a[i][q] = s * aip + c * aiq
                a[q][i] = a[i][q]
        app_new = c * c * app - 2.0 * s * c * apq + s * s * aqq
        aqq_new = s * s * app + 2.0 * s * c * apq + c * c * aqq
        a[p][p] = app_new
        a[q][q] = aqq_new
        a[p][q] = 0.0
        a[q][p] = 0.0
    return sorted((a[i][i] for i in range(size)), reverse=True)


def _dimensionality_analysis(primary_rows: list[ParsedRow]) -> dict[str, Any]:
    vectors = _failure_vectors(primary_rows)
    cov = _covariance_matrix(vectors)
    eigenvalues = _jacobi_eigenvalues(cov)
    total = sum(eigenvalues)
    explained = [
        (value / total if total > 0 else None) for value in eigenvalues
    ]
    dominant_fraction = explained[0] if explained else None

    if dominant_fraction is None or total == 0.0:
        interpretation = (
            "Insufficient failure variance in primary cohort to assess dimensionality."
        )
    elif dominant_fraction >= 0.80:
        interpretation = (
            "Failure structure largely collapses toward one dominant component "
            "(first eigenvalue explains ≥80% of failure-indicator variance)."
        )
    elif dominant_fraction >= 0.50:
        interpretation = (
            "Failure structure shows a leading component plus secondary structure "
            "(first eigenvalue explains 50–80% of variance)."
        )
    else:
        interpretation = (
            "Failure structure spreads across multiple components "
            "(no single eigenvalue dominates)."
        )

    return {
        "failure_covariance_matrix": cov,
        "eigenvalues": eigenvalues,
        "explained_variance_fraction": explained,
        "dominant_eigenvalue_fraction": dominant_fraction,
        "interpretation": interpretation,
        "note": (
            "Descriptive PCA-style summary on binary failure indicators; "
            "does not imply causal channel independence or interaction."
        ),
    }


def _channel_status_key(row: ParsedRow, channel: str) -> str:
    return {
        "vtest": row.pi_vtest_status,
        "verif": row.pi_verif_status,
        "env": row.pi_env_status,
    }[channel].strip().lower()


def _invalid_distribution_rows(parsed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    y0_rows = [row for row in parsed_rows if row.y0]
    rows: list[dict[str, Any]] = []
    for channel in CHANNELS:
        invalid_count = 0
        error_count = 0
        measured_count = 0
        for row in y0_rows:
            status = _channel_status_key(row, channel)
            if status in {"success", "fail"}:
                measured_count += 1
            elif status == "invalid":
                invalid_count += 1
            elif status == "error":
                error_count += 1
        denominator = len(y0_rows)
        invalid_rate = invalid_count / denominator if denominator else None
        error_rate = error_count / denominator if denominator else None
        bias_note = ""
        if invalid_rate is not None and invalid_rate >= 0.10:
            bias_note = "High INVALID rate may bias primary structure estimates."
        elif error_rate is not None and error_rate >= 0.10:
            bias_note = "High ERROR rate may reduce measurable channel coverage."
        rows.append(
            {
                "channel": channel,
                "invalid_count": invalid_count,
                "error_count": error_count,
                "measured_count": measured_count,
                "invalid_rate": invalid_rate,
                "error_rate": error_rate,
                "bias_risk_note": bias_note,
            }
        )
    return rows


def analyze_registry_structure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute Validation 11 registry structure metrics."""
    parsed = [_parse_row(row) for row in rows]
    y0_rows = [row for row in parsed if row.y0]
    primary_rows = [row for row in parsed if row.primary_eligible]

    exclusion_counts = Counter(
        row.exclusion_reason for row in y0_rows if row.exclusion_reason is not None
    )

    cofailure_rows = _cofailure_rows(primary_rows)
    overlap_rows = _overlap_rows(cofailure_rows)
    unique_rows = _unique_detection_rows(primary_rows)
    information_rows = _information_content_rows(primary_rows, unique_rows)
    same_ef_rows = _same_ef_profile_rows(primary_rows)
    dimensionality = _dimensionality_analysis(primary_rows)
    invalid_rows = _invalid_distribution_rows(parsed)

    high_redundancy = [
        row for row in overlap_rows if row["high_co_failure"]
    ]
    unique_channels = [
        row["channel"]
        for row in unique_rows
        if row["unique_fail_count"] > 0
    ]

    same_ef_summary: dict[float, dict[str, Any]] = {}
    for row in same_ef_rows:
        bucket = same_ef_summary.setdefault(
            row["ef_pi"],
            {"ef_pi": row["ef_pi"], "profiles": set(), "instance_ids": []},
        )
        bucket["profiles"].add(row["profile_label"])
        bucket["instance_ids"].append(row["instance_id"])

    same_ef_examples = [
        {
            "ef_pi": ef,
            "profiles": sorted(payload["profiles"]),
            "instance_ids": payload["instance_ids"],
        }
        for ef, payload in sorted(same_ef_summary.items())
    ]

    return {
        "schema_version": "earnbench.registry_structure_validation.v1",
        "validation_layer": 11,
        "input_row_count": len(parsed),
        "y0_row_count": len(y0_rows),
        "primary_row_count": len(primary_rows),
        "excluded_from_primary": dict(sorted(exclusion_counts.items())),
        "cofailure_matrix": cofailure_rows,
        "overlap": overlap_rows,
        "unique_detection": unique_rows,
        "information_content": information_rows,
        "same_ef_different_profile_examples": same_ef_examples,
        "dimensionality": dimensionality,
        "invalid_distribution": invalid_rows,
        "redundancy_summary": {
            "high_co_failure_pairs": high_redundancy,
            "high_co_failure_jaccard_threshold": HIGH_COFAILURE_JACCARD,
            "channels_with_unique_detections": unique_channels,
        },
        "note": (
            "Registry Structure Validation is post-hoc internal-validity analysis. "
            "It does not modify EF@Π, Π membership, or INVALID semantics."
        ),
    }


def _render_report_md(payload: dict[str, Any], *, summary_path: Path) -> str:
    lines = [
        "# Registry Structure Validation (Validation 11)",
        "",
        f"**Input:** `{summary_path}`",
        "",
        "## Scope",
        "",
        "Registry Structure Validation is **not** a new EF estimand, benchmark, or leaderboard.",
        "It validates the **empirical structure** of the frozen multichannel registry Π on",
        "existing `summary.csv` rows.",
        "",
        "This analysis:",
        "",
        "- does **not** modify EF@Π, Π membership, or INVALID semantics;",
        "- does **not** claim causal interaction between channels;",
        "- does **not** claim channel independence unless association statistics support it;",
        "- reports honestly when channels appear redundant on observed data.",
        "",
        "Equal EF@Π can hide distinct exploitation profiles (same scalar, different",
        "survival/failure vectors).",
        "",
        "## Cohort",
        "",
        f"- Input rows: {payload['input_row_count']}",
        f"- Y0 rows: {payload['y0_row_count']}",
        f"- Primary rows (Y0 + all valid π): {payload['primary_row_count']}",
        "",
        "### Excluded from primary channel-structure estimates",
        "",
    ]
    if payload["excluded_from_primary"]:
        for reason, count in payload["excluded_from_primary"].items():
            lines.append(f"- `{reason}`: {count}")
    else:
        lines.append("- None")

    lines.extend(["", "## Co-failure and overlap", ""])
    for row in payload["overlap"]:
        jaccard = row["jaccard_overlap"]
        jaccard_text = f"{jaccard:.4f}" if jaccard is not None else "—"
        lines.append(
            f"- `{row['channel_a']}` × `{row['channel_b']}`: "
            f"Jaccard={jaccard_text}, high_co_failure={row['high_co_failure']}"
        )

    lines.extend(["", "## Unique detection", ""])
    for row in payload["unique_detection"]:
        fraction = row["unique_detection_fraction"]
        fraction_text = f"{fraction:.4f}" if fraction is not None else "—"
        lines.append(
            f"- `{row['channel']}`: fail={row['fail_count']}, "
            f"unique={row['unique_fail_count']}, fraction={fraction_text}"
        )

    lines.extend(["", "## Registry dimensionality", ""])
    dim = payload["dimensionality"]
    lines.append(f"- Interpretation: {dim['interpretation']}")
    if dim["eigenvalues"]:
        lines.append(
            "- Eigenvalues: "
            + ", ".join(f"{value:.6f}" for value in dim["eigenvalues"])
        )
        explained = dim["explained_variance_fraction"]
        if explained and explained[0] is not None:
            lines.append(
                f"- Dominant explained variance: {explained[0]:.4f}"
            )

    lines.extend(["", "## INVALID localisation", ""])
    for row in payload["invalid_distribution"]:
        invalid_rate = row["invalid_rate"]
        rate_text = f"{invalid_rate:.4f}" if invalid_rate is not None else "—"
        note = row["bias_risk_note"] or "—"
        lines.append(
            f"- `{row['channel']}`: invalid_rate={rate_text}; {note}"
        )

    lines.extend(["", "## Same EF, different profile", ""])
    if not payload["same_ef_different_profile_examples"]:
        lines.append("No examples in primary cohort.")
    else:
        for example in payload["same_ef_different_profile_examples"]:
            lines.append(
                f"- EF={example['ef_pi']}: profiles={example['profiles']} "
                f"instances={example['instance_ids']}"
            )

    redundancy = payload["redundancy_summary"]
    lines.extend(["", "## Redundancy summary", ""])
    if redundancy["high_co_failure_pairs"]:
        lines.append(
            "Observed high co-failure pairs (associative; not causal):"
        )
        for pair in redundancy["high_co_failure_pairs"]:
            jaccard = pair["jaccard_overlap"]
            jaccard_text = f"{jaccard:.4f}" if jaccard is not None else "—"
            lines.append(
                f"- `{pair['channel_a']}` × `{pair['channel_b']}` "
                f"(Jaccard={jaccard_text})"
            )
    else:
        lines.append(
            "No channel pairs exceeded the pre-registered high co-failure threshold."
        )

    lines.append("")
    return "\n".join(lines)


def generate_registry_structure_report(
    summary_path: Path,
    output_dir: Path,
) -> RegistryStructureReportResult:
    """Load summary.csv and write Validation 11 artifacts."""
    from earnbench.bootstrap_uncertainty import load_phase_summary_rows

    rows = load_phase_summary_rows(summary_path)
    payload = analyze_registry_structure(rows)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report_md = output_dir / REGISTRY_STRUCTURE_REPORT_MD
    summary_json = output_dir / REGISTRY_STRUCTURE_SUMMARY_JSON
    cofailure_csv = output_dir / REGISTRY_COFAILURE_MATRIX_CSV
    overlap_csv = output_dir / REGISTRY_OVERLAP_CSV
    unique_csv = output_dir / REGISTRY_UNIQUE_DETECTION_CSV
    information_csv = output_dir / REGISTRY_INFORMATION_CONTENT_CSV
    same_ef_csv = output_dir / REGISTRY_SAME_EF_PROFILES_CSV
    dimensionality_json = output_dir / REGISTRY_DIMENSIONALITY_JSON
    invalid_csv = output_dir / REGISTRY_INVALID_DISTRIBUTION_CSV

    report_md.write_text(
        _render_report_md(payload, summary_path=summary_path.resolve()),
        encoding="utf-8",
    )
    with summary_json.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with dimensionality_json.open("w", encoding="utf-8") as handle:
        json.dump(payload["dimensionality"], handle, indent=2, sort_keys=True)
        handle.write("\n")

    _write_csv(cofailure_csv, COFAILURE_COLUMNS, payload["cofailure_matrix"])
    _write_csv(overlap_csv, OVERLAP_COLUMNS, payload["overlap"])
    _write_csv(unique_csv, UNIQUE_DETECTION_COLUMNS, payload["unique_detection"])
    _write_csv(
        information_csv,
        INFORMATION_COLUMNS,
        payload["information_content"],
    )
    primary_rows = [row for row in (_parse_row(row) for row in rows) if row.primary_eligible]
    _write_csv(
        same_ef_csv,
        SAME_EF_PROFILE_COLUMNS,
        _same_ef_profile_rows(primary_rows),
    )
    _write_csv(invalid_csv, INVALID_DISTRIBUTION_COLUMNS, payload["invalid_distribution"])

    return RegistryStructureReportResult(
        output_dir=output_dir,
        report_md=report_md,
        summary_json=summary_json,
        cofailure_matrix_csv=cofailure_csv,
        overlap_csv=overlap_csv,
        unique_detection_csv=unique_csv,
        information_content_csv=information_csv,
        same_ef_profiles_csv=same_ef_csv,
        dimensionality_json=dimensionality_json,
        invalid_distribution_csv=invalid_csv,
    )
