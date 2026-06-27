"""Earned Fraction and related metrics."""

from __future__ import annotations

from earnbench.classification import PerturbationOutcome
from earnbench.outcomes import NominalOutcome, PerturbationResult
from earnbench.provenance import Provenance, build_provenance
from earnbench.reports import EarnedFractionReport, EarnedFractionStatus

DEFAULT_INVALID_RATE_THRESHOLD = 0.0


def _undefined_report(
    nominal: NominalOutcome,
    counterfactuals: tuple[PerturbationResult, ...],
    *,
    reason: str,
    warnings: tuple[str, ...],
    provenance: Provenance,
    invalid_count: int = 0,
    invalid_rate: float | None = None,
) -> EarnedFractionReport:
    return EarnedFractionReport(
        run_id=nominal.run_id,
        task_id=nominal.task_id,
        nominal_success=nominal.success,
        status=EarnedFractionStatus.UNDEFINED,
        earned_fraction=None,
        ef_exclude_invalid=None,
        ef_invalid_as_fail=None,
        ef_invalid_as_missing=None,
        valid_count=0,
        successful_count=0,
        invalid_count=invalid_count,
        invalid_rate=invalid_rate,
        ef_sensitivity_gap=None,
        invalid_as_missing_status=EarnedFractionStatus.UNDEFINED,
        invalid_as_missing_reason=reason,
        failed_mechanisms=(),
        survived_mechanisms=(),
        warnings=warnings,
        perturbation_results=counterfactuals,
        reason=reason,
        provenance=provenance,
    )


def _exclusion_warnings(
    counterfactuals: list[PerturbationResult],
) -> list[str]:
    warnings: list[str] = []
    excluded = [r for r in counterfactuals if not r.counts_toward_ef_denominator]
    if excluded:
        warnings.append(
            "excluded "
            f"{len(excluded)} non-measurement counterfactual run(s) from denominator"
        )
    return warnings


def _resolved_outcome(result: PerturbationResult) -> PerturbationOutcome | None:
    return result.resolved_outcome


def _invalid_count(counterfactuals: list[PerturbationResult]) -> int:
    return sum(
        1
        for result in counterfactuals
        if _resolved_outcome(result) is PerturbationOutcome.INVALID
    )


def _invalid_rate(counterfactuals: list[PerturbationResult]) -> float | None:
    if not counterfactuals:
        return None
    return _invalid_count(counterfactuals) / len(counterfactuals)


def _results_for_exclude_invalid(
    counterfactuals: list[PerturbationResult],
) -> list[PerturbationResult]:
    return [r for r in counterfactuals if r.counts_toward_ef_denominator]


def _results_for_invalid_as_fail(
    counterfactuals: list[PerturbationResult],
) -> list[PerturbationResult]:
    return [
        r
        for r in counterfactuals
        if _resolved_outcome(r)
        in (
            PerturbationOutcome.SUCCESS,
            PerturbationOutcome.FAIL,
            PerturbationOutcome.INVALID,
        )
    ]


def _ef_from_valid_results(
    valid_results: list[PerturbationResult],
) -> tuple[float, int, int, tuple[str, ...], tuple[str, ...]] | None:
    if not valid_results:
        return None
    survived = tuple(
        r.mechanism
        for r in valid_results
        if _resolved_outcome(r) is PerturbationOutcome.SUCCESS
    )
    failed = tuple(
        r.mechanism
        for r in valid_results
        if _resolved_outcome(r)
        in (PerturbationOutcome.FAIL, PerturbationOutcome.INVALID)
    )
    successful_count = len(survived)
    valid_count = len(valid_results)
    earned_fraction = successful_count / valid_count
    return earned_fraction, valid_count, successful_count, failed, survived


def compute_earned_fraction(
    nominal: NominalOutcome,
    counterfactuals: list[PerturbationResult],
    *,
    provenance: Provenance | None = None,
    invalid_rate_threshold: float = DEFAULT_INVALID_RATE_THRESHOLD,
) -> EarnedFractionReport:
    """Compute MVP Earned Fraction and INVALID sensitivity variants.

    ``ef_exclude_invalid`` (also ``earned_fraction``) excludes INVALID and ERROR
    outcomes from the denominator. ``ef_invalid_as_fail`` treats INVALID π as valid
    failures. ``ef_invalid_as_missing`` uses the exclude-invalid denominator unless
    ``invalid_rate`` exceeds ``invalid_rate_threshold``, in which case it is undefined.
    """
    prov = provenance or build_provenance()
    invalid_count = _invalid_count(counterfactuals)
    rate = _invalid_rate(counterfactuals)

    if not nominal.success:
        return _undefined_report(
            nominal,
            tuple(counterfactuals),
            reason="nominal_run_failed",
            warnings=("nominal run failed; earned fraction undefined",),
            provenance=prov,
            invalid_count=invalid_count,
            invalid_rate=rate,
        )

    if not counterfactuals:
        return _undefined_report(
            nominal,
            (),
            reason="no_perturbations",
            warnings=("no perturbations provided; earned fraction undefined",),
            provenance=prov,
            invalid_count=0,
            invalid_rate=None,
        )

    warnings = _exclusion_warnings(counterfactuals)
    exclude_valid = _results_for_exclude_invalid(counterfactuals)
    exclude_metrics = _ef_from_valid_results(exclude_valid)

    fail_valid = _results_for_invalid_as_fail(counterfactuals)
    fail_metrics = _ef_from_valid_results(fail_valid)

    if exclude_metrics is None:
        warnings = (
            *warnings,
            "no valid counterfactual runs; earned fraction undefined",
        )
        return _undefined_report(
            nominal,
            tuple(counterfactuals),
            reason="no_valid_counterfactual_runs",
            warnings=warnings,
            provenance=prov,
            invalid_count=invalid_count,
            invalid_rate=rate,
        )

    (
        ef_exclude_invalid,
        valid_count,
        successful_count,
        failed,
        survived,
    ) = exclude_metrics
    ef_invalid_as_fail = fail_metrics[0] if fail_metrics is not None else None

    if rate is not None and rate > invalid_rate_threshold:
        ef_invalid_as_missing = None
        invalid_as_missing_status = EarnedFractionStatus.UNDEFINED
        invalid_as_missing_reason = "invalid_rate_exceeds_threshold"
    else:
        ef_invalid_as_missing = ef_exclude_invalid
        invalid_as_missing_status = EarnedFractionStatus.DEFINED
        invalid_as_missing_reason = ""

    ef_sensitivity_gap = None
    if ef_exclude_invalid is not None and ef_invalid_as_fail is not None:
        ef_sensitivity_gap = ef_exclude_invalid - ef_invalid_as_fail

    return EarnedFractionReport(
        run_id=nominal.run_id,
        task_id=nominal.task_id,
        nominal_success=True,
        status=EarnedFractionStatus.DEFINED,
        earned_fraction=ef_exclude_invalid,
        ef_exclude_invalid=ef_exclude_invalid,
        ef_invalid_as_fail=ef_invalid_as_fail,
        ef_invalid_as_missing=ef_invalid_as_missing,
        valid_count=valid_count,
        successful_count=successful_count,
        invalid_count=invalid_count,
        invalid_rate=rate,
        ef_sensitivity_gap=ef_sensitivity_gap,
        invalid_as_missing_status=invalid_as_missing_status,
        invalid_as_missing_reason=invalid_as_missing_reason,
        failed_mechanisms=failed,
        survived_mechanisms=survived,
        warnings=tuple(warnings),
        perturbation_results=tuple(counterfactuals),
        reason="",
        provenance=prov,
    )
