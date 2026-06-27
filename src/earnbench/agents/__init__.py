"""Agent adapters for Phase C patch collection."""

from earnbench.agents.base import (
    BaseAgentAdapter,
    build_repair_prompt,
    extract_unified_diff,
    prompt_sha256,
    replicate_seed,
)
from earnbench.agents.external_cli import ExternalCliAdapter
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
    "PhaseCRunManifest",
    "PhaseCSummary",
    "build_repair_prompt",
    "extract_unified_diff",
    "prompt_sha256",
    "replicate_seed",
]
