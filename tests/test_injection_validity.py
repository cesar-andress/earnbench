"""Tests for blinded injection validity analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from earnbench.cli import main
from earnbench.injection_validity import (
    CHANNEL_ATTRIBUTION_MATRIX_CSV,
    FALSE_EARNED_FALSE_UNEARNED_CSV,
    INJECTION_VALIDITY_REPORT_MD,
    INJECTION_VALIDITY_SUMMARY_CSV,
    INVALID_ASYMMETRY_CSV,
    analyze_injection_validity,
    generate_injection_validity_report,
    load_injection_results,
    observed_failed_pi,
)
from earnbench.injections.catalog import load_injection_catalog

FIXTURES = Path(__file__).parent / "fixtures" / "injection_validity"
RESULTS_CSV = FIXTURES / "injection_results.csv"
SPECS_DIR = FIXTURES / "specs"


def test_observed_failed_pi_maps_injected_mechanisms() -> None:
    results = load_injection_results(RESULTS_CSV)
    assert observed_failed_pi(results["BI-vtest"]) == "pi_vtest.v1"
    assert observed_failed_pi(results["BI-verif"]) == "pi_verif.v1"
    assert observed_failed_pi(results["BI-env"]) == "pi_env.v1"
    assert observed_failed_pi(results["BI-oor"]) == "none"


def test_analyze_injection_validity_synthetic_metrics() -> None:
    results = load_injection_results(RESULTS_CSV)
    specs = load_injection_catalog(SPECS_DIR)
    payload = analyze_injection_validity(results, specs)

    assert payload["metrics"]["targeted_channel_detection_rate"] == pytest.approx(1.0)
    assert payload["metrics"]["off_target_failure_rate"] == pytest.approx(0.0)
    assert payload["metrics"]["oor_no_target_failure_rate"] == pytest.approx(1.0)
    assert payload["metrics"]["false_earned_rate"] == pytest.approx(0.0)
    assert payload["metrics"]["false_unearned_rate"] == pytest.approx(0.0)
    assert payload["ef_separation"]["delta_ef_exclude_invalid"] == pytest.approx(
        1.0 - 2 / 3,
        rel=0.05,
    )
    assert payload["ef_separation"]["paired_count"] == 4

    matrix = {
        (row["injected_channel"], row["observed_failed_pi"]): row["count"]
        for row in payload["matrix_rows"]
        if row["count"]
    }
    assert matrix[("visible_test_overfitting", "pi_vtest.v1")] == 1
    assert matrix[("verifier_tampering", "pi_verif.v1")] == 1
    assert matrix[("environment_hijack", "pi_env.v1")] == 1
    assert matrix[("metadata_leakage", "none")] == 1


def test_generate_injection_validity_report_writes_artifacts(tmp_path: Path) -> None:
    result = generate_injection_validity_report(
        RESULTS_CSV,
        SPECS_DIR,
        tmp_path,
    )

    assert result.summary_csv.name == INJECTION_VALIDITY_SUMMARY_CSV
    assert result.channel_attribution_matrix_csv.name == CHANNEL_ATTRIBUTION_MATRIX_CSV
    assert (
        result.false_earned_false_unearned_csv.name == FALSE_EARNED_FALSE_UNEARNED_CSV
    )
    assert result.invalid_asymmetry_csv.name == INVALID_ASYMMETRY_CSV
    assert result.report_md.name == INJECTION_VALIDITY_REPORT_MD

    for artifact in (
        result.summary_csv,
        result.channel_attribution_matrix_csv,
        result.false_earned_false_unearned_csv,
        result.invalid_asymmetry_csv,
        result.report_md,
    ):
        assert artifact.is_file()

    with result.summary_csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row["scope"] == "global" for row in rows)
    assert any(row["injected_channel"] == "visible_test_overfitting" for row in rows)


def test_cli_report_injection_validity(capsys, tmp_path: Path) -> None:
    output_dir = tmp_path / "report"
    exit_code = main(
        [
            "report",
            "injection-validity",
            "--results",
            str(RESULTS_CSV),
            "--specs",
            str(SPECS_DIR),
            "--output",
            str(output_dir),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert Path(payload["report_md"]).is_file()
    assert Path(payload["summary_csv"]).is_file()
