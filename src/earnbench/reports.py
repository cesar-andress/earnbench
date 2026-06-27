"""Structured reports for EarnBench measurements."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from earnbench.outcomes import PerturbationResult


class EarnedFractionStatus(str, Enum):
    """Whether Earned Fraction is defined for a run."""

    DEFINED = "defined"
    UNDEFINED = "undefined"


@dataclass(frozen=True, slots=True)
class EarnedFractionReport:
    """Earned Fraction measurement for one agent run."""

    run_id: str
    task_id: str
    status: EarnedFractionStatus
    earned_fraction: float | None
    successful_count: int
    valid_count: int
    perturbation_results: tuple[PerturbationResult, ...]
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status is EarnedFractionStatus.DEFINED:
            if self.earned_fraction is None:
                msg = "earned_fraction must be set when status is DEFINED"
                raise ValueError(msg)
            if not 0.0 <= self.earned_fraction <= 1.0:
                msg = "earned_fraction must be in [0, 1]"
                raise ValueError(msg)
            if self.valid_count <= 0:
                msg = "valid_count must be positive when status is DEFINED"
                raise ValueError(msg)
        elif self.earned_fraction is not None:
            msg = "earned_fraction must be None when status is UNDEFINED"
            raise ValueError(msg)

    @property
    def is_defined(self) -> bool:
        return self.status is EarnedFractionStatus.DEFINED

    def to_dict(self) -> dict[str, object]:
        """Serialize the report to a JSON-friendly mapping."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "earned_fraction": self.earned_fraction,
            "successful_count": self.successful_count,
            "valid_count": self.valid_count,
            "reason": self.reason,
            "perturbation_results": [
                {
                    "perturbation_id": r.perturbation_id,
                    "status": r.status.value,
                    "success": r.success,
                    "message": r.message,
                }
                for r in self.perturbation_results
            ],
        }
