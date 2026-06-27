"""Benchmark adapter interfaces for post-hoc EarnBench grading."""

from earnbench.adapters.base import (
    AdapterConfig,
    BenchmarkAdapter,
    BenchmarkInstance,
    EvaluationArtifact,
    NominalEvaluationRequest,
    PatchArtifact,
    PerturbationEvaluationRequest,
)
from earnbench.adapters.swebench import SWEBenchAdapter
from earnbench.audit import AuditRecord

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
