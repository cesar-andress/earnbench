"""Tests for Phase C′ policy variance decomposition."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from earnbench.cli import main
from earnbench.policy_variance import (
    POLICY_VARIANCE_BOOTSTRAP_JSON,
    POLICY_VARIANCE_BY_AGENT_CSV,
    POLICY_VARIANCE_BY_AGENT_INSTANCE_CSV,
    POLICY_VARIANCE_COMPONENTS_CSV,
    POLICY_VARIANCE_PAIRWISE_FLIPS_CSV,
    POLICY_VARIANCE_REPORT_MD,
    analyze_policy_variance,
    generate_policy_variance_report,
    load_policy_variance_results,
)
from earnbench.reports import EarnedFractionStatus

HEADER = (
    "agent",
    "model",
    "provider",
    "instance_id",
    "replicate",
    "y0",
    "ef_pi",
    "ef_status",
    "failed_mechanisms",
    "invalid_pi_count",
    "status",
)


def _write_csv(path: Path, rows: list[tuple[str, ...]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)


def _write_deterministic_dataset(path: Path) -> None:
    rows = []
    for instance in ("i1", "i2"):
        rows.extend(
            [
                ("alpha", "m1", "p1", instance, "1", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok"),
                ("alpha", "m1", "p1", instance, "2", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok"),
            ]
        )
    _write_csv(path, rows)


def _write_stochastic_dataset(path: Path) -> None:
    _write_csv(
        path,
        [
            ("alpha", "m1", "p1", "i1", "1", "1", "1.0", "defined", "", "0", "ok"),
            ("alpha", "m1", "p1", "i1", "2", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok"),
            ("alpha", "m1", "p1", "i2", "1", "1", "1.0", "defined", "", "0", "ok"),
            ("alpha", "m1", "p1", "i2", "2", "1", "1.0", "defined", "", "0", "ok"),
        ],
    )


def test_deterministic_toy_within_cell_variance_is_zero(tmp_path: Path) -> None:
    path = tmp_path / "deterministic.csv"
    _write_deterministic_dataset(path)
    payload = analyze_policy_variance(load_policy_variance_results(path), bootstrap_draws=50)
    alpha = next(row for row in payload["by_agent"] if row["agent"] == "alpha")
    assert alpha["within_cell_variance"] == pytest.approx(0.0)


def test_stochastic_toy_within_cell_variance_is_positive(tmp_path: Path) -> None:
    path = tmp_path / "stochastic.csv"
    _write_stochastic_dataset(path)
    payload = analyze_policy_variance(load_policy_variance_results(path), bootstrap_draws=50)
    alpha = next(row for row in payload["by_agent"] if row["agent"] == "alpha")
    assert alpha["within_cell_variance"] > 0.0


def test_between_instance_variance_detected(tmp_path: Path) -> None:
    path = tmp_path / "between_instance.csv"
    _write_csv(
        path,
        [
            ("alpha", "m1", "p1", "easy", "1", "1", "1.0", "defined", "", "0", "ok"),
            ("alpha", "m1", "p1", "hard", "1", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok"),
        ],
    )
    payload = analyze_policy_variance(load_policy_variance_results(path), bootstrap_draws=20)
    alpha = next(row for row in payload["by_agent"] if row["agent"] == "alpha")
    assert alpha["between_instance_variance"] > 0.0


def test_same_pass_rate_different_earned_pass_rate(tmp_path: Path) -> None:
    path = tmp_path / "same_pass.csv"
    _write_csv(
        path,
        [
            ("alpha", "m1", "p1", "i1", "1", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok"),
            ("alpha", "m1", "p1", "i2", "1", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok"),
            ("beta", "m2", "p1", "i1", "1", "1", "1.0", "defined", "", "0", "ok"),
            ("beta", "m2", "p1", "i2", "1", "1", "1.0", "defined", "", "0", "ok"),
        ],
    )
    payload = analyze_policy_variance(load_policy_variance_results(path), bootstrap_draws=20)
    alpha = next(row for row in payload["by_agent"] if row["agent"] == "alpha")
    beta = next(row for row in payload["by_agent"] if row["agent"] == "beta")
    assert alpha["nominal_pass_rate"] == pytest.approx(beta["nominal_pass_rate"])
    assert alpha["earned_pass_rate"] == pytest.approx(0.0)
    assert beta["earned_pass_rate"] == pytest.approx(1.0)


def test_undefined_handling(tmp_path: Path) -> None:
    path = tmp_path / "undefined.csv"
    _write_csv(
        path,
        [
            ("solo", "m1", "p1", "i1", "1", "1", "", EarnedFractionStatus.UNDEFINED.value, "", "1", "invalid"),
            ("solo", "m1", "p1", "i1", "2", "1", "1.0", "defined", "", "0", "ok"),
        ],
    )
    payload = analyze_policy_variance(load_policy_variance_results(path), bootstrap_draws=20)
    solo = payload["by_agent"][0]
    assert solo["undefined_on_success_count"] == 1
    assert solo["mean_ef_conditional_on_pass"] == pytest.approx(1.0)


def test_bootstrap_reproducibility(tmp_path: Path) -> None:
    path = tmp_path / "stochastic.csv"
    _write_stochastic_dataset(path)
    rows = load_policy_variance_results(path)
    first = analyze_policy_variance(rows, bootstrap_draws=100, bootstrap_seed=7)
    second = analyze_policy_variance(rows, bootstrap_draws=100, bootstrap_seed=7)
    assert first["bootstrap"] == second["bootstrap"]


def test_generate_report_writes_artifacts(tmp_path: Path) -> None:
    path = tmp_path / "agent_results.csv"
    _write_stochastic_dataset(path)
    out = tmp_path / "out"
    result = generate_policy_variance_report(path, out, bootstrap_draws=50, bootstrap_seed=0)
    for artifact in (
        result.by_agent_csv,
        result.by_agent_instance_csv,
        result.components_csv,
        result.bootstrap_json,
        result.pairwise_flips_csv,
        result.report_md,
    ):
        assert artifact.is_file()
    assert result.by_agent_csv.name == POLICY_VARIANCE_BY_AGENT_CSV
    bootstrap = json.loads(result.bootstrap_json.read_text(encoding="utf-8"))
    assert bootstrap["schema_version"] == "earnbench.policy_variance_bootstrap.v1"


def test_frontier_written_when_difficulty_bin_present(tmp_path: Path) -> None:
    path = tmp_path / "frontier.csv"
    header = HEADER + ("difficulty_bin",)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(
            [
                ("alpha", "m1", "p1", "i1", "1", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok", "easy"),
                ("alpha", "m1", "p1", "i2", "1", "1", "1.0", "defined", "", "0", "ok", "hard"),
            ]
        )
    result = generate_policy_variance_report(path, tmp_path / "out", bootstrap_draws=20)
    assert result.exploitation_frontier_csv is not None
    assert result.exploitation_frontier_csv.is_file()


def test_cli_report_policy_variance(capsys, tmp_path: Path) -> None:
    path = tmp_path / "agent_results.csv"
    _write_stochastic_dataset(path)
    exit_code = main(
        [
            "report",
            "policy-variance",
            "--agent-results",
            str(path),
            "--output",
            str(tmp_path / "cli_out"),
            "--bootstrap",
            "50",
            "--seed",
            "0",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["report_md"]).is_file()
