"""Tests for Monte Carlo EF estimator simulation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.monte_carlo_ef import (
    MonteCarloSimulationConfig,
    analyze_monte_carlo_ef,
    generate_monte_carlo_ef_report,
)


def test_monte_carlo_matches_analytic_expectation() -> None:
    config = MonteCarloSimulationConfig(
        instance_count=200,
        pi_count=3,
        survival_probability=0.8,
        invalid_probability=0.0,
        simulation_draws=500,
        simulation_seed=0,
    )
    payload = analyze_monte_carlo_ef(config)
    ef_metric = payload["metrics"][0]
    assert ef_metric["analytic_expectation"] == pytest.approx(0.8)
    assert ef_metric["bias"] == pytest.approx(0.0, abs=0.02)


def test_generate_monte_carlo_ef_report(tmp_path: Path) -> None:
    config = MonteCarloSimulationConfig(simulation_draws=100, simulation_seed=1)
    result = generate_monte_carlo_ef_report(tmp_path / "mc", config)
    payload = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "earnbench.monte_carlo_ef.v1"
    assert result.metrics_csv.is_file()
