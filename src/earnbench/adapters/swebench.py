"""SWE-bench Verified adapter — smoke preparation and execution stub.

Smoke preparation (no Docker) loads Verified metadata, extracts golden and
prod-only patches, validates protected-path stripping, and writes a dry-run
execution plan for Phase A.

Harness execution remains ``NotImplementedError`` until a later release.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.base import (
    MVP_PERTURBATION_IDS,
    AdapterConfig,
    BenchmarkAdapter,
    EvaluationArtifact,
    NominalEvaluationRequest,
    PatchArtifact,
    PerturbationEvaluationRequest,
)
from earnbench.adapters.swebench_metadata import (
    MetadataLoadError,
    SWEBenchVerifiedRecord,
    load_verified_instance,
)
from earnbench.adapters.swebench_patch import (
    DEFAULT_PROTECTED_GLOBS,
    ProdPatchResult,
    extract_prod_patch,
    sha256_hex,
    validate_protected_path_stripping,
)
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID

_EXEC_MESSAGE = (
    "SWE-bench harness execution is not implemented in earnbench v0.1. "
    "Use prepare_smoke for dry-run artifacts; execution arrives in a later release."
)

DEFAULT_HOLDOUT_SALT = "earnbench_v0.1_holdout_salt"
DEFAULT_HOLDOUT_K = 2

_MISSING_FOR_REAL_EXECUTION: tuple[str, ...] = (
    "docker_image_digest",
    "pristine_test_tarball",
    "pristine_test_sha256",
    "holdout_manifest.json",
    "swebench_harness_package",
    "container_execution",
    "network_disabled_runtime",
)


@dataclass(frozen=True, slots=True)
class PiVerifPrepareBundle:
    """Request objects and config needed by ``pi_verif.v1`` (dry-run)."""

    perturbation_id: str
    config: dict[str, Any]
    evaluation_request: dict[str, Any]
    tamper_detected: bool
    stripped_paths: tuple[str, ...]


def holdout_partition(
    instance_id: str,
    fail_to_pass: tuple[str, ...],
    *,
    salt: str = DEFAULT_HOLDOUT_SALT,
    k: int = DEFAULT_HOLDOUT_K,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition F2P tests into holdout H(x) and visible V(x)."""
    holdout: list[str] = []
    visible: list[str] = []
    for test_name in fail_to_pass:
        digest = hashlib.sha256(
            f"{instance_id}:{salt}:{test_name}".encode()
        ).hexdigest()
        if int(digest, 16) % k == 0:
            holdout.append(test_name)
        else:
            visible.append(test_name)
    return tuple(holdout), tuple(visible)


def pi_vtest_viable(
    instance_id: str,
    fail_to_pass: tuple[str, ...],
    *,
    salt: str = DEFAULT_HOLDOUT_SALT,
    k: int = DEFAULT_HOLDOUT_K,
) -> bool:
    """Return whether ``pi_vtest.v1`` is schedulable for this instance."""
    if len(fail_to_pass) < 2:
        return False
    holdout, visible = holdout_partition(
        instance_id,
        fail_to_pass,
        salt=salt,
        k=k,
    )
    return len(holdout) >= 1 and len(visible) >= 1


def supported_perturbations(
    instance_id: str,
    fail_to_pass: tuple[str, ...],
) -> tuple[str, ...]:
    """Return perturbation ids supported for dry-run planning."""
    ordered = ("pi_vtest.v1", "pi_verif.v1", "pi_env.v1")
    if pi_vtest_viable(instance_id, fail_to_pass):
        return ordered
    return ("pi_verif.v1", "pi_env.v1")


def build_pi_verif_prepare_bundle(
    *,
    record: SWEBenchVerifiedRecord,
    prod_result: ProdPatchResult,
    config: AdapterConfig,
    run_id: str,
) -> PiVerifPrepareBundle:
    """Build ``pi_verif.v1`` config and evaluation request payloads."""
    prod_patch = PatchArtifact(
        content=prod_result.prod_patch,
        source="prod_only_patch",
        content_sha256=prod_result.prod_patch_sha256,
    )
    instance = record.to_benchmark_instance()
    request = PerturbationEvaluationRequest(
        run_id=run_id,
        instance=instance,
        patch=prod_patch,
        config=config,
        perturbation_id=PI_VERIF_V1_ID,
    )
    adapter = SWEBenchAdapter()
    adapter.validate_perturbation_request(request)

    pi_config: dict[str, Any] = {
        "protected_paths": list(DEFAULT_PROTECTED_GLOBS),
        "pristine_test_sha256": "",
        "require_trajectory_log": False,
        "eval_entrypoint_readonly": True,
    }
    return PiVerifPrepareBundle(
        perturbation_id=PI_VERIF_V1_ID,
        config=pi_config,
        evaluation_request={
            "run_id": request.run_id,
            "perturbation_id": request.perturbation_id,
            "task_id": request.instance.instance_id,
            "patch_sha256": request.patch.content_sha256,
            "config_digest": request.config.config_digest,
            "adapter_id": adapter.adapter_id,
        },
        tamper_detected=prod_result.tamper_detected,
        stripped_paths=prod_result.stripped_paths,
    )


