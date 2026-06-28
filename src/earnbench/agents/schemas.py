"""Schemas for Phase C agent patch collection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PHASE_C_SCAFFOLD_ID = "earnbench_phase_c_v1"
PHASE_C_SCHEMA_VERSION = "earnbench_phase_c.v1"

ATTEMPT_STATUSES = frozenset(
    {
        "ok",
        "no_patch",
        "invalid_patch",
        "error",
        "skipped",
    }
)

ATTEMPT_CSV_COLUMNS = (
    "agent",
    "model",
    "provider",
    "instance_id",
    "replicate",
    "seed",
    "scaffold_id",
    "prompt_sha256",
    "patch_path",
    "patch_sha256",
    "trajectory_log_ref",
    "status",
    "started_at_utc",
    "completed_at_utc",
    "error",
    "repair_applied",
    "original_patch",
    "repaired_patch",
)

ATTEMPT_CSV_OPTIONAL_COLUMNS = (
    "repair_applied",
    "original_patch",
    "repaired_patch",
)

ATTEMPT_CSV_REQUIRED_COLUMNS = tuple(
    column for column in ATTEMPT_CSV_COLUMNS if column not in ATTEMPT_CSV_OPTIONAL_COLUMNS
)

FAILURE_CSV_COLUMNS = ("agent", "instance_id", "replicate", "stage", "error", "timestamp_utc")


@dataclass(frozen=True, slots=True)
class AgentArmSpec:
    """One agent arm from ``arms.yaml``."""

    id: str
    provider: str
    replicates: int = 3
    model: str = ""
    command: str = ""
    temperature: float = 0.2
    base_url: str = "http://127.0.0.1:11434"
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.id.strip():
            errors.append("arm id must be non-empty")
        if self.provider not in {"ollama", "external_cli"}:
            errors.append(
                f"arm {self.id!r}: provider must be 'ollama' or 'external_cli', "
                f"got {self.provider!r}"
            )
        if self.replicates < 1:
            errors.append(f"arm {self.id!r}: replicates must be >= 1")
        if self.provider == "ollama" and not self.model.strip():
            errors.append(f"arm {self.id!r}: ollama arms require model")
        if self.provider == "external_cli" and not self.command.strip():
            errors.append(f"arm {self.id!r}: external_cli arms require command")
        return errors

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "provider": self.provider,
            "replicates": self.replicates,
            "temperature": self.temperature,
        }
        if self.model:
            payload["model"] = self.model
        if self.command:
            payload["command"] = self.command
        if self.base_url != "http://127.0.0.1:11434":
            payload["base_url"] = self.base_url
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentArmSpec:
        known = {"id", "provider", "replicates", "model", "command", "temperature", "base_url"}
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            id=str(data["id"]),
            provider=str(data["provider"]),
            replicates=int(data.get("replicates", 3)),
            model=str(data.get("model", "")),
            command=str(data.get("command", "")),
            temperature=float(data.get("temperature", 0.2)),
            base_url=str(data.get("base_url", "http://127.0.0.1:11434")),
            extra=extra,
        )


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One Phase C patch-collection attempt."""

    agent: str
    model: str
    provider: str
    instance_id: str
    replicate: int
    seed: int
    scaffold_id: str
    prompt_sha256: str
    patch_path: str
    patch_sha256: str
    trajectory_log_ref: str
    status: str
    started_at_utc: str
    completed_at_utc: str
    error: str = ""
    repair_applied: bool = False
    original_patch: str = ""
    repaired_patch: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.status not in ATTEMPT_STATUSES:
            errors.append(f"invalid status {self.status!r}")
        for name in ATTEMPT_CSV_COLUMNS:
            if not hasattr(self, name):
                errors.append(f"missing field {name}")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "model": self.model,
            "provider": self.provider,
            "instance_id": self.instance_id,
            "replicate": self.replicate,
            "seed": self.seed,
            "scaffold_id": self.scaffold_id,
            "prompt_sha256": self.prompt_sha256,
            "patch_path": self.patch_path,
            "patch_sha256": self.patch_sha256,
            "trajectory_log_ref": self.trajectory_log_ref,
            "status": self.status,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "error": self.error,
            "repair_applied": self.repair_applied,
            "original_patch": self.original_patch,
            "repaired_patch": self.repaired_patch,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttemptRecord:
        return cls(
            agent=str(data["agent"]),
            model=str(data.get("model", "")),
            provider=str(data["provider"]),
            instance_id=str(data["instance_id"]),
            replicate=int(data["replicate"]),
            seed=int(data["seed"]),
            scaffold_id=str(data["scaffold_id"]),
            prompt_sha256=str(data["prompt_sha256"]),
            patch_path=str(data.get("patch_path", "")),
            patch_sha256=str(data.get("patch_sha256", "")),
            trajectory_log_ref=str(data.get("trajectory_log_ref", "")),
            status=str(data["status"]),
            started_at_utc=str(data["started_at_utc"]),
            completed_at_utc=str(data["completed_at_utc"]),
            error=str(data.get("error", "")),
            repair_applied=bool(data.get("repair_applied", False)),
            original_patch=str(data.get("original_patch", "")),
            repaired_patch=str(data.get("repaired_patch", "")),
        )


