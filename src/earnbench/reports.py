"""Structured reports for EarnBench measurements."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from earnbench.outcomes import PerturbationResult
from earnbench.provenance import Provenance, build_provenance


class EarnedFractionStatus(str, Enum):
    """Whether Earned Fraction is defined for a run."""

    DEFINED = "defined"
    UNDEFINED = "undefined"


@dataclass(frozen=True, slots=True)
class EarnedFractionReport:
    """Earned Fraction measurement for one agent run."""

    run_id: str
    task_id: str
    nominal_success: bool
    status: EarnedFractionStatus
    earned_fraction: float | None
    valid_count: int
    successful_count: int
    failed_mechanisms: tuple[str, ...]
    survived_mechanisms: tuple[str, ...]
    warnings: tuple[str, ...]
    perturbation_results: tuple[PerturbationResult, ...]
    ef_exclude_invalid: float | None = None
    ef_invalid_as_fail: float | None = None
    ef_invalid_as_missing: float | None = None
    invalid_count: int = 0
    invalid_rate: float | None = None
    ef_sensitivity_gap: float | None = None
    invalid_as_missing_status: EarnedFractionStatus = EarnedFractionStatus.UNDEFINED
    invalid_as_missing_reason: str = ""
    reason: str = ""
    provenance: Provenance = field(default_factory=build_provenance)

    def __post_init__(self) -> None:
        if self.ef_exclude_invalid is None:
            object.__setattr__(self, "ef_exclude_invalid", self.earned_fraction)
        elif self.earned_fraction is None:
            object.__setattr__(self, "earned_fraction", self.ef_exclude_invalid)
        elif self.earned_fraction != self.ef_exclude_invalid:
            msg = "earned_fraction must match ef_exclude_invalid"
            raise ValueError(msg)

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

        if self.invalid_as_missing_status is EarnedFractionStatus.DEFINED:
            if self.ef_invalid_as_missing is None:
                msg = (
                    "ef_invalid_as_missing must be set when "
                    "invalid_as_missing_status is DEFINED"
                )
                raise ValueError(msg)
        elif self.ef_invalid_as_missing is not None:
            msg = (
                "ef_invalid_as_missing must be None when "
                "invalid_as_missing_status is UNDEFINED"
            )
            raise ValueError(msg)

    @property
    def is_defined(self) -> bool:
        return self.status is EarnedFractionStatus.DEFINED

    def to_dict(self) -> dict[str, object]:
        """Serialize the report to a JSON-friendly mapping."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "nominal_success": self.nominal_success,
            "status": self.status.value,
            "earned_fraction": self.earned_fraction,
            "ef_exclude_invalid": self.ef_exclude_invalid,
            "ef_invalid_as_fail": self.ef_invalid_as_fail,
            "ef_invalid_as_missing": self.ef_invalid_as_missing,
            "valid_count": self.valid_count,
            "successful_count": self.successful_count,
            "invalid_count": self.invalid_count,
            "invalid_rate": self.invalid_rate,
            "ef_sensitivity_gap": self.ef_sensitivity_gap,
            "invalid_as_missing_status": self.invalid_as_missing_status.value,
            "invalid_as_missing_reason": self.invalid_as_missing_reason,
            "failed_mechanisms": list(self.failed_mechanisms),
            "survived_mechanisms": list(self.survived_mechanisms),
            "warnings": list(self.warnings),
            "reason": self.reason,
            "perturbation_results": [
                {
                    "perturbation_id": r.perturbation_id,
                    "channel": r.channel,
                    "mechanism": r.mechanism,
                    "status": r.status.value,
                    "outcome": (
                        r.resolved_outcome.value
                        if r.resolved_outcome is not None
                        else None
                    ),
                    "success": r.success,
                    "message": r.message,
                }
                for r in self.perturbation_results
            ],
            "provenance": self.provenance.to_dict(),
        }
