"""Post-hoc registry geometry analysis from Phase A/B summary.csv rows."""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.pi_ablation import PI_COLUMNS, _ef_from_pi_outcomes, _row_pi_outcomes

REGISTRY_GEOMETRY_SUMMARY_JSON = "registry_geometry_summary.json"
REGISTRY_GEOMETRY_PROFILES_CSV = "registry_geometry_profiles.csv"
REGISTRY_GEOMETRY_COFAILURE_MATRIX_CSV = "registry_geometry_cofailure_matrix.csv"
REGISTRY_GEOMETRY_CHANNEL_CORRELATIONS_CSV = "registry_geometry_channel_correlations.csv"
REGISTRY_GEOMETRY_MARGINAL_CONTRIBUTION_CSV = "registry_geometry_marginal_contribution.csv"
REGISTRY_GEOMETRY_REPORT_MD = "registry_geometry_report.md"

CHANNELS = ("vtest", "verif", "env")
CHANNEL_PI_IDS = {
    "vtest": "pi_vtest.v1",
    "verif": "pi_verif.v1",
    "env": "pi_env.v1",
}

PROFILE_LABELS = (
    "survive_all",
    "fail_vtest",
    "fail_verif",
    "fail_env",
    "fail_vtest_verif",
    "fail_vtest_env",
    "fail_verif_env",
    "fail_all",
)

PROFILE_COLUMNS = (
    "profile_label",
    "count",
    "fraction",
    "mean_ef",
    "median_ef",
    "invalid_count",
)

COFAILURE_COLUMNS = (
    "channel_a",
    "channel_b",
    "count_both_fail",
    "count_either_fail",
    "jaccard",
    "phi",
)

CORRELATION_COLUMNS = (
    "channel_a",
    "channel_b",
    "phi",
    "jaccard",
    "redundancy_estimate",
    "high_co_failure",
    "unique_detections_a",
    "unique_detections_b",
)

MARGINAL_COLUMNS = (
    "channel",
    "only_failed_channel_count",
    "ef_change_count",
    "mean_ef_delta_under_ablation",
    "unique_detection_count",
)

HIGH_COFAILURE_JACCARD = 0.70

PRIMARY_EXCLUSION_KEYS = (
    "y0_false",
    "invalid_status",
    "error_status",
    "missing_status",
    "not_applicable_status",
    "missing_outcome",
    "ef_undefined",
    "unknown_status",
)


@dataclass(frozen=True, slots=True)
class ParsedRow:
    raw: dict[str, Any]
    instance_id: str
    agent: str | None
    y0: bool
    y_vtest: bool | None
    y_verif: bool | None
    y_env: bool | None
    pi_vtest_status: str
    pi_verif_status: str
    pi_env_status: str
    channel_status_kinds: dict[str, str]
    ef_pi: float | None
    ef_status: str
    invalid_pi_count: int
    primary_eligible: bool
    exclusion_reason: str | None


@dataclass(frozen=True, slots=True)
class RegistryGeometryReportResult:
    output_dir: Path
    summary_json: Path
    profiles_csv: Path
    cofailure_matrix_csv: Path
    channel_correlations_csv: Path
    marginal_contribution_csv: Path
    report_md: Path


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _parse_optional_bool(value: object) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    return _as_bool(value)


