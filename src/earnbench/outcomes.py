"""Outcomes from nominal and counterfactual grading."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OutcomeStatus(str, Enum):
    """Lifecycle status of a perturbation outcome."""

    OK = "ok"
    INVALID = "invalid"
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

    @property
    def valid(self) -> bool:
        """Whether this result counts toward the EF denominator."""
        return self.status is OutcomeStatus.OK

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
    ) -> PerturbationResult:
        return cls(
            perturbation_id=perturbation_id,
            status=OutcomeStatus.OK,
            success=success,
            channel=channel,
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
            status=OutcomeStatus.INVALID,
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
