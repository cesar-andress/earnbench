"""Benchmark adapter interfaces for post-hoc EarnBench grading."""

from earnbench.adapters.base import (
    AdapterConfig,
    AuditRecord,
    BenchmarkAdapter,
    BenchmarkInstance,
    EvaluationArtifact,
    NominalEvaluationRequest,
    PatchArtifact,
    PerturbationEvaluationRequest,
)
from earnbench.adapters.swebench import SWEBenchAdapter

__all__ = [
    "AdapterConfig",
    "AuditRecord",
    "BenchmarkAdapter",
    "BenchmarkInstance",
    "EvaluationArtifact",
    "NominalEvaluationRequest",
    "PatchArtifact",
    "PerturbationEvaluationRequest",
    "SWEBenchAdapter",
]
