"""External CLI placeholder adapters for Phase C patch collection."""

from __future__ import annotations

import subprocess

from earnbench.agents.base import (
    AgentRunContext,
    AgentRunResult,
    BaseAgentAdapter,
    extract_unified_diff,
)
from earnbench.agents.schemas import AgentArmSpec


class ExternalCliAdapter(BaseAgentAdapter):
    """Invoke an external agent CLI via a configurable command template."""

    provider = "external_cli"

    def __init__(self, *, timeout_seconds: float = 3600.0) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, context: AgentRunContext) -> AgentRunResult:
        command_template = context.arm.command.strip()
        if not command_template or command_template == "TODO":
            msg = (
                f"external_cli arm {context.arm.id!r} has no configured command; "
                "set command in arms.yaml"
            )
            raise RuntimeError(msg)

        context.prompt_path.parent.mkdir(parents=True, exist_ok=True)
        context.prompt_path.write_text(context.prompt, encoding="utf-8")
        output_path = context.trajectory_path.with_suffix(".out.txt")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not output_path.exists():
            output_path.write_text("", encoding="utf-8")

        rendered = command_template.format(
            prompt_file=str(context.prompt_path),
            output_file=str(output_path),
            instance_id=context.instance_id,
            replicate=context.replicate,
            seed=context.seed,
            model=context.arm.model,
            agent_id=context.arm.id,
        )
        completed = subprocess.run(
            rendered,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        output_text = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
        trajectory = (
            f"command: {rendered}\n"
            f"exit_code: {completed.returncode}\n"
            "--- stdout ---\n"
            f"{stdout}\n"
            "--- stderr ---\n"
            f"{stderr}\n"
            "--- output_file ---\n"
            f"{output_text}\n"
        )
        if completed.returncode != 0:
            return AgentRunResult(
                trajectory_text=trajectory,
                patch_text="",
                status="error",
                error=f"external CLI exited with code {completed.returncode}",
                model=context.arm.model,
            )

        combined = output_text or stdout
        patch = extract_unified_diff(combined)
        if patch is None:
            return AgentRunResult(
                trajectory_text=trajectory,
                patch_text="",
                status="no_patch",
                error="no valid unified diff found in CLI output",
                model=context.arm.model,
            )
        return AgentRunResult(
            trajectory_text=trajectory,
            patch_text=patch,
            status="ok",
            model=context.arm.model,
        )


def build_external_cli_adapter(arm: AgentArmSpec) -> ExternalCliAdapter:
    """Return an external CLI adapter configured from an arm spec."""
    _ = arm
    return ExternalCliAdapter()