def build_execution_plan(
    *,
    record: SWEBenchVerifiedRecord,
    prod_result: ProdPatchResult,
    pi_verif_bundle: PiVerifPrepareBundle,
) -> dict[str, Any]:
    """Build the dry-run ``plan.json`` payload."""
    scheduled = supported_perturbations(record.instance_id, record.fail_to_pass)
    missing = list(_MISSING_FOR_REAL_EXECUTION)
    if "pi_vtest.v1" in scheduled:
        missing.append("holdout_f2p_mount")
    if pi_verif_bundle.tamper_detected:
        missing.append("pi_verif_expected_failure_on_golden_tamper")

    return {
        "instance_id": record.instance_id,
        "repo": record.repo,
        "base_commit": record.base_commit,
        "raw_patch_sha256": prod_result.raw_patch_sha256,
        "prod_patch_sha256": prod_result.prod_patch_sha256,
        "stripped_paths": list(prod_result.stripped_paths),
        "protected_paths": list(DEFAULT_PROTECTED_GLOBS),
        "supported_perturbations": list(scheduled),
        "missing_inputs_for_real_execution": missing,
        "dry_run": True,
        "execution_mode": "prepare_smoke",
        "planned_runs": [
            {
                "phase": "nominal",
                "description": (
                    "Checkout base_commit, apply prod_only.patch, run F2P+P2P"
                ),
                "docker": False,
            },
            *[
                {
                    "phase": perturbation_id,
                    "description": (
                        f"Execute {perturbation_id} executor in fresh container"
                    ),
                    "docker": False,
                }
                for perturbation_id in scheduled
            ],
        ],
        "pi_verif_prepare": {
            "perturbation_id": pi_verif_bundle.perturbation_id,
            "config": pi_verif_bundle.config,
            "evaluation_request": pi_verif_bundle.evaluation_request,
            "tamper_detected": pi_verif_bundle.tamper_detected,
            "stripped_paths": list(pi_verif_bundle.stripped_paths),
        },
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare_smoke(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    run_id: str | None = None,
    dataset_revision: str = "unpinned",
) -> dict[str, Any]:
    """Prepare smoke-test artifacts without running Docker or SWE-bench.

    Creates ``<output>/<instance_id>/`` with ``meta.json``, patch files, and
    ``plan.json``. Returns the plan payload.
    """
    record = load_verified_instance(metadata_path, instance_id)
    raw_patch = record.golden_patch
    prod_result = extract_prod_patch(raw_patch)
    validate_protected_path_stripping(prod_result)
    if prod_result.empty_after_strip:
        msg = f"{instance_id}: prod-only patch is empty after protected-path stripping"
        raise ValueError(msg)

    effective_run_id = run_id or f"smoke_{instance_id}"
    config = AdapterConfig(
        dataset_revision=dataset_revision,
        holdout_salt=DEFAULT_HOLDOUT_SALT,
    )
    pi_verif_bundle = build_pi_verif_prepare_bundle(
        record=record,
        prod_result=prod_result,
        config=config,
        run_id=effective_run_id,
    )
    plan = build_execution_plan(
        record=record,
        prod_result=prod_result,
        pi_verif_bundle=pi_verif_bundle,
    )

    instance_dir = output_dir / instance_id
    patch_dir = instance_dir / "patch"
    patch_dir.mkdir(parents=True, exist_ok=True)

    (patch_dir / "raw.patch").write_text(raw_patch, encoding="utf-8")
    (patch_dir / "prod_only.patch").write_text(prod_result.prod_patch, encoding="utf-8")

    meta = {
        "instance_id": record.instance_id,
        "repo": record.repo,
        "base_commit": record.base_commit,
        "dataset_name": record.dataset_name,
        "fail_to_pass": list(record.fail_to_pass),
        "pass_to_pass": list(record.pass_to_pass),
        "metadata_source": str(metadata_path),
        "raw_patch_sha256": prod_result.raw_patch_sha256,
        "prod_patch_sha256": prod_result.prod_patch_sha256,
        "stripped_paths": list(prod_result.stripped_paths),
        "prod_paths": list(prod_result.prod_paths),
        "empty_after_strip": prod_result.empty_after_strip,
        "run_id": effective_run_id,
        "config_digest": config.config_digest,
    }
    _write_json(instance_dir / "meta.json", meta)
    _write_json(instance_dir / "plan.json", plan)

    return plan


def prepare_exploit(
    *,
    metadata_path: Path,
    instance_id: str,
    exploit_id: str,
    patch_content: str,
    output_dir: Path,
    run_id: str | None = None,
    dataset_revision: str = "unpinned",
    patch_class: str = "exploit_planted",
    y0_policy: str = "prod_only",
    channel: str = "",
    family: str = "",
    template_id: str = "",
    predicted_fail_pi: str = "",
) -> dict[str, Any]:
    """Prepare exploit-run artifacts without executing Docker.

    Writes ``<output>/<instance_id>/`` with exploit patch files, ``meta.json``,
    and ``plan.json`` mirroring ``prepare_smoke`` layout.
    """
    record = load_verified_instance(metadata_path, instance_id)
    prod_result = extract_prod_patch(patch_content)
    validate_protected_path_stripping(prod_result)
    if not patch_content.strip():
        msg = f"{exploit_id}: exploit patch is empty"
        raise ValueError(msg)
    if prod_result.empty_after_strip:
        if y0_policy == "prod_only":
            prod_result = ProdPatchResult(
                prod_patch=patch_content if patch_content.endswith("\n") else patch_content + "\n",
                prod_paths=prod_result.prod_paths or ("earnbench_planted/",),
                stripped_paths=prod_result.stripped_paths,
                stripped_hunks=prod_result.stripped_hunks,
                empty_after_strip=False,
                raw_patch_sha256=prod_result.raw_patch_sha256,
                prod_patch_sha256=sha256_hex(
                    patch_content if patch_content.endswith("\n") else patch_content + "\n"
                ),
            )
        elif y0_policy == "raw_full":
            pass
        else:
            msg = (
                f"{exploit_id}: prod-only patch is empty after protected-path stripping"
            )
            raise ValueError(msg)

    effective_run_id = run_id or f"phase_b_{exploit_id}"
    config = AdapterConfig(
        dataset_revision=dataset_revision,
        holdout_salt=DEFAULT_HOLDOUT_SALT,
    )
    pi_verif_bundle = build_pi_verif_prepare_bundle(
        record=record,
        prod_result=prod_result,
        config=config,
        run_id=effective_run_id,
    )
    plan = build_execution_plan(
        record=record,
        prod_result=prod_result,
        pi_verif_bundle=pi_verif_bundle,
    )

    instance_dir = output_dir / instance_id
    patch_dir = instance_dir / "patch"
    patch_dir.mkdir(parents=True, exist_ok=True)

    (patch_dir / "raw.patch").write_text(patch_content, encoding="utf-8")
    (patch_dir / "prod_only.patch").write_text(prod_result.prod_patch, encoding="utf-8")

    meta = {
        "instance_id": record.instance_id,
        "exploit_id": exploit_id,
        "repo": record.repo,
        "base_commit": record.base_commit,
        "dataset_name": record.dataset_name,
        "fail_to_pass": list(record.fail_to_pass),
        "pass_to_pass": list(record.pass_to_pass),
        "metadata_source": str(metadata_path),
        "raw_patch_sha256": prod_result.raw_patch_sha256,
        "prod_patch_sha256": prod_result.prod_patch_sha256,
        "stripped_paths": list(prod_result.stripped_paths),
        "prod_paths": list(prod_result.prod_paths),
        "empty_after_strip": prod_result.empty_after_strip,
        "run_id": effective_run_id,
        "config_digest": config.config_digest,
        "patch_class": patch_class,
        "y0_policy": y0_policy or "prod_only",
        "channel": channel,
        "family": family,
        "template_id": template_id,
        "predicted_fail_pi": predicted_fail_pi,
    }
    _write_json(instance_dir / "meta.json", meta)
    _write_json(instance_dir / "plan.json", plan)
    return plan


class SWEBenchAdapter(BenchmarkAdapter):
    """Post-hoc SWE-bench Verified adapter (validation + smoke prep in v0.1)."""

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
        """Re-grade patch under the nominal SWE-bench predicate (not implemented)."""
        self.validate_nominal_request(request)
        raise NotImplementedError(_EXEC_MESSAGE)

    def evaluate_perturbation(
        self,
        request: PerturbationEvaluationRequest,
    ) -> EvaluationArtifact:
        """Re-grade patch under one MVP perturbation executor (not implemented)."""
        self.validate_perturbation_request(request)
        raise NotImplementedError(_EXEC_MESSAGE)


__all__ = [
    "MetadataLoadError",
    "PiVerifPrepareBundle",
    "SWEBenchAdapter",
    "SWEBenchVerifiedRecord",
    "build_execution_plan",
    "build_pi_verif_prepare_bundle",
    "holdout_partition",
    "load_verified_instance",
    "pi_vtest_viable",
    "prepare_smoke",
    "supported_perturbations",
]
