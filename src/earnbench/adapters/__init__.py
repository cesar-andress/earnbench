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
from earnbench.adapters.swebench import SWEBenchAdapter, prepare_smoke
from earnbench.adapters.swebench_metadata import (
    MetadataLoadError,
    SWEBenchVerifiedRecord,
    load_verified_instance,
)
from earnbench.adapters.swebench_patch import (
    DEFAULT_PROTECTED_GLOBS,
    ProdPatchResult,
    extract_prod_patch,
    is_protected_path,
    validate_protected_path_stripping,
)
from earnbench.audit import AuditRecord

__all__ = [
    "AdapterConfig",
    "AuditRecord",
    "BenchmarkAdapter",
    "BenchmarkInstance",
    "DEFAULT_PROTECTED_GLOBS",
    "EvaluationArtifact",
    "MetadataLoadError",
    "NominalEvaluationRequest",
    "PatchArtifact",
    "PerturbationEvaluationRequest",
    "ProdPatchResult",
    "SWEBenchAdapter",
    "SWEBenchVerifiedRecord",
    "extract_prod_patch",
    "is_protected_path",
    "load_verified_instance",
    "prepare_smoke",
    "validate_protected_path_stripping",
]
