"""Shared Phase C agent utilities and base adapter."""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.agents.schemas import PHASE_C_SCAFFOLD_ID, AgentArmSpec, AttemptRecord
from earnbench.provenance import utc_timestamp

_DIFF_FENCE_RE = re.compile(
    r"```(?:diff|patch)?\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_DIFF_GIT_RE = re.compile(r"^diff --git ", re.MULTILINE)


def replicate_seed(*, base_seed: int, agent_id: str, instance_id: str, replicate: int) -> int:
    """Return a deterministic episode seed."""
    material = f"{base_seed}|{agent_id}|{instance_id}|{replicate}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def prompt_sha256(prompt: str) -> str:
    """Return lowercase SHA-256 hex digest of a prompt."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def patch_sha256(content: str) -> str:
    """Return lowercase SHA-256 hex digest of patch text."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_repair_prompt(*, instance_row: dict[str, Any]) -> str:
    """Build a deterministic SWE-bench repair prompt without verifier leakage."""
    instance_id = str(instance_row["instance_id"])
    repo = str(instance_row["repo"])
    base_commit = str(instance_row["base_commit"])
    problem = str(
        instance_row.get("problem_statement")
        or instance_row.get("issue")
        or instance_row.get("description")
        or ""
    ).strip()
    if not problem:
        problem = f"Fix the failing tests for SWE-bench instance {instance_id}."

    return (
        "You are a software repair agent. Produce a single unified diff that fixes "
        "the issue described below.\n\n"
        "Constraints:\n"
        "- Output ONLY a valid unified diff (no prose before or after).\n"
        "- Do NOT modify or add test files.\n"
        "- Do NOT include test harness patches.\n"
        "- Production/source changes only.\n\n"
        f"instance_id: {instance_id}\n"
        f"repository: {repo}\n"
        f"base_commit: {base_commit}\n\n"
        "Problem statement:\n"
        f"{problem}\n"
    )


def extract_unified_diff(text: str) -> str | None:
    """Extract the first plausible unified diff from model output."""
    if not text.strip():
        return None

    for match in _DIFF_FENCE_RE.finditer(text):
        candidate = match.group(1).strip()
        if _DIFF_GIT_RE.search(candidate):
            return _normalize_patch(candidate)

    start = text.find("diff --git ")
    if start >= 0:
        candidate = text[start:].strip()
        if _DIFF_GIT_RE.search(candidate):
            return _normalize_patch(candidate)

    return None


def _normalize_patch(patch: str) -> str:
    body = patch.strip("\n")
    return body + "\n"


@dataclass(frozen=True, slots=True)
class AgentRunContext:
    """Runtime paths and metadata for one collection attempt."""

    output_dir: Path
    arm: AgentArmSpec
    instance_id: str
    replicate: int
    seed: int
    prompt: str
    prompt_path: Path
    patch_path: Path
    trajectory_path: Path
    scaffold_id: str = PHASE_C_SCAFFOLD_ID


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Raw outcome from an adapter before AttemptRecord assembly."""

    trajectory_text: str
    patch_text: str
    status: str
    error: str = ""
    model: str = ""


class BaseAgentAdapter(ABC):
    """Collect one patch attempt for a single (arm, instance, replicate)."""

    provider: str

    @abstractmethod
    def run(self, context: AgentRunContext) -> AgentRunResult:
        """Execute the agent and return trajectory plus optional patch."""

    def collect_attempt(
        self,
        context: AgentRunContext,
        *,
        provider: str | None = None,
    ) -> AttemptRecord:
        """Run the adapter and assemble a validated attempt record."""
        started = utc_timestamp()
        try:
            result = self.run(context)
        except Exception as exc:
            completed = utc_timestamp()
            context.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
            context.trajectory_path.write_text(
                f"error: {exc}\n",
                encoding="utf-8",
            )
            return AttemptRecord(
                agent=context.arm.id,
                model=context.arm.model,
                provider=provider or self.provider,
                instance_id=context.instance_id,
                replicate=context.replicate,
                seed=context.seed,
                scaffold_id=context.scaffold_id,
                prompt_sha256=prompt_sha256(context.prompt),
                patch_path="",
                patch_sha256="",
                trajectory_log_ref=_relative_ref(context.output_dir, context.trajectory_path),
                status="error",
                started_at_utc=started,
                completed_at_utc=completed,
                error=str(exc),
            )

        context.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
        context.trajectory_path.write_text(result.trajectory_text, encoding="utf-8")

        patch_rel = ""
        patch_digest = ""
        if result.patch_text.strip():
            context.patch_path.parent.mkdir(parents=True, exist_ok=True)
            context.patch_path.write_text(result.patch_text, encoding="utf-8")
            patch_rel = _relative_ref(context.output_dir, context.patch_path)
            patch_digest = patch_sha256(result.patch_text)

        completed = utc_timestamp()
        return AttemptRecord(
            agent=context.arm.id,
            model=result.model or context.arm.model,
            provider=provider or self.provider,
            instance_id=context.instance_id,
            replicate=context.replicate,
            seed=context.seed,
            scaffold_id=context.scaffold_id,
            prompt_sha256=prompt_sha256(context.prompt),
            patch_path=patch_rel,
            patch_sha256=patch_digest,
            trajectory_log_ref=_relative_ref(context.output_dir, context.trajectory_path),
            status=result.status,
            started_at_utc=started,
            completed_at_utc=completed,
            error=result.error,
        )


def _relative_ref(output_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(output_dir.resolve()))
    except ValueError:
        return str(path)
