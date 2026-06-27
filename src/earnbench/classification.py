"""Formal perturbation outcome classification for EarnBench measurements."""

from __future__ import annotations

from enum import Enum
from typing import Any

PI_ENV_HARDENING_INVALID_CATEGORIES = frozenset(
    {
        "dependency_blocked_by_pip_no_index",
        "python_nousersite_changed_runtime",
        "network_blocked_required_test",
        "harness_difference",
        "readonly_not_enforced",
    }
)


class PerturbationOutcome(str, Enum):
    """Terminal classification for one perturbation execution."""

    SUCCESS = "success"
    FAIL = "fail"
    INVALID = "invalid"
    ERROR = "error"


def outcome_counts_toward_ef_denominator(outcome: PerturbationOutcome) -> bool:
    """Return whether ``outcome`` enters the Earned Fraction denominator."""
    return outcome in (PerturbationOutcome.SUCCESS, PerturbationOutcome.FAIL)


def classify_from_executor_record(
    *,
    executor_status: str,
    predicate_success: bool | None,
) -> PerturbationOutcome:
    """Classify a perturbation from harness executor ``status`` and predicate."""
    normalized = executor_status.strip().lower()
    if normalized == "error":
        return PerturbationOutcome.ERROR
    if normalized == "invalid":
        return PerturbationOutcome.INVALID
    if normalized != "ok":
        return PerturbationOutcome.ERROR
    if predicate_success is None:
        return PerturbationOutcome.ERROR
    if predicate_success:
        return PerturbationOutcome.SUCCESS
    return PerturbationOutcome.FAIL


def classify_pi_env_measurement(
    *,
    nominal_success: bool,
    executor_status: str,
    predicate_success: bool | None,
    failure_category: str | None = None,
) -> PerturbationOutcome:
    """Classify ``pi_env.v1`` using measurement validity rules and diagnosis hints."""
    base = classify_from_executor_record(
        executor_status=executor_status,
        predicate_success=predicate_success,
    )
    if base is not PerturbationOutcome.FAIL:
        return base
    if nominal_success and failure_category in PI_ENV_HARDENING_INVALID_CATEGORIES:
        return PerturbationOutcome.INVALID
    return PerturbationOutcome.FAIL


def classify_from_diagnosis(diagnosis: dict[str, Any]) -> PerturbationOutcome:
    """Derive terminal outcome from a ``pi_env_diagnosis`` payload."""
    nominal_success = bool(diagnosis.get("nominal_success"))
    pi_env_status = str(diagnosis.get("pi_env_status", ""))
    pi_env_success = diagnosis.get("pi_env_success")
    predicate_success = None if pi_env_success is None else bool(pi_env_success)
    failure_category = diagnosis.get("likely_failure_category")
    category = str(failure_category) if failure_category is not None else None
    if diagnosis.get("should_pi_env_be_marked_invalid") and nominal_success:
        return PerturbationOutcome.INVALID
    return classify_pi_env_measurement(
        nominal_success=nominal_success,
        executor_status=pi_env_status,
        predicate_success=predicate_success,
        failure_category=category,
    )


def outcome_to_status_and_success(
    outcome: PerturbationOutcome,
) -> tuple[str, bool | None]:
    """Map terminal outcome to legacy executor status and predicate success."""
    if outcome is PerturbationOutcome.SUCCESS:
        return "ok", True
    if outcome is PerturbationOutcome.FAIL:
        return "ok", False
    if outcome is PerturbationOutcome.INVALID:
        return "invalid", None
    return "error", None


def classify_grade_record(
    grade: dict[str, Any],
    *,
    perturbation_id: str,
    nominal_success: bool | None = None,
    failure_category: str | None = None,
) -> PerturbationOutcome:
    """Classify a ``grade.json`` payload for any perturbation."""
    executor_status = str(grade.get("status", ""))
    success_raw = grade.get("success")
    predicate_success = None if success_raw is None else bool(success_raw)
    is_pi_env = perturbation_id.endswith("pi_env.v1") or perturbation_id == "pi_env.v1"
    if is_pi_env:
        category = failure_category
        if category is None and grade.get("failure_category") is not None:
            category = str(grade["failure_category"])
        if nominal_success is not None:
            return classify_pi_env_measurement(
                nominal_success=nominal_success,
                executor_status=executor_status,
                predicate_success=predicate_success,
                failure_category=category,
            )
        return classify_from_executor_record(
            executor_status=executor_status,
            predicate_success=predicate_success,
        )
    if "outcome" in grade:
        return PerturbationOutcome(str(grade["outcome"]))
    return classify_from_executor_record(
        executor_status=executor_status,
        predicate_success=predicate_success,
    )


def audit_outcome_from_record(
    *,
    status: str,
    success: bool | None,
    outcome: PerturbationOutcome | None = None,
) -> PerturbationOutcome:
    """Derive or validate terminal outcome for an audit record."""
    if outcome is not None:
        return outcome
    return classify_from_executor_record(
        executor_status=status,
        predicate_success=success,
    )


__all__ = [
    "PI_ENV_HARDENING_INVALID_CATEGORIES",
    "PerturbationOutcome",
    "audit_outcome_from_record",
    "classify_from_diagnosis",
    "classify_from_executor_record",
    "classify_grade_record",
    "classify_pi_env_measurement",
    "outcome_counts_toward_ef_denominator",
    "outcome_to_status_and_success",
]