@dataclass(frozen=True, slots=True)
class CollectionTask:
    """One scheduled (arm, instance, replicate) unit."""

    agent_id: str
    instance_id: str
    replicate: int

    @property
    def task_key(self) -> str:
        return f"{self.agent_id}:{self.instance_id}:r{self.replicate}"


@dataclass(frozen=True, slots=True)
class PhaseCRunManifest:
    """Prepared Phase C run manifest."""

    schema_version: str
    scaffold_id: str
    phase_a_run: str
    metadata_path: str
    output_dir: str
    arms_path: str
    instances_path: str
    instance_ids: tuple[str, ...]
    arms: tuple[AgentArmSpec, ...]
    tasks: tuple[CollectionTask, ...]
    run_id: str
    prepared_at_utc: str
    base_seed: int = 20260627

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scaffold_id": self.scaffold_id,
            "phase_a_run": self.phase_a_run,
            "metadata_path": self.metadata_path,
            "output_dir": self.output_dir,
            "arms_path": self.arms_path,
            "instances_path": self.instances_path,
            "instance_ids": list(self.instance_ids),
            "arms": [arm.to_dict() for arm in self.arms],
            "tasks": [
                {
                    "agent_id": task.agent_id,
                    "instance_id": task.instance_id,
                    "replicate": task.replicate,
                    "task_key": task.task_key,
                }
                for task in self.tasks
            ],
            "run_id": self.run_id,
            "prepared_at_utc": self.prepared_at_utc,
            "base_seed": self.base_seed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhaseCRunManifest:
        arms = tuple(AgentArmSpec.from_dict(item) for item in data["arms"])
        tasks = tuple(
            CollectionTask(
                agent_id=str(item["agent_id"]),
                instance_id=str(item["instance_id"]),
                replicate=int(item["replicate"]),
            )
            for item in data["tasks"]
        )
        instance_ids = tuple(str(item) for item in data["instance_ids"])
        return cls(
            schema_version=str(data.get("schema_version", PHASE_C_SCHEMA_VERSION)),
            scaffold_id=str(data.get("scaffold_id", PHASE_C_SCAFFOLD_ID)),
            phase_a_run=str(data["phase_a_run"]),
            metadata_path=str(data["metadata_path"]),
            output_dir=str(data["output_dir"]),
            arms_path=str(data["arms_path"]),
            instances_path=str(data["instances_path"]),
            instance_ids=instance_ids,
            arms=arms,
            tasks=tasks,
            run_id=str(data["run_id"]),
            prepared_at_utc=str(data["prepared_at_utc"]),
            base_seed=int(data.get("base_seed", 20260627)),
        )


@dataclass(frozen=True, slots=True)
class PhaseCSummary:
    """Aggregate summary for a completed Phase C run."""

    run_id: str
    attempt_count: int
    ok_count: int
    no_patch_count: int
    invalid_patch_count: int
    error_count: int
    skipped_count: int
    by_agent: dict[str, dict[str, int]]
    summarized_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "attempt_count": self.attempt_count,
            "ok_count": self.ok_count,
            "no_patch_count": self.no_patch_count,
            "invalid_patch_count": self.invalid_patch_count,
            "error_count": self.error_count,
            "skipped_count": self.skipped_count,
            "by_agent": self.by_agent,
            "summarized_at_utc": self.summarized_at_utc,
        }
