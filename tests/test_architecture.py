import pytest

from earnbench import (
    AgentRun,
    EarnedFractionReport,
    EarnedFractionStatus,
    OutcomeStatus,
    Perturbation,
    PerturbationResult,
    Task,
    compute_earned_fraction,
)


def test_task_construction() -> None:
    task = Task(task_id="django__123", description="Fix parsing bug")
    assert task.task_id == "django__123"
    assert task.metadata == {}


def test_task_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="task_id"):
        Task(task_id="")


def test_perturbation_construction() -> None:
    perturbation = Perturbation(
        perturbation_id="pi_vtest.v1",
        channel="visible_test_overfitting",
    )
    assert perturbation.version == "v1"


def test_agent_run_construction() -> None:
    run = AgentRun(
        run_id="run-1",
        task_id="django__123",
        artifact_ref="patch-abc",
        nominal_success=True,
    )
    assert run.nominal_success is True


def test_perturbation_result_factories() -> None:
    ok = PerturbationResult.ok("pi_vtest.v1", success=True)
    assert ok.valid
    assert ok.success is True

    invalid = PerturbationResult.invalid("pi_env.v1", message="harness error")
    assert not invalid.valid
    assert invalid.status is OutcomeStatus.INVALID

    missing = PerturbationResult.missing("pi_verif.v1")
    assert missing.status is OutcomeStatus.MISSING


def test_earned_fraction_simple_ratio() -> None:
    run = AgentRun(
        run_id="run-1",
        task_id="django__123",
        artifact_ref="patch-abc",
        nominal_success=True,
    )
    results = [
        PerturbationResult.ok("pi_vtest.v1", success=True),
        PerturbationResult.ok("pi_verif.v1", success=True),
        PerturbationResult.ok("pi_env.v1", success=False),
    ]
    report = compute_earned_fraction(run, results)
    assert report.is_defined
    assert report.earned_fraction == pytest.approx(2 / 3)
    assert report.successful_count == 2
    assert report.valid_count == 3


def test_earned_fraction_failed_nominal() -> None:
    run = AgentRun(
        run_id="run-1",
        task_id="django__123",
        artifact_ref="patch-abc",
        nominal_success=False,
    )
    report = compute_earned_fraction(
        run,
        [PerturbationResult.ok("pi_vtest.v1", success=True)],
    )
    assert not report.is_defined
    assert report.earned_fraction is None
    assert report.reason == "nominal_run_failed"


def test_earned_fraction_no_perturbations() -> None:
    run = AgentRun(
        run_id="run-1",
        task_id="django__123",
        artifact_ref="patch-abc",
        nominal_success=True,
    )
    report = compute_earned_fraction(run, [])
    assert not report.is_defined
    assert report.reason == "no_perturbations"


def test_earned_fraction_no_valid_runs() -> None:
    run = AgentRun(
        run_id="run-1",
        task_id="django__123",
        artifact_ref="patch-abc",
        nominal_success=True,
    )
    results = [
        PerturbationResult.invalid("pi_vtest.v1", message="crash"),
        PerturbationResult.missing("pi_verif.v1"),
    ]
    report = compute_earned_fraction(run, results)
    assert not report.is_defined
    assert report.reason == "no_valid_counterfactual_runs"
    assert report.valid_count == 0


def test_earned_fraction_report_validation() -> None:
    with pytest.raises(ValueError, match="earned_fraction must be set"):
        EarnedFractionReport(
            run_id="run-1",
            task_id="django__123",
            status=EarnedFractionStatus.DEFINED,
            earned_fraction=None,
            successful_count=0,
            valid_count=1,
            perturbation_results=(),
        )


def test_report_to_dict() -> None:
    run = AgentRun(
        run_id="run-1",
        task_id="django__123",
        artifact_ref="patch-abc",
        nominal_success=True,
    )
    report = compute_earned_fraction(
        run,
        [PerturbationResult.ok("pi_vtest.v1", success=True)],
    )
    payload = report.to_dict()
    assert payload["earned_fraction"] == 1.0
    assert payload["status"] == "defined"
    assert len(payload["perturbation_results"]) == 1
