"""Shared adapter types and abstract benchmark adapter interface."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from earnbench.audit import AuditRecord
from earnbench.outcomes import NominalOutcome, PerturbationResult

ADAPTER_CONFIG_SCHEMA_VERSION = "swe_adapter_run.v1"
MVP_PERTURBATION_IDS = frozenset({"pi_vtest.v1", "pi_verif.v1", "pi_env.v1"})


@dataclass(frozen=True, slots=True)
class PatchArtifact:
    """Unified diff submitted for grading (e.g. SWE-bench ``model_patch``)."""

    content: str
    source: str = "model_patch"
    content_sha256: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.content.strip():
            msg = "patch content must be non-empty"
            raise ValueError(msg)
        if not self.source:
            msg = "patch source must be non-empty"
            raise ValueError(msg)
        if not self.content_sha256:
            digest = hashlib.sha256(self.content.encode()).hexdigest()
            object.__setattr__(self, "content_sha256", digest)


@dataclass(frozen=True, slots=True)
class BenchmarkInstance:
    """Pinned benchmark task instance metadata (no harness execution)."""

    instance_id: str
    repo: str
    base_commit: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...] = ()
    dataset_name: str = "SWE-bench_Verified"
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.instance_id:
            msg = "instance_id must be non-empty"
            raise ValueError(msg)
        if not self.repo:
            msg = "repo must be non-empty"
            raise ValueError(msg)
        if not self.base_commit:
            msg = "base_commit must be non-empty"
            raise ValueError(msg)
        if not self.fail_to_pass:
            msg = "fail_to_pass must contain at least one test id"
            raise ValueError(msg)
        if not self.dataset_name:
            msg = "dataset_name must be non-empty"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    """Pinned adapter run configuration (digests, enabled perturbations)."""

    dataset_revision: str
    holdout_salt: str
    enabled_perturbations: tuple[str, ...] = tuple(sorted(MVP_PERTURBATION_IDS))
    schema_version: str = ADAPTER_CONFIG_SCHEMA_VERSION
    config_digest: str = ""
    swebench_package_version: str = ""

    def __post_init__(self) -> None:
        if not self.dataset_revision:
            msg = "dataset_revision must be non-empty"
            raise ValueError(msg)
        if not self.holdout_salt:
            msg = "holdout_salt must be non-empty"
            raise ValueError(msg)
        if not self.enabled_perturbations:
            msg = "enabled_perturbations must be non-empty"
            raise ValueError(msg)
        if not self.schema_version:
            msg = "schema_version must be non-empty"
            raise ValueError(msg)
        for perturbation_id in self.enabled_perturbations:
            if not perturbation_id:
                msg = "enabled_perturbations entries must be non-empty"
                raise ValueError(msg)
        if not self.config_digest:
            payload = {
                "schema_version": self.schema_version,
                "dataset_revision": self.dataset_revision,
                "holdout_salt": self.holdout_salt,
                "enabled_perturbations": list(self.enabled_perturbations),
                "swebench_package_version": self.swebench_package_version,
            }
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode(),
            ).hexdigest()
            object.__setattr__(self, "config_digest", f"sha256:{digest}")


@dataclass(frozen=True, slots=True)
class NominalEvaluationRequest:
    """Request to re-grade a fixed patch under the nominal predicate."""

    run_id: str
    instance: BenchmarkInstance
    patch: PatchArtifact
    config: AdapterConfig

    def __post_init__(self) -> None:
        if not self.run_id:
            msg = "run_id must be non-empty"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PerturbationEvaluationRequest:
    """Request to re-grade a fixed patch under one counterfactual perturbation."""

    run_id: str
    instance: BenchmarkInstance
    patch: PatchArtifact
    config: AdapterConfig
    perturbation_id: str

    def __post_init__(self) -> None:
        if not self.run_id:
            msg = "run_id must be non-empty"
            raise ValueError(msg)
        if not self.perturbation_id:
            msg = "perturbation_id must be non-empty"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class EvaluationArtifact:
    """Bundle returned by an adapter evaluation (outcome + optional audit)."""

    run_id: str
    task_id: str
    nominal: NominalOutcome | None = None
    perturbation: PerturbationResult | None = None
    audit: AuditRecord | None = None
    log_ref: str = ""

    def __post_init__(self) -> None:
        if not self.run_id:
            msg = "run_id must be non-empty"
            raise ValueError(msg)
        if not self.task_id:
            msg = "task_id must be non-empty"
            raise ValueError(msg)
        if self.nominal is None and self.perturbation is None:
            msg = "EvaluationArtifact requires nominal or perturbation result"
            raise ValueError(msg)
        if self.nominal is not None and self.perturbation is not None:
            msg = "EvaluationArtifact cannot set both nominal and perturbation results"
            raise ValueError(msg)


class BenchmarkAdapter(ABC):
    """Abstract post-hoc grading adapter (parse/validate now; execute later)."""

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        """Stable adapter identifier (e.g. ``swebench_verified.v1``)."""

    def validate_patch(self, patch: PatchArtifact) -> None:
        """Validate patch artifact; raises ``ValueError`` on failure."""
        if not patch.content.strip():
            msg = "patch content must be non-empty"
            raise ValueError(msg)

    def validate_instance(self, instance: BenchmarkInstance) -> None:
        """Validate benchmark instance metadata."""
        BenchmarkInstance(
            instance_id=instance.instance_id,
            repo=instance.repo,
            base_commit=instance.base_commit,
            fail_to_pass=instance.fail_to_pass,
            pass_to_pass=instance.pass_to_pass,
            dataset_name=instance.dataset_name,
            metadata=instance.metadata,
        )

    def validate_config(self, config: AdapterConfig) -> None:
        """Validate adapter configuration."""
        AdapterConfig(
            dataset_revision=config.dataset_revision,
            holdout_salt=config.holdout_salt,
            enabled_perturbations=config.enabled_perturbations,
            schema_version=config.schema_version,
            config_digest=config.config_digest,
            swebench_package_version=config.swebench_package_version,
        )

    def validate_nominal_request(self, request: NominalEvaluationRequest) -> None:
        """Validate a nominal evaluation request."""
        self.validate_patch(request.patch)
        self.validate_instance(request.instance)
        self.validate_config(request.config)

    def validate_perturbation_request(
        self,
        request: PerturbationEvaluationRequest,
    ) -> None:
        """Validate a perturbation evaluation request."""
        self.validate_nominal_request(
            NominalEvaluationRequest(
                run_id=request.run_id,
                instance=request.instance,
                patch=request.patch,
                config=request.config,
            ),
        )
        if request.perturbation_id not in request.config.enabled_perturbations:
            msg = (
                f"perturbation {request.perturbation_id!r} is not enabled "
                f"in adapter config"
            )
            raise ValueError(msg)

    @abstractmethod
    def evaluate_nominal(
        self,
        request: NominalEvaluationRequest,
    ) -> EvaluationArtifact:
        """Execute nominal re-grade (prod-only patch, official F2P+P2P)."""

    @abstractmethod
    def evaluate_perturbation(
        self,
        request: PerturbationEvaluationRequest,
    ) -> EvaluationArtifact:
        """Execute one perturbation re-grade and emit audit metadata."""
