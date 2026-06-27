"""Tests for Earned Rank Stability (ERS) analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from earnbench.rank_stability import (
    CHANNEL_RANK_CONTRIBUTIONS_CSV,
    PAIRWISE_FLIPS_CSV,
    RANK_STABILITY_JSON,
    RANK_STABILITY_REPORT_MD,
    RANK_STABILITY_SUMMARY_CSV,
    analyze_rank_stability,
    generate_rank_stability_report,
    kendall_tau,
    load_agent_results,
    render_rank_stability_report,
    spearman_rank_correlation,
)

SYNTHETIC_HEADER = (
    "agent",
    "instance_id",
    "y0",
    "ef_exclude_invalid",
    "ef_invalid_as_fail",
    "failed_mechanisms",
    "invalid_pi_count",
)


def _write_synthetic_leaderboard(path: Path) -> None:
    rows = [
        ("alpha", "inst-1", "1", "1.0", "1.0", "", "0"),
        ("alpha", "inst-2", "1", "0.0", "0.0", "visible_test_overfitting", "0"),
        ("alpha", "inst-3", "1", "0.0", "0.0", "verifier_tampering", "0"),
        ("beta", "inst-1", "1", "1.0", "1.0", "", "0"),
        ("beta", "inst-2", "1", "0.5", "0.0", "visible_test_overfitting", "0"),
        ("beta", "inst-3", "0", "", "", "", "0"),
        ("gamma", "inst-1", "0", "", "", "", "0"),
        ("gamma", "inst-2", "1", "1.0", "1.0", "", "0"),
        ("gamma", "inst-3", "1", "1.0", "1.0", "", "0"),
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SYNTHETIC_HEADER)
        writer.writerows(rows)


def _summary_by_agent(payload: dict) -> dict[str, dict]:
    return {row["agent"]: row for row in payload["summary"]}


def test_synthetic_leaderboard_nominal_and_earned_rankings_differ(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agent_results.csv"
    _write_synthetic_leaderboard(path)
    rows = load_agent_results(path)
    payload = analyze_rank_stability(rows, bootstrap_draws=500, bootstrap_seed=0)
    summary = _summary_by_agent(payload)

    assert summary["alpha"]["nominal_pass_rate"] == pytest.approx(1.0)
    assert summary["alpha"]["earned_pass_rate_exclude_invalid"] == pytest.approx(
        1 / 3,
    )
    assert summary["beta"]["nominal_pass_rate"] == pytest.approx(2 / 3)
    assert summary["gamma"]["nominal_pass_rate"] == pytest.approx(2 / 3)
    assert summary["gamma"]["earned_pass_rate_exclude_invalid"] == pytest.approx(
        2 / 3,
    )

    assert summary["alpha"]["nominal_rank"] == pytest.approx(1.0)
    assert summary["alpha"]["earned_rank"] == pytest.approx(3.0)
    assert summary["gamma"]["earned_rank"] == pytest.approx(1.0)
    assert summary["gamma"]["nominal_rank"] == pytest.approx(2.5)

    assert payload["ers"]["spearman"] is not None
    assert payload["ers"]["spearman"] < 1.0
    assert payload["ers"]["pairwise_flip_count"] == 2
    assert payload["ers"]["kendall_tau"] is not None

    flips = {
        (row["agent_a"], row["agent_b"]): row["flip"]
        for row in payload["pairwise_flips"]
    }
    assert flips[("alpha", "beta")] == 1
    assert flips[("alpha", "gamma")] == 1
    assert flips[("beta", "gamma")] == 0


def test_channel_lost_credit_attributes_failures(tmp_path: Path) -> None:
    path = tmp_path / "agent_results.csv"
    _write_synthetic_leaderboard(path)
    rows = load_agent_results(path)
    payload = analyze_rank_stability(rows, bootstrap_draws=100, bootstrap_seed=0)
    contributions = {
        (row["agent"], row["channel"]): row["lost_credit"]
        for row in payload["channel_rank_contributions"]
    }

    assert contributions[("alpha", "visible_test_overfitting")] == pytest.approx(
        1 / 3,
    )
    assert contributions[("alpha", "verifier_tampering")] == pytest.approx(1 / 3)
    assert contributions[("beta", "visible_test_overfitting")] == pytest.approx(
        0.5 / 3,
    )


def test_generate_rank_stability_report_writes_artifacts(tmp_path: Path) -> None:
    input_csv = tmp_path / "agent_results.csv"
    _write_synthetic_leaderboard(input_csv)
    output_dir = tmp_path / "ers"
    result = generate_rank_stability_report(
        input_csv,
        output_dir,
        bootstrap_draws=200,
    )

    assert result.summary_csv.name == RANK_STABILITY_SUMMARY_CSV
    assert result.pairwise_flips_csv.name == PAIRWISE_FLIPS_CSV
    assert result.channel_rank_contributions_csv.name == CHANNEL_RANK_CONTRIBUTIONS_CSV
    assert result.report_md.name == RANK_STABILITY_REPORT_MD
    assert result.report_json.name == RANK_STABILITY_JSON

    for artifact in (
        result.summary_csv,
        result.pairwise_flips_csv,
        result.channel_rank_contributions_csv,
        result.report_md,
        result.report_json,
    ):
        assert artifact.is_file()

    payload = json.loads(result.report_json.read_text(encoding="utf-8"))
    report_text = result.report_md.read_text(encoding="utf-8")
    assert report_text == render_rank_stability_report(payload)
    assert payload["agent_count"] == 3
    assert payload["instance_count"] == 3


def test_load_agent_results_requires_columns(tmp_path: Path) -> None:
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("agent,instance_id\na,i1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        load_agent_results(bad_csv)


def test_spearman_and_kendall_helpers() -> None:
    assert spearman_rank_correlation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(
        1.0,
    )
    assert kendall_tau([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)
