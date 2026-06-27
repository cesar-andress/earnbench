"""Post-hoc Π ablation sensitivity analysis from per-π outcome columns."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PI_ABLATION_CSV = "pi_ablation.csv"
PI_ABLATION_JSON = "pi_ablation.json"

PI_COLUMNS = (
    ("pi_vtest.v1", "y_vtest", "pi_vtest_status"),
    ("pi_verif.v1", "y_verif", "pi_verif_status"),
    ("pi_env.v1", "y_env", "pi_env_status"),
)

ABLATION_COLUMNS = (
    "ablated_pi",
    "ef_mean",
    "ef_defined_count",
    "instance_count",
    "delta_from_full_ef_mean",
    "full_ef_mean",
)


@dataclass(frozen=True, slots=True)
class PiAblationResult:
    output_dir: Path
    ablation_csv: Path
    report_json: Path


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


def _ef_from_pi_outcomes(
    pi_outcomes: list[tuple[str, bool | None, str]],
    *,
    exclude_pi: str | None = None,
) -> float | None:
    usable: list[bool] = []
    for pi_id, outcome, status in pi_outcomes:
        if exclude_pi is not None and pi_id == exclude_pi:
            continue
        if status.strip().lower() == "invalid":
            continue
        if outcome is None:
            continue
        usable.append(outcome)
    if not usable:
        return None
    return sum(1 for item in usable if item) / len(usable)


def _row_pi_outcomes(row: dict[str, Any]) -> list[tuple[str, bool | None, str]]:
    outcomes: list[tuple[str, bool | None, str]] = []
    for pi_id, outcome_key, status_key in PI_COLUMNS:
        outcome = _parse_optional_bool(row.get(outcome_key))
        status = str(row.get(status_key, "")).strip()
        outcomes.append((pi_id, outcome, status))
    return outcomes


def _full_ef_for_row(row: dict[str, Any]) -> float | None:
    if row.get("ef_pi") not in ("", None):
        return float(row["ef_pi"])
    return _ef_from_pi_outcomes(_row_pi_outcomes(row))


def analyze_pi_ablation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute EF means with each MVP π removed from the denominator."""
    eligible = [
        row
        for row in rows
        if _as_bool(row.get("retained")) and _as_bool(row.get("y0"))
    ]

    full_values = [value for row in eligible if (value := _full_ef_for_row(row)) is not None]
    full_ef_mean = sum(full_values) / len(full_values) if full_values else None

    ablation_rows: list[dict[str, Any]] = []
    for pi_id, _, _ in PI_COLUMNS:
        ablated_values: list[float] = []
        for row in eligible:
            ef = _ef_from_pi_outcomes(_row_pi_outcomes(row), exclude_pi=pi_id)
            if ef is not None:
                ablated_values.append(ef)
        ablated_mean = (
            sum(ablated_values) / len(ablated_values) if ablated_values else None
        )
        delta = (
            ablated_mean - full_ef_mean
            if ablated_mean is not None and full_ef_mean is not None
            else None
        )
        ablation_rows.append(
            {
                "ablated_pi": pi_id,
                "ef_mean": ablated_mean,
                "ef_defined_count": len(ablated_values),
                "instance_count": len(eligible),
                "delta_from_full_ef_mean": delta,
                "full_ef_mean": full_ef_mean,
            }
        )

    return {
        "schema_version": "earnbench.pi_ablation.v1",
        "eligible_instance_count": len(eligible),
        "full_ef_mean": full_ef_mean,
        "ablations": ablation_rows,
        "note": (
            "Post-hoc sensitivity analysis only; does not modify EF@Π semantics "
            "or MVP registry membership."
        ),
    }


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            formatted = {}
            for column in columns:
                value = row.get(column, "")
                if isinstance(value, float):
                    formatted[column] = f"{value:.6f}"
                elif value is None:
                    formatted[column] = ""
                else:
                    formatted[column] = value
            writer.writerow(formatted)


def generate_pi_ablation_report(
    summary_path: Path,
    output_dir: Path,
) -> PiAblationResult:
    """Load summary.csv and write Π ablation artifacts."""
    from earnbench.bootstrap_uncertainty import load_phase_summary_rows

    rows = load_phase_summary_rows(summary_path)
    payload = analyze_pi_ablation(rows)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / PI_ABLATION_CSV
    json_path = output_dir / PI_ABLATION_JSON

    _write_csv(csv_path, ABLATION_COLUMNS, payload["ablations"])

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return PiAblationResult(
        output_dir=output_dir,
        ablation_csv=csv_path,
        report_json=json_path,
    )