def _parse_optional_float(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(value)


def _parse_optional_int(value: object, *, default: int = 0) -> int:
    if value is None or str(value).strip() == "":
        return default
    return int(value)


def _normalize_pi_status_kind(status: object) -> str:
    """Map summary.csv pi_*_status values to normalized lifecycle kinds."""
    text = str(status if status is not None else "").strip().lower()
    if text in {"ok", "success", "fail"}:
        return "ok"
    if text == "invalid":
        return "invalid"
    if text == "error":
        return "error"
    if text in {"missing", ""}:
        return "missing"
    if text in {"not_applicable", "not-applicable", "n/a", "na"}:
        return "not_applicable"
    return "unknown"


def _is_measured_status_kind(kind: str) -> bool:
    return kind == "ok"


def _channel_status_kind(row: ParsedRow, channel: str) -> str:
    return row.channel_status_kinds[channel]


def _channel_is_observed(row: ParsedRow, channel: str) -> bool:
    return _is_measured_status_kind(_channel_status_kind(row, channel)) and (
        _channel_outcome(row, channel) is not None
    )


def _compute_primary_exclusion_reason(
    *,
    y0: bool,
    kinds: dict[str, str],
    outcomes: dict[str, bool | None],
    ef_status: str,
) -> str | None:
    if not y0:
        return "y0_false"
    if any(kinds[channel] == "invalid" for channel in CHANNELS):
        return "invalid_status"
    if any(kinds[channel] == "error" for channel in CHANNELS):
        return "error_status"
    if any(kinds[channel] == "missing" for channel in CHANNELS):
        return "missing_status"
    if any(kinds[channel] == "not_applicable" for channel in CHANNELS):
        return "not_applicable_status"
    if any(kinds[channel] == "unknown" for channel in CHANNELS):
        return "unknown_status"
    if any(
        kinds[channel] == "ok" and outcomes[channel] is None for channel in CHANNELS
    ):
        return "missing_outcome"
    ef_status_norm = ef_status.strip().lower()
    if ef_status_norm and ef_status_norm != "defined":
        return "ef_undefined"
    return None


def _excluded_from_primary_breakdown(parsed: list[ParsedRow]) -> dict[str, int]:
    counter = Counter(
        row.exclusion_reason for row in parsed if row.exclusion_reason is not None
    )
    return {
        key: counter.get(key, 0)
        for key in PRIMARY_EXCLUSION_KEYS
        if counter.get(key, 0) > 0
    }


def _is_valid_pi_status(status: str) -> bool:
    return _is_measured_status_kind(_normalize_pi_status_kind(status))


def _channel_status(row: ParsedRow, channel: str) -> str:
    return {
        "vtest": row.pi_vtest_status,
        "verif": row.pi_verif_status,
        "env": row.pi_env_status,
    }[channel]


def _channel_outcome(row: ParsedRow, channel: str) -> bool | None:
    return {
        "vtest": row.y_vtest,
        "verif": row.y_verif,
        "env": row.y_env,
    }[channel]


def _profile_label(y_vtest: bool, y_verif: bool, y_env: bool) -> str:
    failures = (
        not y_vtest,
        not y_verif,
        not y_env,
    )
    mapping = {
        (False, False, False): "survive_all",
        (True, False, False): "fail_vtest",
        (False, True, False): "fail_verif",
        (False, False, True): "fail_env",
        (True, True, False): "fail_vtest_verif",
        (True, False, True): "fail_vtest_env",
        (False, True, True): "fail_verif_env",
        (True, True, True): "fail_all",
    }
    return mapping[failures]


def _phi_coefficient(a: int, b: int, c: int, d: int) -> float | None:
    denom = (a + b) * (c + d) * (a + c) * (b + d)
    if denom == 0:
        return None
    return (a * d - b * c) / math.sqrt(denom)


def _pairwise_failure_stats(
    rows: list[ParsedRow],
    channel_a: str,
    channel_b: str,
) -> dict[str, Any]:
    both_fail = 0
    either_fail = 0
    only_a = 0
    only_b = 0
    neither = 0
    observed_rows = 0
    for row in rows:
        if not (
            _channel_is_observed(row, channel_a)
            and _channel_is_observed(row, channel_b)
        ):
            continue
        observed_rows += 1
        fail_a = _channel_outcome(row, channel_a) is False
        fail_b = _channel_outcome(row, channel_b) is False
        if fail_a and fail_b:
            both_fail += 1
        if fail_a or fail_b:
            either_fail += 1
        if fail_a and not fail_b:
            only_a += 1
        if fail_b and not fail_a:
            only_b += 1
        if not fail_a and not fail_b:
            neither += 1

    jaccard = both_fail / either_fail if either_fail else None
    phi = _phi_coefficient(both_fail, only_a, only_b, neither)
    return {
        "channel_a": channel_a,
        "channel_b": channel_b,
        "count_both_fail": both_fail,
        "count_either_fail": either_fail,
        "observed_row_count": observed_rows,
        "jaccard": jaccard,
        "phi": phi,
        "redundancy_estimate": jaccard,
        "high_co_failure": jaccard is not None and jaccard >= HIGH_COFAILURE_JACCARD,
        "unique_detections_a": only_a,
        "unique_detections_b": only_b,
    }


def _full_ef(row: ParsedRow) -> float | None:
    if row.ef_pi is not None:
        return row.ef_pi
    return _ef_from_pi_outcomes(_row_pi_outcomes(row.raw))


def _ablated_ef(row: ParsedRow, *, exclude_channel: str) -> float | None:
    exclude_pi = CHANNEL_PI_IDS[exclude_channel]
    return _ef_from_pi_outcomes(_row_pi_outcomes(row.raw), exclude_pi=exclude_pi)


def _only_failed_channel(row: ParsedRow, channel: str) -> bool:
    outcomes = {ch: _channel_outcome(row, ch) for ch in CHANNELS}
    if any(value is None for value in outcomes.values()):
        return False
    failures = {ch: outcomes[ch] is False for ch in CHANNELS}
    return failures[channel] and sum(failures.values()) == 1


def _parse_row(raw: dict[str, Any]) -> ParsedRow:
    instance_id = str(raw.get("instance_id", "")).strip()
    agent_value = raw.get("agent")
    agent = str(agent_value).strip() if agent_value not in (None, "") else None

    y0 = _as_bool(raw.get("y0"))
    statuses = {
        "vtest": str(raw.get("pi_vtest_status", "")).strip(),
        "verif": str(raw.get("pi_verif_status", "")).strip(),
        "env": str(raw.get("pi_env_status", "")).strip(),
    }
    outcomes = {
        "vtest": _parse_optional_bool(raw.get("y_vtest")),
        "verif": _parse_optional_bool(raw.get("y_verif")),
        "env": _parse_optional_bool(raw.get("y_env")),
    }

    invalid_pi_count = _parse_optional_int(raw.get("invalid_pi_count"))
    ef_pi = _parse_optional_float(raw.get("ef_pi"))
    ef_status = str(raw.get("ef_status", "")).strip()
    kinds = {channel: _normalize_pi_status_kind(statuses[channel]) for channel in CHANNELS}

    exclusion_reason = _compute_primary_exclusion_reason(
        y0=y0,
        kinds=kinds,
        outcomes=outcomes,
        ef_status=ef_status,
    )
    primary_eligible = exclusion_reason is None

    return ParsedRow(
        raw=raw,
        instance_id=instance_id,
        agent=agent,
        y0=y0,
        y_vtest=outcomes["vtest"],
        y_verif=outcomes["verif"],
        y_env=outcomes["env"],
        pi_vtest_status=statuses["vtest"],
        pi_verif_status=statuses["verif"],
        pi_env_status=statuses["env"],
        channel_status_kinds=kinds,
        ef_pi=ef_pi,
        ef_status=ef_status,
        invalid_pi_count=invalid_pi_count,
        primary_eligible=primary_eligible,
        exclusion_reason=exclusion_reason,
    )


def _profile_rows(primary_rows: list[ParsedRow]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for row in primary_rows:
        assert row.y_vtest is not None
        assert row.y_verif is not None
        assert row.y_env is not None
        label = _profile_label(row.y_vtest, row.y_verif, row.y_env)
        counts[label] += 1
        ef = _full_ef(row)
        if ef is not None:
            grouped[label].append(ef)

    total = len(primary_rows)
    rows: list[dict[str, Any]] = []
    for label in PROFILE_LABELS:
        count = counts.get(label, 0)
        ef_values = grouped.get(label, [])
        rows.append(
            {
                "profile_label": label,
                "count": count,
                "fraction": count / total if total else None,
                "mean_ef": statistics.fmean(ef_values) if ef_values else None,
                "median_ef": statistics.median(ef_values) if ef_values else None,
                "invalid_count": 0,
            }
        )
    return rows


def _same_ef_different_profiles(primary_rows: list[ParsedRow]) -> list[dict[str, Any]]:
    by_ef: dict[float, dict[str, Any]] = {}
    for row in primary_rows:
        ef = _full_ef(row)
        if ef is None:
            continue
        assert row.y_vtest is not None
        assert row.y_verif is not None
        assert row.y_env is not None
        profile = _profile_label(row.y_vtest, row.y_verif, row.y_env)
        bucket = by_ef.setdefault(
            round(ef, 6),
            {"ef_pi": round(ef, 6), "profiles": set(), "instance_ids": []},
        )
        bucket["profiles"].add(profile)
        bucket["instance_ids"].append(row.instance_id)

    examples: list[dict[str, Any]] = []
    for payload in sorted(by_ef.values(), key=lambda item: item["ef_pi"]):
        if len(payload["profiles"]) <= 1:
            continue
        examples.append(
            {
                "ef_pi": payload["ef_pi"],
                "profiles": sorted(payload["profiles"]),
                "instance_ids": payload["instance_ids"],
            }
        )
    return examples


def _marginal_contribution_rows(primary_rows: list[ParsedRow]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for channel in CHANNELS:
        only_failed = 0
        ef_change = 0
        deltas: list[float] = []
        for row in primary_rows:
            if _only_failed_channel(row, channel):
                only_failed += 1
            full = _full_ef(row)
            ablated = _ablated_ef(row, exclude_channel=channel)
            if full is None or ablated is None:
                continue
            delta = ablated - full
            if not math.isclose(full, ablated, rel_tol=0.0, abs_tol=1e-9):
                ef_change += 1
            deltas.append(delta)
        rows.append(
            {
                "channel": channel,
                "only_failed_channel_count": only_failed,
                "ef_change_count": ef_change,
                "mean_ef_delta_under_ablation": (
                    statistics.fmean(deltas) if deltas else None
                ),
                "unique_detection_count": only_failed,
            }
        )
    return rows


def _profiles_by_agent(primary_rows: list[ParsedRow]) -> dict[str, list[dict[str, Any]]]:
    by_agent: dict[str, list[ParsedRow]] = defaultdict(list)
    for row in primary_rows:
        if row.agent:
            by_agent[row.agent].append(row)
    return {agent: _profile_rows(agent_rows) for agent, agent_rows in sorted(by_agent.items())}


def analyze_registry_geometry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute registry geometry metrics from summary.csv rows."""
    parsed = [_parse_row(row) for row in rows]
    y0_rows = [row for row in parsed if row.y0]
    primary_rows = [row for row in parsed if row.primary_eligible]
    exclusion_breakdown = _excluded_from_primary_breakdown(parsed)
    partial_measurement_rows = len(
        [
            row
            for row in y0_rows
            if not row.primary_eligible
            and row.exclusion_reason in {"missing_status", "missing_outcome"}
        ]
    )

    profile_rows = _profile_rows(primary_rows)
    cofailure_rows: list[dict[str, Any]] = []
    correlation_rows: list[dict[str, Any]] = []
    for index, channel_a in enumerate(CHANNELS):
        for channel_b in CHANNELS[index:]:
            stats = _pairwise_failure_stats(y0_rows, channel_a, channel_b)
            cofailure_rows.append(
                {key: stats[key] for key in COFAILURE_COLUMNS}
            )
            correlation_rows.append(stats)

    marginal_rows = _marginal_contribution_rows(primary_rows)
    same_ef_examples = _same_ef_different_profiles(primary_rows)

    high_redundancy_pairs = [
        {
            "channel_a": row["channel_a"],
            "channel_b": row["channel_b"],
            "jaccard": row["jaccard"],
        }
        for row in correlation_rows
        if row["channel_a"] != row["channel_b"] and row["high_co_failure"]
    ]
    unique_channels = [
        row["channel"]
        for row in marginal_rows
        if row["unique_detection_count"] > 0
    ]

    agent_profiles = _profiles_by_agent(primary_rows)

    return {
        "schema_version": "earnbench.registry_geometry.v1",
        "input_row_count": len(parsed),
        "y0_row_count": len(y0_rows),
        "primary_row_count": len(primary_rows),
        "excluded_from_primary": {
            "counts_by_reason": exclusion_breakdown,
            "partial_measurement_y0_rows": partial_measurement_rows,
        },
        "profiles": profile_rows,
        "cofailure_matrix": cofailure_rows,
        "channel_correlations": correlation_rows,
        "marginal_contribution": marginal_rows,
        "redundancy": {
            "high_co_failure_pairs": high_redundancy_pairs,
            "high_co_failure_jaccard_threshold": HIGH_COFAILURE_JACCARD,
            "channels_with_unique_detections": unique_channels,
        },
        "same_ef_different_profile_examples": same_ef_examples,
        "by_agent": agent_profiles if agent_profiles else None,
        "note": (
            "Post-hoc internal-validity analysis only; does not modify EF@Π, "
            "Π membership, or INVALID semantics."
        ),
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
                    formatted[column] = "1" if value else "0"
                elif value is None:
                    formatted[column] = ""
                else:
                    formatted[column] = value
            writer.writerow(formatted)


def _render_report_md(payload: dict[str, Any], *, summary_path: Path) -> str:
    lines = [
        "# Registry geometry report",
        "",
        f"**Input:** `{summary_path}`",
        "",
        "Post-hoc multichannel survival analysis. Does **not** modify EF@Π, Π, or INVALID semantics.",
        "",
        "## Cohort",
        "",
        f"- Input rows: {payload['input_row_count']}",
        f"- Y0 rows: {payload['y0_row_count']}",
        f"- Primary rows (Y0 + all three channels measured `ok` with outcomes): "
        f"{payload['primary_row_count']}",
        "",
        "### Excluded from primary",
        "",
        "Status kinds follow Phase A `OutcomeStatus` (`ok`, `invalid`, `error`, "
        "`missing`, `not_applicable`). Phase B-style `success`/`fail` are treated as "
        "measured (`ok`). Pairwise co-failure uses Y0 rows where both channels are "
        "observed, even if a third channel is missing.",
        "",
    ]
    excluded = payload["excluded_from_primary"]
    if excluded["counts_by_reason"]:
        for reason, count in excluded["counts_by_reason"].items():
            lines.append(f"- `{reason}`: {count}")
    else:
        lines.append("- None")
    lines.append(
        f"- Partial-measurement Y0 rows (missing channel/outcome): "
        f"{excluded['partial_measurement_y0_rows']}"
    )
    lines.extend(["", "## Exploitation profiles", ""])
    lines.append("| Profile | Count | Fraction | Mean EF | Median EF |")
    lines.append("|---------|------:|---------:|--------:|----------:|")
    for row in payload["profiles"]:
        if row["count"] == 0:
            continue
        mean_ef = row["mean_ef"]
        median_ef = row["median_ef"]
        lines.append(
            f"| {row['profile_label']} | {row['count']} | "
            f"{row['fraction']:.4f} | "
            f"{mean_ef:.4f} | "
            f"{median_ef:.4f} |"
            if mean_ef is not None and median_ef is not None and row["fraction"] is not None
            else f"| {row['profile_label']} | {row['count']} | — | — | — |"
        )

    lines.extend(["", "## Same EF, different profile", ""])
    examples = payload["same_ef_different_profile_examples"]
    if not examples:
        lines.append("No examples in primary cohort.")
    else:
        for example in examples:
            lines.append(
                f"- EF={example['ef_pi']}: profiles={example['profiles']} "
                f"instances={example['instance_ids']}"
            )

    lines.extend(["", "## Redundancy", ""])
    redundancy = payload["redundancy"]
    if redundancy["high_co_failure_pairs"]:
        for pair in redundancy["high_co_failure_pairs"]:
            lines.append(
                f"- High co-failure: `{pair['channel_a']}` × `{pair['channel_b']}` "
                f"(Jaccard={pair['jaccard']:.4f})"
            )
    else:
        lines.append("- No channel pairs above redundancy threshold.")
    if redundancy["channels_with_unique_detections"]:
        lines.append(
            "- Channels with unique single-channel detections: "
            + ", ".join(redundancy["channels_with_unique_detections"])
        )

    if payload.get("by_agent"):
        lines.extend(["", "## Profiles by agent", ""])
        for agent, profiles in payload["by_agent"].items():
            active = [row for row in profiles if row["count"] > 0]
            if not active:
                continue
            lines.append(f"### {agent}")
            for row in active:
                lines.append(
                    f"- {row['profile_label']}: count={row['count']} "
                    f"fraction={row['fraction']:.4f}"
                    if row["fraction"] is not None
                    else f"- {row['profile_label']}: count={row['count']}"
                )

    lines.append("")
    return "\n".join(lines)


def generate_registry_geometry_report(
    summary_path: Path,
    output_dir: Path,
) -> RegistryGeometryReportResult:
    """Load summary.csv and write registry geometry artifacts."""
    from earnbench.bootstrap_uncertainty import load_phase_summary_rows

    rows = load_phase_summary_rows(summary_path)
    payload = analyze_registry_geometry(rows)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_json = output_dir / REGISTRY_GEOMETRY_SUMMARY_JSON
    profiles_csv = output_dir / REGISTRY_GEOMETRY_PROFILES_CSV
    cofailure_csv = output_dir / REGISTRY_GEOMETRY_COFAILURE_MATRIX_CSV
    correlations_csv = output_dir / REGISTRY_GEOMETRY_CHANNEL_CORRELATIONS_CSV
    marginal_csv = output_dir / REGISTRY_GEOMETRY_MARGINAL_CONTRIBUTION_CSV
    report_md = output_dir / REGISTRY_GEOMETRY_REPORT_MD

    with summary_json.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    _write_csv(profiles_csv, PROFILE_COLUMNS, payload["profiles"])
    _write_csv(cofailure_csv, COFAILURE_COLUMNS, payload["cofailure_matrix"])
    _write_csv(
        correlations_csv,
        CORRELATION_COLUMNS,
        payload["channel_correlations"],
    )
    _write_csv(
        marginal_csv,
        MARGINAL_COLUMNS,
        payload["marginal_contribution"],
    )
    report_md.write_text(
        _render_report_md(payload, summary_path=summary_path.resolve()),
        encoding="utf-8",
    )

    return RegistryGeometryReportResult(
        output_dir=output_dir,
        summary_json=summary_json,
        profiles_csv=profiles_csv,
        cofailure_matrix_csv=cofailure_csv,
        channel_correlations_csv=correlations_csv,
        marginal_contribution_csv=marginal_csv,
        report_md=report_md,
    )
