"""Regression and unit tests for perturbation outcome classification."""

from __future__ import annotations

import json
from pathlib import Path

from earnbench import NominalOutcome, PerturbationResult, compute_earned_fraction
from earnbench.classification import (
    PerturbationOutcome,
    classify_from_diagnosis,
    classify_from_executor_record,
    classify_grade_record,
    classify_pi_env_measurement,
    outcome_counts_toward_ef_denominator,
)
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1_ID
from earnbench.reports import EarnedFractionStatus
from earnbench.scheduler import aggregate_instance, load_pi_outcome

FIXTURES = Path(__file__).parent / "fixtures"
INSTANCE_ID = "psf__requests-1724"


def test_executor_success_fail_invalid_error() -> None:
    assert (
        classify_from_executor_record(executor_status="ok", predicate_success=True)
        is PerturbationOutcome.SUCCESS
    )
    assert (
        classify_from_executor_record(executor_status="ok", predicate_success=False)
        is PerturbationOutcome.FAIL
    )
    assert (
        classify_from_executor_record(executor_status="invalid", predicate_success=None)
        is PerturbationOutcome.INVALID
    )
    assert (
        classify_from_executor_record(executor_status="error", predicate_success=None)
        is PerturbationOutcome.ERROR
    )
    assert (
        classify_from_executor_record(executor_status="ok", predicate_success=None)
        is PerturbationOutcome.ERROR
    )


def test_pi_env_hardening_invalid_when_nominal_ok() -> None:
    outcome = classify_pi_env_measurement(
        nominal_success=True,
        executor_status="ok",
        predicate_success=False,
        failure_category="dependency_blocked_by_pip_no_index",
    )
    assert outcome is PerturbationOutcome.INVALID


def test_pi_env_genuine_fail_when_nominal_ok_without_invalid_category() -> None:
    outcome = classify_pi_env_measurement(
        nominal_success=True,
        executor_status="ok",
        predicate_success=False,
        failure_category="patch_application_difference",
    )
    assert outcome is PerturbationOutcome.FAIL


def test_ef_denominator_includes_fail_excludes_invalid_and_error() -> None:
    assert outcome_counts_toward_ef_denominator(PerturbationOutcome.SUCCESS)
    assert outcome_counts_toward_ef_denominator(PerturbationOutcome.FAIL)
    assert not outcome_counts_toward_ef_denominator(PerturbationOutcome.INVALID)
    assert not outcome_counts_toward_ef_denominator(PerturbationOutcome.ERROR)

    nominal = NominalOutcome(run_id="run-1", task_id=INSTANCE_ID, success=True)
    fail_result = PerturbationResult.ok("pi_verif.v1", success=False, channel="verif")
    invalid_result = PerturbationResult.invalid("pi_env.v1", channel="env")
    error_result = PerturbationResult.error("pi_vtest.v1", channel="vtest")

    report = compute_earned_fraction(
        nominal,
        [fail_result, invalid_result, error_result],
    )
    assert report.is_defined
    assert report.valid_count == 1
    assert report.earned_fraction == 0.0
    assert fail_result.counts_toward_ef_denominator
    assert not invalid_result.counts_toward_ef_denominator
    assert not error_result.counts_toward_ef_denominator


def test_diagnosis_pip_no_index_maps_to_invalid() -> None:
    diagnosis = {
        "instance_id": INSTANCE_ID,
        "nominal_success": True,
        "pi_env_status": "ok",
        "pi_env_success": False,
        "likely_failure_category": "dependency_blocked_by_pip_no_index",
        "should_pi_env_be_marked_invalid": True,
    }
    assert classify_from_diagnosis(diagnosis) is PerturbationOutcome.INVALID


