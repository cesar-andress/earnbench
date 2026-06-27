"""Tests for blind injection error taxonomy analysis."""

from __future__ import annotations

from pathlib import Path

import pytest

from earnbench.injection_error_taxonomy import (
    BLIND_INJECTION_ERROR_TAXONOMY_MD,
    BLIND_INJECTION_PAIR_DIAGNOSTIC_CSV,
    CAUSE_INVALID,
    CAUSE_MULTIPLE,
    CAUSE_NOMINAL_HARNESS,
    classify_immediate_cause,
    compute_criterion_hit,
    estimate_adjusted_false_unearned,
)
from earnbench.injection_validity import InjectionResultRow
from earnbench.injections.spec import InjectionSpec

def _spec() -> InjectionSpec:
    return InjectionSpec(
        injection_id="BI-test",
        instance_id="inst",
        paired_clean_patch_ref="clean.patch",
        injected_patch_ref="injected.patch",
        injected_channel="visible_test_overfitting",
        in_registry=True,
        expected_failed_pi="pi_vtest.v1",
        expected_ef_exclude_invalid=0.6666666666666666,
        expected_ef_invalid_as_fail=0.6666666666666666,
        blinding_group="test",
        generation_seed="seed",
        template_id="T",
    )


def _row(
    *,
    y0: bool = True,
    ef: float = 1.0,
    invalid_count: int = 0,
    pi_vtest: str = "ok",
    pi_verif: str = "ok",
    pi_env: str = "ok",
    failed: tuple[str, ...] = (),
) -> InjectionResultRow:
    return InjectionResultRow(
        injection_id="BI-test__clean",
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


def test_compute_criterion_hit_separates_clean_y0_from_ef_detection() -> None:
    clean = _row(y0=False)
    assert compute_criterion_hit(None, clean, arm="clean") is False
    injected = InjectionResultRow(
        injection_id="BI-test",
        instance_id="inst",
        y0=True,
        ef_exclude_invalid=0.6666666666666666,
        ef_invalid_as_fail=0.6666666666666666,
        failed_mechanisms=("visible_test_overfitting",),
        invalid_pi_count=0,
        pi_vtest_status="ok",
        pi_verif_status="ok",
        pi_env_status="ok",
    )
    assert compute_criterion_hit(_spec(), injected, arm="injected") is True


def test_classify_immediate_cause_marks_invalid_and_multiple() -> None:
    invalid_row = _row(y0=False, invalid_count=1, pi_verif="invalid")
    assert classify_immediate_cause(invalid_row, None) == CAUSE_INVALID

    mixed_row = _row(y0=False, invalid_count=1, pi_verif="invalid", pi_env="error")
    context = type(
        "Ctx",
        (),
        {
            "runtime_errors": 1,
            "build_errors": 0,
            "reason": "nominal_run_failed",
            "nominal_status": "error",
            "perturbation_results": (),
        },
    )()
    assert classify_immediate_cause(mixed_row, context) == CAUSE_MULTIPLE


def test_estimate_adjusted_false_unearned_excludes_infrastructure_only() -> None:
    clean_rows = [
        {
            "pair_id": "BI003",
            "false_unearned": True,
            "immediate_cause": CAUSE_NOMINAL_HARNESS,
            "y0": False,
            "EF": "",
        },
        {
            "pair_id": "BI002",
            "false_unearned": True,
            "immediate_cause": CAUSE_MULTIPLE,
            "y0": False,
            "EF": "",
        },
        {
            "pair_id": "BI007",
            "false_unearned": False,
            "immediate_cause": "none",
            "y0": True,
            "EF": 1.0,
        },
    ]
    adjustment = estimate_adjusted_false_unearned(clean_rows)
    assert adjustment["observed_false_unearned_count"] == 2
    assert adjustment["infrastructure_only_excluded_count"] == 1
    assert adjustment["remaining_false_unearned_count"] == 1
    assert adjustment["remaining_pair_ids"] == ["BI002"]


def test_blind_run_taxonomy_report_is_generated_from_existing_run() -> None:
    run_dir = (
        Path(__file__).resolve().parents[2].parent
        / "paper"
        / "experiments"
        / "runs"
        / "blind_run"
    )
    if not (run_dir / "injection_results.csv").is_file():
        pytest.skip("blind_run artifacts not available in this checkout")

    from earnbench.injection_error_taxonomy import (
        build_pair_diagnostic_rows,
        write_blind_injection_error_taxonomy_artifacts,
    )
    from earnbench.injection_validity import (
        analyze_injection_validity,
        load_injection_results,
    )
    from earnbench.injections.catalog import load_injection_catalog

    specs_dir = run_dir.parents[1] / "blind_injection" / "specs"
    results = load_injection_results(run_dir / "injection_results.csv")
    specs = load_injection_catalog(specs_dir)
    payload = analyze_injection_validity(results, specs)
    csv_path, md_path = write_blind_injection_error_taxonomy_artifacts(
        payload,
        results,
        specs,
        run_dir,
    )
    assert csv_path.name == BLIND_INJECTION_PAIR_DIAGNOSTIC_CSV
    assert md_path.name == BLIND_INJECTION_ERROR_TAXONOMY_MD
    pair_rows = build_pair_diagnostic_rows(results, specs, run_dir)
    assert len(pair_rows) == 24
    false_unearned = [
        row for row in pair_rows if row["arm"] == "clean" and row["false_unearned"]
    ]
    assert len(false_unearned) == 5
    assert md_path.read_text(encoding="utf-8").startswith("# Blind Injection Error Taxonomy")
