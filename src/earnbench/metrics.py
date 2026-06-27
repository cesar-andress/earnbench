"""Earned Fraction and related metrics."""

from __future__ import annotations

from earnbench.outcomes import OutcomeStatus, PerturbationResult
from earnbench.reports import EarnedFractionReport, EarnedFractionStatus
from earnbench.runs import AgentRun


def compute_earned_fraction(
    run: AgentRun,
    results: list[PerturbationResult],
) -> EarnedFractionReport:
    """Compute MVP Earned Fraction for one agent run.

    earned_fraction = successful_counterfactual_runs / valid_counterfactual_runs

    Defined only when the nominal run succeeded and at least one perturbation
    produced a valid counterfactual outcome.
    """
    if not run.nominal_success:
        return EarnedFractionReport(
            run_id=run.run_id,
            task_id=run.task_id,
            status=EarnedFractionStatus.UNDEFINED,
            earned_fraction=None,
            successful_count=0,
            valid_count=0,
            perturbation_results=tuple(results),
            reason="nominal_run_failed",
        )

    if not results:
        return EarnedFractionReport(
            run_id=run.run_id,
            task_id=run.task_id,
            status=EarnedFractionStatus.UNDEFINED,
            earned_fraction=None,
            successful_count=0,
            valid_count=0,
            perturbation_results=(),
            reason="no_perturbations",
        )

    valid_results = [r for r in results if r.status is OutcomeStatus.OK]
    valid_count = len(valid_results)

    if valid_count == 0:
        return EarnedFractionReport(
            run_id=run.run_id,
            task_id=run.task_id,
            status=EarnedFractionStatus.UNDEFINED,
            earned_fraction=None,
            successful_count=0,
            valid_count=0,
            perturbation_results=tuple(results),
            reason="no_valid_counterfactual_runs",
        )

    successful_count = sum(1 for r in valid_results if r.success)
    earned_fraction = successful_count / valid_count

    return EarnedFractionReport(
        run_id=run.run_id,
        task_id=run.task_id,
        status=EarnedFractionStatus.DEFINED,
        earned_fraction=earned_fraction,
        successful_count=successful_count,
        valid_count=valid_count,
        perturbation_results=tuple(results),
        reason="",
    )
