"""Tests for bootstrap uncertainty analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from earnbench.bootstrap_uncertainty import (
    BOOTSTRAP_METRICS_CSV,
    BOOTSTRAP_UNCERTAINTY_JSON,
    analyze_bootstrap_uncertainty,
    generate_bootstrap_uncertainty_report,
    load_phase_summary_rows,
)
from earnbench.reports import EarnedFractionStatus

SUMMARY_HEADER = (
    "instance_id",
    "repo",
    "y0",
    "y_vtest",
    "y_verif",
    "y_env",
    "pi_vtest_status",
    "pi_verif_status",
    "pi_env_status",
    "valid_pi_count",
    "ef_pi",
    "ef_exclude_invalid",
    "ef_invalid_as_fail",
    "invalid_pi_count",
    "invalid_pi_rate",
    "ef_sensitivity_gap",
    "ef_status",
    "false_unearned",
    "retained",
    "exclude_reason",
    "run_id",
    "config_digest",
)


def _write_summary(path: Path, rows: list[tuple[str, ...]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SUMMARY_HEADER)
        writer.writerows(rows)


def test_analyze_bootstrap_uncertainty_deterministic() -> None:
    rows = [
        {
            "ef_pi": "1.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "invalid_pi_rate": "0.0",
            "ef_sensitivity_gap": "0.0",
            "false_unearned": "0",
            "retained": "1",
        },
        {
            "ef_pi": "0.5",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "invalid_pi_rate": "0.333333",
            "ef_sensitivity_gap": "0.1",
            "false_unearned": "0",
            "retained": "1",
        },
    ]
    first = analyze_bootstrap_uncertainty(rows, bootstrap_draws=100, bootstrap_seed=0)
    second = analyze_bootstrap_uncertainty(rows, bootstrap_draws=100, bootstrap_seed=0)
    assert first == second
    ef_metric = next(item for item in first["metrics"] if item["metric_name"] == "ef_mean")
    assert ef_metric["status"] == "defined"
    assert ef_metric["point_estimate"] == pytest.approx(0.75)


def test_generate_bootstrap_uncertainty_report(tmp_path: Path) -> None:
    summary = tmp_path / "summary.csv"
    _write_summary(
        summary,
        [
            (
                "inst-1",
                "repo/a",
                "1",
                "1",
                "1",
                "1",
                "success",
                "success",
                "success",
                "3",
                "1.0",
                "1.0",
                "1.0",
                "0",
                "0.0",
                "0.0",
                EarnedFractionStatus.DEFINED.value,
                "0",
                "1",
                "",
                "run-1",
                "digest",
            ),
        ],
    )
    out = tmp_path / "bootstrap"
    result = generate_bootstrap_uncertainty_report(
        summary,
        out,
        bootstrap_draws=50,
        bootstrap_seed=0,
    )
    assert (result.output_dir / BOOTSTRAP_METRICS_CSV).is_file()
    assert (result.output_dir / BOOTSTRAP_UNCERTAINTY_JSON).is_file()
    payload = json.loads((result.output_dir / BOOTSTRAP_UNCERTAINTY_JSON).read_text())
    assert payload["schema_version"] == "earnbench.bootstrap_uncertainty.v1"


def test_load_phase_summary_rows_from_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "phase_a"
    run_dir.mkdir()
    _write_summary(
        run_dir / "summary.csv",
        [
            (
                "inst-1",
                "repo/a",
                "1",
                "1",
                "1",
                "1",
                "success",
                "success",
                "success",
                "3",
                "1.0",
                "1.0",
                "1.0",
                "0",
                "0.0",
                "0.0",
                EarnedFractionStatus.DEFINED.value,
                "0",
                "1",
                "",
                "run-1",
                "digest",
            ),
        ],
    )
    rows = load_phase_summary_rows(run_dir)
    assert len(rows) == 1
