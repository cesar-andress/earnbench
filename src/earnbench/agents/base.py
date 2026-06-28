"""Shared Phase C agent utilities and base adapter."""

from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.agents.patch_repair import maybe_repair_unified_diff
from earnbench.agents.patch_validation import validate_unified_diff
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


_UNIFIED_DIFF_EXAMPLE = (
    "diff --git a/pkg/module.py b/pkg/module.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/pkg/module.py\n"
    "+++ b/pkg/module.py\n"
    "@@ -10,3 +10,4 @@ def example():\n"
    " unchanged context\n"
    "-old line\n"
    "+new line\n"
    " unchanged context\n"
)


def _parse_test_id_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        items = json.loads(stripped)
    else:
        return ()
    return tuple(str(item) for item in items)


def _test_id_to_module_path(test_id: str) -> str | None:
    parts = test_id.split(".")
    if len(parts) < 2:
        return None
    if parts[0] == "tests":
        return f"tests/{parts[1]}.py"
    return f"{parts[0].replace('.', '/')}.py"


def _failing_test_files(instance_row: dict[str, Any]) -> tuple[str, ...]:
    raw = instance_row.get("FAIL_TO_PASS")
    if raw is None:
        raw = instance_row.get("fail_to_pass")
    test_ids = _parse_test_id_list(raw)
    files = {
        path
        for test_id in test_ids
        if (path := _test_id_to_module_path(test_id)) is not None
    }
    return tuple(sorted(files))


def _repository_layout_section(*, repo: str, failing_test_files: tuple[str, ...]) -> str:
    repo_name = repo.split("/", 1)[1] if "/" in repo else repo
    lines = [
        "Repository layout:",
        f"- Checkout root: repository root for `{repo}` at the given base_commit.",
        "- Diff paths must be repository-relative and must match real filenames at that commit.",
        "- Diff headers use `a/<path>` and `b/<path>` prefixes relative to the checkout root.",
    ]
    if repo_name:
        lines.append(
            f"- Primary source code is usually under `{repo_name}/` "
            "(adjust only if the repository uses a different layout).",
        )
    if failing_test_files:
        lines.append("- Failing tests reference these test modules (read-only; do not edit):")
        lines.extend(f"  - `{path}`" for path in failing_test_files)
    else:
        lines.append("- Do not edit files under `tests/` or add new test files.")
    return "\n".join(lines)


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

    layout = _repository_layout_section(
        repo=repo,
        failing_test_files=_failing_test_files(instance_row),
    )

    return (
        "You are a software repair agent working on a SWE-bench Verified instance.\n"
        "Produce exactly one unified diff that fixes the issue below.\n\n"
        "Instance metadata:\n"
        f"- instance_id: {instance_id}\n"
        f"- repository: {repo}\n"
        f"- base_commit: {base_commit}\n\n"
        f"{layout}\n\n"
        "Problem statement:\n"
        f"{problem}\n\n"
        "Output requirements (strict):\n"
        "- Return ONLY a unified diff. The first line must be `diff --git a/... b/...`.\n"
        "- Use exact repository-relative filenames that exist at base_commit.\n"
        "- Every file change must include complete hunks with valid `@@ -l,s +l,s @@` headers.\n"
        "- Include context lines and every removed (`-`) / added (`+`) line for each hunk.\n"
        "- Do not truncate hunks, omit lines, or use placeholder paths.\n"
        "- End the diff with a trailing newline.\n\n"
        "Forbidden output:\n"
        "- Any prose, explanation, summary, or status message before or after the diff.\n"
        "- Markdown code fences such as ``` or ```diff.\n"
        "- Commentary inside the diff.\n"
        "- Edits to test files, new tests, or test harness patches.\n\n"
        "Scope:\n"
        "- Production/source changes only.\n"
        "- Do NOT modify or add test files.\n"
        "- Do NOT include test harness patches.\n\n"
        "Unified diff shape (illustrative paths only):\n"
        f"{_UNIFIED_DIFF_EXAMPLE}"
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
    repair_patch: bool = False


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
        original_patch_rel = ""
        repaired_patch_rel = ""
        repair_applied = False
        status = result.status
        error = result.error
        if result.patch_text.strip():
            original_text = result.patch_text
            repair = maybe_repair_unified_diff(
                original_text,
                enabled=context.repair_patch,
            )
            effective_text = repair.patch
            validation = validate_unified_diff(effective_text)

            context.patch_path.parent.mkdir(parents=True, exist_ok=True)
            if repair.applied:
                original_path = context.patch_path.with_name(
                    f"{context.patch_path.stem}.original.patch"
                )
                original_path.write_text(repair.original_patch, encoding="utf-8")
                context.patch_path.write_text(effective_text, encoding="utf-8")
                original_patch_rel = _relative_ref(context.output_dir, original_path)
                repaired_patch_rel = _relative_ref(context.output_dir, context.patch_path)
                repair_applied = True
            else:
                context.patch_path.write_text(effective_text, encoding="utf-8")

            patch_rel = _relative_ref(context.output_dir, context.patch_path)
            patch_digest = patch_sha256(effective_text)
            if not validation.valid:
                status = "invalid_patch"
                error = (
                    f"{validation.reason}: {validation.detail}"
                    if validation.detail
                    else validation.reason
                )
            else:
                status = "ok"

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
            status=status,
            started_at_utc=started,
            completed_at_utc=completed,
            error=error,
            repair_applied=repair_applied,
            original_patch=original_patch_rel,
            repaired_patch=repaired_patch_rel,
        )


def _relative_ref(output_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(output_dir.resolve()))
    except ValueError:
        return str(path)
