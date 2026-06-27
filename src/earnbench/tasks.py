"""Task definitions for EarnBench evaluations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Task:
    """A single benchmark instance to evaluate."""

    task_id: str
    description: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id:
            msg = "task_id must be non-empty"
            raise ValueError(msg)
