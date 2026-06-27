"""Instance-bootstrap uncertainty for Phase A/B summary metrics."""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.phase_a_batch import SUMMARY_COLUMNS as PHASE_A_SUMMARY_COLUMNS
from earnbench.reports import EarnedFractionStatus

BOOTSTRAP_METRICS_CSV = "bootstrap_metrics.csv"
BOOTSTRAP_UNCERTAINTY_JSON = "bootstrap_uncertainty.json"

DEFAULT_BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 0
CI_LOW_QUANTILE = 0.025
CI_HIGH_QUANTILE = 0.975

METRIC_NAMES = (
    "ef_mean",
    "invalid_pi_rate_mean",
    "ef_sensitivity_gap_mean",
    "false_unearned_rate",
    "retained_rate",
)

BOOTSTRAP_METRIC_COLUMNS = (
    "metric_name",
    "point_estimate",
    "ci_low",
    "ci_high",
    "bootstrap_draws",
    "bootstrap_seed",
    "sample_size",
)


@dataclass(frozen=True, slots=True)
class BootstrapUncertaintyResult:
    output_dir: Path
    metrics_csv: Path
    report_json: Path


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        msg = "cannot compute quantile of empty sample"
        raise ValueError(msg)
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _ci(samples: list[float]) -> tuple[float, float]:
    ordered = sorted(samples)
    return (
        _quantile(ordered, CI_LOW_QUANTILE),
        _quantile(ordered, CI_HIGH_QUANTILE),
    )


def load_phase_summary_rows(path: Path) -> list[dict[str, Any]]:
    """Load ``summary.csv`` rows from a Phase A or Phase B batch directory."""
    resolved = path.resolve()
    summary_path = resolved if resolved.name == "summary.csv" else resolved / "summary.csv"
    if not summary_path.is_file():
        msg = f"summary.csv not found: {summary_path}"
        raise FileNotFoundError(msg)

    with summary_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            msg = f"{summary_path}: missing header row"
            raise ValueError(msg)
        rows = [dict(row) for row in reader]
    if not rows:
        msg = f"{summary_path}: contains no data rows"
        raise ValueError(msg)
    return rows


def _metric_values(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    total = len(rows)
    retained_rows = [row for row in rows if _as_bool(row.get("retained"))]
    retained_count = len(retained_rows)

    ef_defined = [
        row
        for row in retained_rows
        if str(row.get("ef_status")) == EarnedFractionStatus.DEFINED.value
        and row.get("ef_pi") not in ("", None)
    ]
    ef_values = [float(row["ef_pi"]) for row in ef_defined]

    invalid_rates = [
        float(row["invalid_pi_rate"])
        for row in retained_rows
        if row.get("invalid_pi_rate") not in ("", None)
    ]
    gap_values = [
        float(row["ef_sensitivity_gap"])
        for row in retained_rows
        if row.get("ef_sensitivity_gap") not in ("", None)
    ]
    false_unearned_count = sum(
        1 for row in rows if _as_bool(row.get("false_unearned"))
    )

    return {
        "ef_mean": (sum(ef_values) / len(ef_values) if ef_values else None),
        "invalid_pi_rate_mean": (
            sum(invalid_rates) / len(invalid_rates) if invalid_rates else None
        ),
        "ef_sensitivity_gap_mean": (
            sum(gap_values) / len(gap_values) if gap_values else None
        ),
        "false_unearned_rate": (
            false_unearned_count / total if total else None
        ),
        "retained_rate": retained_count / total if total else None,
    }


def _resample_metric(
    rows: list[dict[str, Any]],
    metric_name: str,
) -> float | None:
    if not rows:
        return None
    sample = [rows[random.randrange(len(rows))] for _ in range(len(rows))]
    return _metric_values(sample)[metric_name]


def analyze_bootstrap_uncertainty(
    rows: list[dict[str, Any]],
    *,
    bootstrap_draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Compute instance-bootstrap CIs for major summary metrics."""
    if bootstrap_draws < 1:
        msg = f"bootstrap_draws must be >= 1, got {bootstrap_draws}"
        raise ValueError(msg)

    point_estimates = _metric_values(rows)
    random.seed(bootstrap_seed)

    metric_rows: list[dict[str, Any]] = []
    for metric_name in METRIC_NAMES:
        point = point_estimates[metric_name]
        if point is None:
            metric_rows.append(
                {
                    "metric_name": metric_name,
                    "point_estimate": "",
                    "ci_low": "",
                    "ci_high": "",
                    "bootstrap_draws": bootstrap_draws,
                    "bootstrap_seed": bootstrap_seed,
                    "sample_size": len(rows),
                    "status": "undefined",
                }
            )
            continue

        samples: list[float] = []
        for _ in range(bootstrap_draws):
            value = _resample_metric(rows, metric_name)
            if value is not None:
                samples.append(value)

        ci_low, ci_high = _ci(samples) if samples else (None, None)
        metric_rows.append(
            {
                "metric_name": metric_name,
                "point_estimate": point,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "bootstrap_draws": bootstrap_draws,
                "bootstrap_seed": bootstrap_seed,
                "sample_size": len(rows),
                "status": "defined",
            }
        )

    return {
        "schema_version": "earnbench.bootstrap_uncertainty.v1",
        "instance_count": len(rows),
        "bootstrap_draws": bootstrap_draws,
        "bootstrap_seed": bootstrap_seed,
        "summary_source_columns": list(PHASE_A_SUMMARY_COLUMNS),
        "metrics": metric_rows,
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
                else:
                    formatted[column] = value
            writer.writerow(formatted)


def generate_bootstrap_uncertainty_report(
    summary_path: Path,
    output_dir: Path,
    *,
    bootstrap_draws: int = DEFAULT_BOOTSTRAP_DRAWS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> BootstrapUncertaintyResult:
    """Load summary.csv and write bootstrap uncertainty artifacts."""
    rows = load_phase_summary_rows(summary_path)
    payload = analyze_bootstrap_uncertainty(
        rows,
        bootstrap_draws=bootstrap_draws,
        bootstrap_seed=bootstrap_seed,
    )

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics_path = output_dir / BOOTSTRAP_METRICS_CSV
    json_path = output_dir / BOOTSTRAP_UNCERTAINTY_JSON

    csv_rows = []
    for row in payload["metrics"]:
        csv_rows.append(
            {
                "metric_name": row["metric_name"],
                "point_estimate": row["point_estimate"],
                "ci_low": row["ci_low"],
                "ci_high": row["ci_high"],
                "bootstrap_draws": row["bootstrap_draws"],
                "bootstrap_seed": row["bootstrap_seed"],
                "sample_size": row["sample_size"],
            }
        )
    _write_csv(metrics_path, BOOTSTRAP_METRIC_COLUMNS, csv_rows)

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return BootstrapUncertaintyResult(
        output_dir=output_dir,
        metrics_csv=metrics_path,
        report_json=json_path,
    )
