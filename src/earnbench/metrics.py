"""Earned Fraction and related metrics."""

from __future__ import annotations

from earnbench.outcomes import NominalOutcome, OutcomeStatus, PerturbationResult
from earnbench.reports import EarnedFractionReport, EarnedFractionStatus


def _undefined_report(
    nominal: NominalOutcome,
    counterfactuals: tuple[PerturbationResult, ...],
    *,
    reason: str,
    warnings: tuple[str, ...],
) -> EarnedFractionReport:
    return EarnedFractionReport(
        run_id=nominal.run_id,
        task_id=nominal.task_id,
        nominal_success=nominal.success,
        status=EarnedFractionStatus.UNDEFINED,
        earned_fraction=None,
        valid_count=0,
        successful_count=0,
        failed_mechanisms=(),
        survived_mechanisms=(),
        warnings=warnings,
        perturbation_results=counterfactuals,
        reason=reason,
    )


def _exclusion_warnings(
    counterfactuals: list[PerturbationResult],
) -> list[str]:
    warnings: list[str] = []
    excluded = [r for r in counterfactuals if r.status is not OutcomeStatus.OK]
    if excluded:
        warnings.append(
            f"excluded {len(excluded)} invalid counterfactual run(s) from denominator"
        )
    return warnings


def compute_earned_fraction(
    nominal: NominalOutcome,
    counterfactuals: list[PerturbationResult],
) -> EarnedFractionReport:
    """Compute MVP Earned Fraction from nominal and counterfactual outcomes.

    earned_fraction = successful_counterfactual_runs / valid_counterfactual_runs

    Defined only when the nominal outcome succeeded and at least one
    counterfactual run is valid.
    """
    if not nominal.success:
        return _undefined_report(
            nominal,
            tuple(counterfactuals),
            reason="nominal_run_failed",
            warnings=("nominal run failed; earned fraction undefined",),
        )

    if not counterfactuals:
        return _undefined_report(
            nominal,
            (),
            reason="no_perturbations",
            warnings=("no perturbations provided; earned fraction undefined",),
        )

    warnings = _exclusion_warnings(counterfactuals)
    valid_results = [r for r in counterfactuals if r.status is OutcomeStatus.OK]
    valid_count = len(valid_results)

    if valid_count == 0:
        warnings = (
            *warnings,
            "no valid counterfactual runs; earned fraction undefined",
        )
        return _undefined_report(
            nominal,
            tuple(counterfactuals),
            reason="no_valid_counterfactual_runs",
            warnings=warnings,
        )

    survived = tuple(r.mechanism for r in valid_results if r.success)
    failed = tuple(r.mechanism for r in valid_results if not r.success)
    successful_count = len(survived)
    earned_fraction = successful_count / valid_count

    return EarnedFractionReport(
        run_id=nominal.run_id,
        task_id=nominal.task_id,
        nominal_success=True,
        status=EarnedFractionStatus.DEFINED,
        earned_fraction=earned_fraction,
        valid_count=valid_count,
        successful_count=successful_count,
        failed_mechanisms=failed,
        survived_mechanisms=survived,
        warnings=tuple(warnings),
        perturbation_results=tuple(counterfactuals),
        reason="",
    )
