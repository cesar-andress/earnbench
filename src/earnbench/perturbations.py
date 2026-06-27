"""Counterfactual perturbation specifications."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Perturbation:
    """An executable perturbation that closes an exploitable channel."""

    perturbation_id: str
    channel: str
    version: str = "v1"
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.perturbation_id:
            msg = "perturbation_id must be non-empty"
            raise ValueError(msg)
        if not self.channel:
            msg = "channel must be non-empty"
            raise ValueError(msg)
