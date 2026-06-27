import pytest

from earnbench import (
    EarnedFractionReport,
    EarnedFractionStatus,
    NominalOutcome,
    PerturbationResult,
    compute_earned_fraction,
)


def _nominal(*, success: bool = True) -> NominalOutcome:
    return NominalOutcome(run_id="run-1", task_id="django__123", success=success)


def test_all_perturbations_survive() -> None:
    report = compute_earned_fraction(
        _nominal(),
        [
            PerturbationResult.ok("pi_vtest.v1", success=True, channel="vtest"),
            PerturbationResult.ok("pi_verif.v1", success=True, channel="verif"),
            PerturbationResult.ok("pi_env.v1", success=True, channel="env"),
        ],
    )
    assert report.is_defined
    assert report.earned_fraction == 1.0
    assert report.nominal_success is True
    assert report.valid_count == 3
    assert report.successful_count == 3
    assert report.survived_mechanisms == ("vtest", "verif", "env")
    assert report.failed_mechanisms == ()
    assert report.warnings == ()


def test_half_perturbations_survive() -> None:
    report = compute_earned_fraction(
        _nominal(),
        [
            PerturbationResult.ok("pi_vtest.v1", success=True, channel="vtest"),
            PerturbationResult.ok("pi_verif.v1", success=False, channel="verif"),
        ],
    )
    assert report.is_defined
    assert report.earned_fraction == 0.5
    assert report.successful_count == 1
    assert report.valid_count == 2
    assert report.survived_mechanisms == ("vtest",)
    assert report.failed_mechanisms == ("verif",)


def test_no_perturbations_survive() -> None:
    report = compute_earned_fraction(
        _nominal(),
        [
            PerturbationResult.ok("pi_vtest.v1", success=False, channel="vtest"),
            PerturbationResult.ok("pi_verif.v1", success=False, channel="verif"),
        ],
    )
    assert report.is_defined
    assert report.earned_fraction == 0.0
    assert report.successful_count == 0
    assert report.survived_mechanisms == ()
    assert report.failed_mechanisms == ("vtest", "verif")


def test_nominal_failure_is_undefined() -> None:
    report = compute_earned_fraction(
        _nominal(success=False),
        [PerturbationResult.ok("pi_vtest.v1", success=True, channel="vtest")],
    )
    assert not report.is_defined
    assert report.earned_fraction is None
    assert report.nominal_success is False
    assert report.reason == "nominal_run_failed"
    assert "nominal run failed" in report.warnings[0]


def test_invalid_perturbation_excluded() -> None:
    report = compute_earned_fraction(
        _nominal(),
        [
            PerturbationResult.ok("pi_vtest.v1", success=True, channel="vtest"),
            PerturbationResult.invalid(
                "pi_verif.v1",
                channel="verif",
                message="harness crash",
            ),
        ],
    )
    assert report.is_defined
    assert report.earned_fraction == 1.0
    assert report.valid_count == 1
    assert report.successful_count == 1
    assert any("excluded 1 invalid" in w for w in report.warnings)


def test_no_valid_perturbations_undefined_with_warning() -> None:
    report = compute_earned_fraction(
        _nominal(),
        [
            PerturbationResult.invalid("pi_vtest.v1", channel="vtest"),
            PerturbationResult.missing("pi_verif.v1", channel="verif"),
        ],
    )
    assert not report.is_defined
    assert report.earned_fraction is None
    assert report.valid_count == 0
    assert report.reason == "no_valid_counterfactual_runs"
    assert any("no valid counterfactual runs" in w for w in report.warnings)


def test_report_to_dict_includes_new_fields() -> None:
    report = compute_earned_fraction(
        _nominal(),
        [PerturbationResult.ok("pi_vtest.v1", success=True, channel="vtest")],
    )
    payload = report.to_dict()
    assert payload["nominal_success"] is True
    assert payload["failed_mechanisms"] == []
    assert payload["survived_mechanisms"] == ["vtest"]
    assert payload["warnings"] == []


def test_report_validation_rejects_invalid_defined_state() -> None:
    with pytest.raises(ValueError, match="earned_fraction must be set"):
        EarnedFractionReport(
            run_id="run-1",
            task_id="django__123",
            nominal_success=True,
            status=EarnedFractionStatus.DEFINED,
            earned_fraction=None,
            valid_count=1,
            successful_count=0,
            failed_mechanisms=(),
            survived_mechanisms=(),
            warnings=(),
            perturbation_results=(),
        )
