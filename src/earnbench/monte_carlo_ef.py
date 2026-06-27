"""Monte Carlo simulation of the EF@Π estimator under known generative rates."""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path

MONTE_CARLO_SUMMARY_JSON = "monte_carlo_summary.json"
MONTE_CARLO_METRICS_CSV = "monte_carlo_metrics.csv"

DEFAULT_SIMULATION_DRAWS = 10_000
SIMULATION_SEED = 0

METRIC_COLUMNS = (
    "metric_name",
    "simulated_mean",
    "analytic_expectation",
    "bias",
    "simulated_std",
    "simulation_draws",
    "simulation_seed",
)


@dataclass(frozen=True, slots=True)
class MonteCarloSimulationConfig:
    instance_count: int = 100
    pi_count: int = 3
    survival_probability: float = 0.8
    invalid_probability: float = 0.0
    simulation_draws: int = DEFAULT_SIMULATION_DRAWS
    simulation_seed: int = SIMULATION_SEED


@dataclass(frozen=True, slots=True)
class MonteCarloEfResult:
    output_dir: Path
    summary_json: Path
    metrics_csv: Path


def _simulate_instance_ef(
    *,
    pi_count: int,
    survival_probability: float,
    invalid_probability: float,
    rng: random.Random,
) -> tuple[float | None, bool]:
    """Return (ef, is_defined) for one synthetic instance."""
    successes = 0
    valid = 0
    for _ in range(pi_count):
        if rng.random() < invalid_probability:
            continue
        valid += 1
        if rng.random() < survival_probability:
            successes += 1
    if valid == 0:
        return None, False
    return successes / valid, True


def _analytic_ef_expectation(
    *,
    survival_probability: float,
    invalid_probability: float,
) -> float | None:
    if invalid_probability >= 1.0:
        return None
    return survival_probability


def _analytic_undefined_rate(*, invalid_probability: float, pi_count: int) -> float:
    if invalid_probability <= 0.0:
        return 0.0
    return 1.0 - (1.0 - invalid_probability) ** pi_count


def analyze_monte_carlo_ef(config: MonteCarloSimulationConfig) -> dict[str, object]:
    """Run Monte Carlo draws and compare to analytic EF expectations."""
    if config.instance_count < 1:
        msg = "instance_count must be >= 1"
        raise ValueError(msg)
    if config.pi_count < 1:
        msg = "pi_count must be >= 1"
        raise ValueError(msg)
    if config.simulation_draws < 1:
        msg = "simulation_draws must be >= 1"
        raise ValueError(msg)
    if not 0.0 <= config.survival_probability <= 1.0:
        msg = "survival_probability must be in [0, 1]"
        raise ValueError(msg)
    if not 0.0 <= config.invalid_probability < 1.0:
        msg = "invalid_probability must be in [0, 1)"
        raise ValueError(msg)

    rng = random.Random(config.simulation_seed)
    dataset_ef_means: list[float] = []
    undefined_rates: list[float] = []

    for _ in range(config.simulation_draws):
        instance_efs: list[float] = []
        undefined_count = 0
        for _instance in range(config.instance_count):
            ef, defined = _simulate_instance_ef(
                pi_count=config.pi_count,
                survival_probability=config.survival_probability,
                invalid_probability=config.invalid_probability,
                rng=rng,
            )
            if not defined or ef is None:
                undefined_count += 1
            else:
                instance_efs.append(ef)
        if instance_efs:
            dataset_ef_means.append(sum(instance_efs) / len(instance_efs))
        undefined_rates.append(undefined_count / config.instance_count)

    analytic_ef = _analytic_ef_expectation(
        survival_probability=config.survival_probability,
        invalid_probability=config.invalid_probability,
    )
    analytic_undefined = _analytic_undefined_rate(
        invalid_probability=config.invalid_probability,
        pi_count=config.pi_count,
    )

    simulated_ef_mean = (
        sum(dataset_ef_means) / len(dataset_ef_means) if dataset_ef_means else None
    )
    simulated_ef_std = (
        (
            sum((value - simulated_ef_mean) ** 2 for value in dataset_ef_means)
            / len(dataset_ef_means)
        )
        ** 0.5
        if dataset_ef_means and simulated_ef_mean is not None
        else None
    )
    simulated_undefined_mean = (
        sum(undefined_rates) / len(undefined_rates) if undefined_rates else None
    )

    bias = (
        simulated_ef_mean - analytic_ef
        if simulated_ef_mean is not None and analytic_ef is not None
        else None
    )
    undefined_bias = (
        simulated_undefined_mean - analytic_undefined
        if simulated_undefined_mean is not None
        else None
    )

    metrics = [
        {
            "metric_name": "ef_mean",
            "simulated_mean": simulated_ef_mean,
            "analytic_expectation": analytic_ef,
            "bias": bias,
            "simulated_std": simulated_ef_std,
            "simulation_draws": config.simulation_draws,
            "simulation_seed": config.simulation_seed,
        },
        {
            "metric_name": "undefined_rate",
            "simulated_mean": simulated_undefined_mean,
            "analytic_expectation": analytic_undefined,
            "bias": undefined_bias,
            "simulated_std": None,
            "simulation_draws": config.simulation_draws,
            "simulation_seed": config.simulation_seed,
        },
    ]

    return {
        "schema_version": "earnbench.monte_carlo_ef.v1",
        "config": {
            "instance_count": config.instance_count,
            "pi_count": config.pi_count,
            "survival_probability": config.survival_probability,
            "invalid_probability": config.invalid_probability,
            "simulation_draws": config.simulation_draws,
            "simulation_seed": config.simulation_seed,
        },
        "metrics": metrics,
        "pass_criteria": {
            "ef_bias_abs_max": 0.02,
            "undefined_bias_abs_max": 0.01,
        },
    }


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, object]]) -> None:
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


def generate_monte_carlo_ef_report(
    output_dir: Path,
    config: MonteCarloSimulationConfig,
) -> MonteCarloEfResult:
    """Write Monte Carlo EF simulation artifacts."""
    payload = analyze_monte_carlo_ef(config)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / MONTE_CARLO_SUMMARY_JSON
    csv_path = output_dir / MONTE_CARLO_METRICS_CSV

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

    _write_csv(csv_path, METRIC_COLUMNS, payload["metrics"])  # type: ignore[arg-type]

    return MonteCarloEfResult(
        output_dir=output_dir,
        summary_json=json_path,
        metrics_csv=csv_path,
    )
