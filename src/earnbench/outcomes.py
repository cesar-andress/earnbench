"""Outcomes from nominal and counterfactual grading."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from earnbench.classification import (
    PerturbationOutcome,
    outcome_counts_toward_ef_denominator,
    outcome_to_status_and_success,
)


class OutcomeStatus(str, Enum):
    """Lifecycle status of a perturbation outcome."""

    OK = "ok"
    INVALID = "invalid"
    ERROR = "error"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class NominalOutcome:
    """Result of nominal (unperturbed) grading for one agent run."""

    run_id: str
    task_id: str
    success: bool

    def __post_init__(self) -> None:
        if not self.run_id:
            msg = "run_id must be non-empty"
            raise ValueError(msg)
        if not self.task_id:
            msg = "task_id must be non-empty"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PerturbationResult:
    """Result of applying one perturbation to a nominally successful run."""

    perturbation_id: str
    status: OutcomeStatus
    success: bool | None = None
    outcome: PerturbationOutcome | None = None
    channel: str = ""
    message: str = ""

    def __post_init__(self) -> None:
        if not self.perturbation_id:
            msg = "perturbation_id must be non-empty"
            raise ValueError(msg)
        if self.status is OutcomeStatus.OK and self.success is None:
            msg = "success must be set when status is OK"
            raise ValueError(msg)
        if self.status is not OutcomeStatus.OK and self.success is not None:
            msg = "success must be None when status is not OK"
            raise ValueError(msg)
        if self.status is OutcomeStatus.MISSING and self.outcome is not None:
            msg = "outcome must be None when status is MISSING"
            raise ValueError(msg)
        if self.outcome is not None:
            expected_status_raw, expected_success = outcome_to_status_and_success(
                self.outcome
            )
            expected_status = OutcomeStatus(expected_status_raw)
            if self.status is not expected_status or self.success != expected_success:
                msg = (
                    "outcome is inconsistent with status/success: "
                    f"outcome={self.outcome.value} status={self.status.value} "
                    f"success={self.success}"
                )
                raise ValueError(msg)

    @property
    def valid(self) -> bool:
        """Whether this result counts toward the EF denominator."""
        return self.counts_toward_ef_denominator

    @property
    def counts_toward_ef_denominator(self) -> bool:
        """Whether this result enters the EF denominator."""
        if self.outcome is not None:
            return outcome_counts_toward_ef_denominator(self.outcome)
        return self.status is OutcomeStatus.OK

    @property
    def resolved_outcome(self) -> PerturbationOutcome | None:
        """Return terminal outcome, inferring from status when omitted."""
        if self.outcome is not None:
            return self.outcome
        if self.status is OutcomeStatus.MISSING:
            return None
        if self.status is OutcomeStatus.OK:
            return (
                PerturbationOutcome.SUCCESS
                if self.success
                else PerturbationOutcome.FAIL
            )
        if self.status is OutcomeStatus.INVALID:
            return PerturbationOutcome.INVALID
        return PerturbationOutcome.ERROR

    @property
    def mechanism(self) -> str:
        """Mechanism label for attribution (channel or perturbation id)."""
        return self.channel or self.perturbation_id

    @classmethod
    def ok(
        cls,
        perturbation_id: str,
        *,
        success: bool,
        channel: str = "",
        message: str = "",
    ) -> PerturbationResult:
        outcome = PerturbationOutcome.SUCCESS if success else PerturbationOutcome.FAIL
        return cls(
            perturbation_id=perturbation_id,
            outcome=outcome,
            status=OutcomeStatus.OK,
            success=success,
            channel=channel,
            message=message,
        )

    @classmethod
    def invalid(
        cls,
        perturbation_id: str,
        *,
        channel: str = "",
        message: str = "",
    ) -> PerturbationResult:
        return cls(
            perturbation_id=perturbation_id,
            outcome=PerturbationOutcome.INVALID,
            status=OutcomeStatus.INVALID,
            channel=channel,
            message=message,
        )

    @classmethod
    def error(
        cls,
        perturbation_id: str,
        *,
        channel: str = "",
        message: str = "",
    ) -> PerturbationResult:
        return cls(
            perturbation_id=perturbation_id,
            outcome=PerturbationOutcome.ERROR,
            status=OutcomeStatus.ERROR,
            channel=channel,
            message=message,
        )

    @classmethod
    def missing(
        cls,
        perturbation_id: str,
        *,
        channel: str = "",
        message: str = "",
    ) -> PerturbationResult:
        return cls(
            perturbation_id=perturbation_id,
            status=OutcomeStatus.MISSING,
            channel=channel,
            message=message,
        )

    @classmethod
    def from_outcome(
        cls,
        perturbation_id: str,
        outcome: PerturbationOutcome,
        *,
        channel: str = "",
        message: str = "",
    ) -> PerturbationResult:
        status_raw, success = outcome_to_status_and_success(outcome)
        return cls(
            perturbation_id=perturbation_id,
            outcome=outcome,
            status=OutcomeStatus(status_raw),
            success=success,
            channel=channel,
            message=message,
        )
