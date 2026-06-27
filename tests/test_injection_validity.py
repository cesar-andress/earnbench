"""Tests for blinded injection validity analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from earnbench.cli import main
from earnbench.injection_diagnostic import (
    BLIND_INJECTION_DIAGNOSTIC_MD,
    BLIND_INJECTION_SUMMARY_JSON,
)
from earnbench.injection_validity import (
    CHANNEL_ATTRIBUTION_MATRIX_CSV,
    FALSE_EARNED_FALSE_UNEARNED_CSV,
    INJECTION_VALIDITY_REPORT_MD,
    INJECTION_VALIDITY_SUMMARY_CSV,
    INVALID_ASYMMETRY_CSV,
    InjectionResultRow,
    _ef_detected,
    _exact_channel_attribution,
    _in_registry_ef_detected,
    _target_pi_attributed,
    analyze_injection_validity,
    generate_injection_validity_report,
    load_injection_results,
    observed_failed_pi,
)
from earnbench.injections.catalog import load_injection_catalog
from earnbench.injections.spec import InjectionSpec

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
    assert result.summary_json.name == BLIND_INJECTION_SUMMARY_JSON
    assert result.diagnostic_md.name == BLIND_INJECTION_DIAGNOSTIC_MD

    for artifact in (
        result.summary_csv,
        result.channel_attribution_matrix_csv,
        result.false_earned_false_unearned_csv,
        result.invalid_asymmetry_csv,
        result.report_md,
        result.summary_json,
        result.diagnostic_md,
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
    assert Path(payload["summary_json"]).is_file()
    assert Path(payload["diagnostic_md"]).is_file()


def _make_spec(
    injection_id: str,
    *,
    channel: str = "visible_test_overfitting",
    expected_pi: str = "pi_vtest.v1",
) -> InjectionSpec:
    return InjectionSpec(
        injection_id=injection_id,
        instance_id="inst",
        paired_clean_patch_ref="clean.patch",
        injected_patch_ref="injected.patch",
        injected_channel=channel,
        in_registry=True,
        expected_failed_pi=expected_pi,
        expected_ef_exclude_invalid=0.6666666666666666,
        expected_ef_invalid_as_fail=0.6666666666666666,
        blinding_group="test",
        generation_seed="seed",
        template_id="T",
    )


def _make_row(
    injection_id: str,
    *,
    y0: bool = True,
    ef: float = 0.5,
    failed: tuple[str, ...] = (),
    pi_vtest: str = "ok",
    pi_verif: str = "ok",
    pi_env: str = "ok",
    invalid_count: int = 0,
) -> InjectionResultRow:
    return InjectionResultRow(
        injection_id=injection_id,
        instance_id="inst",
        y0=y0,
        ef_exclude_invalid=0.0 if not y0 else ef,
        ef_invalid_as_fail=0.0 if not y0 else ef,
        failed_mechanisms=failed,
        invalid_pi_count=invalid_count,
        pi_vtest_status=pi_vtest,
        pi_verif_status=pi_verif,
        pi_env_status=pi_env,
    )


def test_ef_detection_differs_from_exact_channel_attribution() -> None:
    spec = _make_spec("BI-ef-only")
    row = _make_row(
        "BI-ef-only",
        ef=0.5,
        failed=("verifier_tampering",),
    )
    assert _ef_detected(row) is True
    assert _in_registry_ef_detected(spec, row) is True
    assert _exact_channel_attribution(spec, row) is False
    assert _target_pi_attributed(spec, row) is False
    assert observed_failed_pi(row) == "pi_verif.v1"


def test_target_pi_attribution_without_exact_observed_match() -> None:
    spec = _make_spec(
        "BI-target-only",
        channel="verifier_tampering",
        expected_pi="pi_verif.v1",
    )
    row = _make_row(
        "BI-target-only",
        ef=1.0,
        failed=("visible_test_overfitting", "verifier_tampering"),
    )
    assert _target_pi_attributed(spec, row) is True
    assert _exact_channel_attribution(spec, row) is False
    assert observed_failed_pi(row) == "pi_vtest.v1"
    assert _ef_detected(row) is False
    assert _in_registry_ef_detected(spec, row) is False


def test_analyze_injection_validity_exposes_diagnostic_metric_split() -> None:
    spec = _make_spec("BI-split")
    clean = _make_row("BI-split__clean", ef=1.0)
    injected = _make_row(
        "BI-split",
        ef=0.5,
        failed=("verifier_tampering",),
    )
    payload = analyze_injection_validity(
        {"BI-split": injected, "BI-split__clean": clean},
        {"BI-split": spec},
    )
    diagnostic = payload["diagnostic_metrics"]
    assert payload["metrics"]["targeted_channel_detection_rate"] == pytest.approx(0.0)
    assert diagnostic["exact_channel_attribution_rate"] == pytest.approx(0.0)
    assert diagnostic["in_registry_ef_detection_rate"] == pytest.approx(1.0)
    assert diagnostic["target_pi_attribution_rate"] == pytest.approx(0.0)
