import pytest

from earnbench import (
    AgentRun,
    NominalOutcome,
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
    ok = PerturbationResult.ok("pi_vtest.v1", success=True, channel="vtest")
    assert ok.valid
    assert ok.mechanism == "vtest"

    invalid = PerturbationResult.invalid("pi_env.v1", message="harness error")
    assert not invalid.valid
    assert invalid.status is OutcomeStatus.INVALID

    missing = PerturbationResult.missing("pi_verif.v1")
    assert missing.status is OutcomeStatus.MISSING


def test_earned_fraction_simple_ratio() -> None:
    nominal = NominalOutcome(
        run_id="run-1",
        task_id="django__123",
        success=True,
    )
    results = [
        PerturbationResult.ok("pi_vtest.v1", success=True, channel="vtest"),
        PerturbationResult.ok("pi_verif.v1", success=True, channel="verif"),
        PerturbationResult.ok("pi_env.v1", success=False, channel="env"),
    ]
    report = compute_earned_fraction(nominal, results)
    assert report.is_defined
    assert report.earned_fraction == pytest.approx(2 / 3)
    assert report.successful_count == 2
    assert report.valid_count == 3
    assert report.failed_mechanisms == ("env",)
    assert report.survived_mechanisms == ("vtest", "verif")


def test_earned_fraction_failed_nominal() -> None:
    nominal = NominalOutcome(
        run_id="run-1",
        task_id="django__123",
        success=False,
    )
    report = compute_earned_fraction(
        nominal,
        [PerturbationResult.ok("pi_vtest.v1", success=True)],
    )
    assert not report.is_defined
    assert report.earned_fraction is None
    assert report.reason == "nominal_run_failed"


def test_earned_fraction_no_perturbations() -> None:
    nominal = NominalOutcome(
        run_id="run-1",
        task_id="django__123",
        success=True,
    )
    report = compute_earned_fraction(nominal, [])
    assert not report.is_defined
    assert report.reason == "no_perturbations"
    assert "no perturbations provided" in report.warnings[0]
