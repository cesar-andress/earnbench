"""Agent run records."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AgentRun:
    """One agent episode on a task, with a submitted artifact for grading."""

    run_id: str
    task_id: str
    artifact_ref: str
    nominal_success: bool
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id:
            msg = "run_id must be non-empty"
            raise ValueError(msg)
        if not self.task_id:
            msg = "task_id must be non-empty"
            raise ValueError(msg)
