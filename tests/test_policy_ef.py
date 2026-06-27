"""Tests for policy-level Earned Fraction analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from earnbench.cli import main
from earnbench.policy_ef import (
    POLICY_EF_BOOTSTRAP_JSON,
    POLICY_EF_BY_AGENT_CSV,
    POLICY_EF_REPORT_MD,
    POLICY_EF_VARIANCE_CSV,
    analyze_policy_ef,
    generate_policy_ef_report,
    load_policy_agent_results,
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
    for instance in ("i1", "i2", "i3", "i4"):
        rows.extend(
            [
                ("alpha", "m1", "p1", instance, "1", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok"),
                ("alpha", "m1", "p1", instance, "2", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok"),
                ("beta", "m2", "p1", instance, "1", "1", "1.0", "defined", "", "0", "ok"),
                ("beta", "m2", "p1", instance, "2", "0", "", "undefined", "", "0", "fail"),
            ]
        )
    _write_csv(path, rows)


def _write_stochastic_dataset(path: Path) -> None:
    rows = [
        ("alpha", "m1", "p1", "i1", "1", "1", "1.0", "defined", "", "0", "ok"),
        ("alpha", "m1", "p1", "i1", "2", "1", "0.0", "defined", "visible_test_overfitting", "0", "ok"),
        ("alpha", "m1", "p1", "i2", "1", "1", "1.0", "defined", "", "0", "ok"),
        ("alpha", "m1", "p1", "i2", "2", "1", "1.0", "defined", "", "0", "ok"),
        ("beta", "m2", "p1", "i1", "1", "1", "1.0", "defined", "", "0", "ok"),
        ("beta", "m2", "p1", "i1", "2", "1", "1.0", "defined", "", "0", "ok"),
        ("beta", "m2", "p1", "i2", "1", "1", "1.0", "defined", "", "0", "ok"),
        ("beta", "m2", "p1", "i2", "2", "1", "1.0", "defined", "", "0", "ok"),
    ]
    _write_csv(path, rows)


def test_deterministic_toy_dataset_has_zero_inter_replicate_variance(tmp_path: Path) -> None:
    path = tmp_path / "deterministic.csv"
    _write_deterministic_dataset(path)
    payload = analyze_policy_ef(load_policy_agent_results(path), bootstrap_draws=100)

    alpha = next(row for row in payload["by_agent"] if row["agent"] == "alpha")
    beta = next(row for row in payload["by_agent"] if row["agent"] == "beta")

    assert alpha["nominal_pass_rate"] == pytest.approx(1.0)
    assert alpha["earned_pass_rate"] == pytest.approx(0.0)
    assert alpha["policy_ef"] == pytest.approx(0.0)
    assert alpha["inter_replicate_variance"] == pytest.approx(0.0)
    assert beta["nominal_pass_rate"] == pytest.approx(0.5)
    assert beta["earned_pass_rate"] == pytest.approx(0.5)
    assert payload["ers"]["pairwise_flip_count"] == 1


def test_stochastic_toy_dataset_has_nonzero_inter_replicate_variance(tmp_path: Path) -> None:
    path = tmp_path / "stochastic.csv"
    _write_stochastic_dataset(path)
    payload = analyze_policy_ef(load_policy_agent_results(path), bootstrap_draws=100)

    alpha = next(row for row in payload["by_agent"] if row["agent"] == "alpha")
    assert alpha["inter_replicate_variance"] > 0.0
    assert alpha["earned_pass_rate"] == pytest.approx(0.75)


def test_pairwise_flip_probability_from_bootstrap(tmp_path: Path) -> None:
    path = tmp_path / "deterministic.csv"
    _write_deterministic_dataset(path)
    payload = analyze_policy_ef(
        load_policy_agent_results(path),
        bootstrap_draws=500,
        bootstrap_seed=0,
    )
    pair_stats = payload["bootstrap"]["ers"]["pairwise_flip_probability_by_pair"]
    assert "alpha|beta" in pair_stats
    assert pair_stats["alpha|beta"]["probability"] == pytest.approx(1.0)


def test_bootstrap_reproducibility(tmp_path: Path) -> None:
    path = tmp_path / "deterministic.csv"
    _write_deterministic_dataset(path)
    rows = load_policy_agent_results(path)
    first = analyze_policy_ef(rows, bootstrap_draws=200, bootstrap_seed=42)
    second = analyze_policy_ef(rows, bootstrap_draws=200, bootstrap_seed=42)
    assert first["bootstrap"] == second["bootstrap"]


def test_undefined_attempts_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "undefined.csv"
    _write_csv(
        path,
        [
            ("solo", "m1", "p1", "i1", "1", "1", "", EarnedFractionStatus.UNDEFINED.value, "", "1", "invalid"),
            ("solo", "m1", "p1", "i1", "2", "1", "1.0", "defined", "", "0", "ok"),
        ],
    )
    payload = analyze_policy_ef(load_policy_agent_results(path), bootstrap_draws=50)
    solo = payload["by_agent"][0]
    assert solo["undefined_ef_on_success_count"] == 1
    assert solo["policy_ef"] == pytest.approx(1.0)


def test_duplicate_agent_instance_replicate_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    _write_csv(
        path,
        [
            ("alpha", "m1", "p1", "i1", "1", "1", "1.0", "defined", "", "0", "ok"),
            ("alpha", "m1", "p1", "i1", "1", "1", "1.0", "defined", "", "0", "ok"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate row"):
        load_policy_agent_results(path)


def test_generate_policy_ef_report_writes_artifacts(tmp_path: Path) -> None:
    input_csv = tmp_path / "agent_results.csv"
    _write_deterministic_dataset(input_csv)
    output_dir = tmp_path / "policy_ef"
    result = generate_policy_ef_report(
        input_csv,
        output_dir,
        bootstrap_draws=100,
    )

    assert result.by_agent_csv.name == POLICY_EF_BY_AGENT_CSV
    assert result.variance_csv.name == POLICY_EF_VARIANCE_CSV
    assert result.bootstrap_json.name == POLICY_EF_BOOTSTRAP_JSON
    assert result.report_md.name == POLICY_EF_REPORT_MD
    for artifact in (
        result.by_agent_csv,
        result.variance_csv,
        result.bootstrap_json,
        result.report_md,
    ):
        assert artifact.is_file()

    bootstrap = json.loads(result.bootstrap_json.read_text(encoding="utf-8"))
    assert bootstrap["schema_version"] == "earnbench.policy_ef_bootstrap.v1"


def test_cli_report_policy_ef(capsys, tmp_path: Path) -> None:
    input_csv = tmp_path / "agent_results.csv"
    _write_deterministic_dataset(input_csv)
    out = tmp_path / "out"
    exit_code = main(
        [
            "report",
            "policy-ef",
            "--agent-results",
            str(input_csv),
            "--output",
            str(out),
            "--bootstrap",
            "100",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["report_md"]).is_file()