def test_requests_1724_pi_env_invalid_excluded_from_ef_denominator(
    tmp_path: Path,
) -> None:
    """Regression: requests-1724 pi_env pip_no_index block is INVALID, not FAIL."""
    instance_dir = tmp_path / INSTANCE_ID
    pi_env_dir = instance_dir / PI_ENV_V1_ID
    pi_env_dir.mkdir(parents=True)
    (pi_env_dir / "grade.json").write_text(
        json.dumps(
            {
                "instance_id": INSTANCE_ID,
                "success": False,
                "status": "ok",
                "outcome": "fail",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    pip_log = (
        "APPLY_PATCH_PASS\n"
        "PIP_NO_INDEX=1\n"
        "ERROR: No matching distribution found for urllib3\n"
        "ERROR: Could not find a version that satisfies the requirement urllib3\n"
    )
    (pi_env_dir / "harness.log").write_text(pip_log, encoding="utf-8")

    pi_env = load_pi_outcome(
        instance_dir,
        PI_ENV_V1_ID,
        scheduled=(PI_ENV_V1_ID, "pi_verif.v1"),
        nominal_success=True,
    )
    assert pi_env.resolved_outcome is PerturbationOutcome.INVALID
    assert pi_env.status.value == "invalid"
    assert pi_env.success is None
    assert not pi_env.counts_toward_ef_denominator

    nominal = NominalOutcome(run_id="run-1", task_id=INSTANCE_ID, success=True)
    report = compute_earned_fraction(
        nominal,
        [
            pi_env,
            PerturbationResult.ok("pi_verif.v1", success=True, channel="verif"),
        ],
    )
    assert report.is_defined
    assert report.valid_count == 1
    assert report.earned_fraction == 1.0


def test_grade_record_pi_env_reclassifies_stored_fail_outcome() -> None:
    grade = {
        "status": "ok",
        "success": False,
        "outcome": "fail",
        "failure_category": "dependency_blocked_by_pip_no_index",
    }
    outcome = classify_grade_record(
        grade,
        perturbation_id=PI_ENV_V1_ID,
        nominal_success=True,
    )
    assert outcome is PerturbationOutcome.INVALID


def test_aggregate_instance_summary_csv_uses_invalid_pi_env_status(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / INSTANCE_ID
    instance_dir.mkdir(parents=True)
    (instance_dir / "nominal").mkdir(parents=True)
    (instance_dir / "meta.json").write_text(
        json.dumps(
            {
                "instance_id": INSTANCE_ID,
                "repo": "psf/requests",
                "run_id": "run-1",
                "config_digest": "sha256:cfg",
                "scheduled_perturbations": [
                    PI_VTEST_V1_ID,
                    PI_VERIF_V1_ID,
                    PI_ENV_V1_ID,
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (instance_dir / "nominal" / "grade.json").write_text(
        json.dumps({"success": True, "status": "ok"}) + "\n",
        encoding="utf-8",
    )
    for pid, success in (
        (PI_VTEST_V1_ID, True),
        (PI_VERIF_V1_ID, True),
    ):
        artifact = instance_dir / pid
        artifact.mkdir(parents=True)
        (artifact / "grade.json").write_text(
            json.dumps(
                {
                    "instance_id": INSTANCE_ID,
                    "success": success,
                    "status": "ok",
                    "outcome": "success",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    pi_env_dir = instance_dir / PI_ENV_V1_ID
    pi_env_dir.mkdir(parents=True)
    (pi_env_dir / "grade.json").write_text(
        json.dumps(
            {
                "instance_id": INSTANCE_ID,
                "success": False,
                "status": "ok",
                "outcome": "fail",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (pi_env_dir / "harness.log").write_text(
        "PIP_NO_INDEX=1\n"
        "ERROR: Could not find a version that satisfies the requirement urllib3\n",
        encoding="utf-8",
    )

    row = aggregate_instance(
        metadata_path=FIXTURES / "swebench_smoke_metadata.json",
        output_dir=tmp_path,
        instance_id=INSTANCE_ID,
        scheduled_perturbations=(PI_VTEST_V1_ID, PI_VERIF_V1_ID, PI_ENV_V1_ID),
        run_id="run-1",
    )

    assert row["pi_env_status"] == "invalid"
    assert row["y_env"] is None
    assert row["valid_pi_count"] == 2
    assert row["ef_pi"] == 1.0
    assert row["ef_status"] == EarnedFractionStatus.DEFINED.value
    assert row["retained"] is True


def test_grade_record_explicit_outcome_wins() -> None:
    grade = {"status": "ok", "success": False, "outcome": "success"}
    assert (
        classify_grade_record(grade, perturbation_id="pi_verif.v1")
        is PerturbationOutcome.SUCCESS
    )


def test_perturbation_result_from_outcome_round_trip() -> None:
    for outcome in PerturbationOutcome:
        result = PerturbationResult.from_outcome("pi_env.v1", outcome, channel="env")
        assert result.resolved_outcome is outcome
        expected_denominator = outcome_counts_toward_ef_denominator(outcome)
        assert result.counts_toward_ef_denominator == expected_denominator


def test_legacy_invalid_status_resolves_to_invalid_outcome() -> None:
    result = PerturbationResult.invalid("pi_env.v1", channel="env")
    assert result.resolved_outcome is PerturbationOutcome.INVALID
    assert not result.counts_toward_ef_denominator


def test_legacy_error_status_resolves_to_error_outcome() -> None:
    result = PerturbationResult.error("pi_vtest.v1", channel="vtest")
    assert result.resolved_outcome is PerturbationOutcome.ERROR
    assert not result.counts_toward_ef_denominator
