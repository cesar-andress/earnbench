"""SWE-bench Verified adapter (interface stub; execution not implemented).

Expected behavior when execution lands (v0.1):

* **Nominal** — fresh container, checkout ``base_commit``, apply prod-only diff,
  run official FAIL_TO_PASS + PASS_TO_PASS predicate → ``NominalOutcome``.
* **`pi_vtest.v1`** — same as nominal plus holdout F2P partition (hash salt from
  config); agent must not have seen holdout tests during episode.
* **`pi_verif.v1`** — restore pristine test tree, reject test-path hunks in patch,
  run harness from read-only eval mount.
* **`pi_env.v1`** — fresh hardened container (no network, ``PYTHONNOUSERSITE``,
  no agent layer reuse).

This stub validates request shapes and raises ``NotImplementedError`` for harness
calls. See the private design note ``swebench_adapter_design.md`` in the paper repo.
"""

from __future__ import annotations

from earnbench.adapters.base import (
    MVP_PERTURBATION_IDS,
    AdapterConfig,
    BenchmarkAdapter,
    EvaluationArtifact,
    NominalEvaluationRequest,
    PerturbationEvaluationRequest,
)

_EXEC_MESSAGE = (
    "SWE-bench harness execution is not implemented in earnbench v0.1. "
    "Use validate_* methods to check requests; execution arrives in a later release."
)


class SWEBenchAdapter(BenchmarkAdapter):
    """Post-hoc SWE-bench Verified adapter (validation only in v0.1)."""

    @property
    def adapter_id(self) -> str:
        return "swebench_verified.v1"

    def validate_config(self, config: AdapterConfig) -> None:
        super().validate_config(config)
        unknown = set(config.enabled_perturbations) - MVP_PERTURBATION_IDS
        if unknown:
            msg = f"unsupported perturbation ids for SWE-bench MVP: {sorted(unknown)}"
            raise ValueError(msg)

    def validate_perturbation_request(
        self,
        request: PerturbationEvaluationRequest,
    ) -> None:
        super().validate_perturbation_request(request)
        if request.perturbation_id not in MVP_PERTURBATION_IDS:
            msg = f"unsupported SWE-bench perturbation id: {request.perturbation_id}"
            raise ValueError(msg)

    def evaluate_nominal(
        self,
        request: NominalEvaluationRequest,
    ) -> EvaluationArtifact:
        """Re-grade patch under the nominal SWE-bench predicate.

        Future steps: spawn container → checkout ``base_commit`` →
        ``extract_prod_patch`` → run F2P+P2P → map to ``NominalOutcome`` +
        ``AuditRecord`` (``perturbation_id`` empty).
        """
        self.validate_nominal_request(request)
        raise NotImplementedError(_EXEC_MESSAGE)

    def evaluate_perturbation(
        self,
        request: PerturbationEvaluationRequest,
    ) -> EvaluationArtifact:
        """Re-grade patch under one MVP perturbation executor.

        Future steps: shared container prelude → perturbation-specific
        executor (``pi_vtest`` | ``pi_verif`` | ``pi_env``) →
        ``PerturbationResult`` + ``AuditRecord`` written beside harness log.
        """
        self.validate_perturbation_request(request)
        raise NotImplementedError(_EXEC_MESSAGE)
