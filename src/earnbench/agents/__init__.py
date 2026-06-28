"""Agent adapters for Phase C patch collection."""

from earnbench.agents.base import (
    BaseAgentAdapter,
    build_repair_prompt,
    extract_unified_diff,
    prompt_sha256,
    replicate_seed,
)
from earnbench.agents.external_cli import ExternalCliAdapter
from earnbench.agents.patch_repair import PatchRepairResult, maybe_repair_unified_diff, repair_unified_diff
from earnbench.agents.patch_validation import PatchValidationResult, validate_unified_diff
from earnbench.agents.ollama import OllamaAdapter
from earnbench.agents.schemas import (
    AgentArmSpec,
    AttemptRecord,
    PhaseCRunManifest,
    PhaseCSummary,
)

__all__ = [
    "AgentArmSpec",
    "AttemptRecord",
    "BaseAgentAdapter",
    "ExternalCliAdapter",
    "OllamaAdapter",
    "PatchRepairResult",
    "PhaseCRunManifest",
    "PhaseCSummary",
    "build_repair_prompt",
    "extract_unified_diff",
    "maybe_repair_unified_diff",
    "PatchValidationResult",
    "prompt_sha256",
    "replicate_seed",
    "repair_unified_diff",
    "validate_unified_diff",
]
