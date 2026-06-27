"""``pi_verif.v1`` — pristine trusted verifier perturbation spec."""

from __future__ import annotations

from typing import Any

from earnbench.registry.base import (
    PerturbationSpec,
    _validate_field_types,
    _validate_required_fields,
)

PI_VERIF_V1_ID = "pi_verif.v1"

PI_VERIF_V1_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["protected_paths", "pristine_test_sha256"],
    "properties": {
        "protected_paths": {
            "type": "array",
            "description": "Glob patterns for verifier paths that must stay pristine.",
        },
        "pristine_test_sha256": {
            "type": "string",
            "description": "Expected sha256 of the pristine test tree tarball T(x).",
        },
        "require_trajectory_log": {
            "type": "boolean",
            "description": "When true, tamper detection may inspect trajectory writes.",
        },
        "eval_entrypoint_readonly": {
            "type": "boolean",
            "description": "Run harness entrypoint from a read-only mount.",
        },
    },
}


def _executor_stub(
    *,
    instance_id: str,
    patch: str,
    config: dict[str, Any],
) -> None:
    raise NotImplementedError(
        f"{PI_VERIF_V1_ID} harness executor is not implemented. "
        f"instance_id={instance_id!r}, config_keys={sorted(config)}"
    )


def _validator(config: dict[str, Any]) -> list[str]:
    errors = _validate_required_fields(
        config,
        required=("protected_paths", "pristine_test_sha256"),
        perturbation_id=PI_VERIF_V1_ID,
    )
    errors.extend(
        _validate_field_types(
            config,
            perturbation_id=PI_VERIF_V1_ID,
            properties=PI_VERIF_V1_CONFIG_SCHEMA["properties"],
        )
    )
    protected_paths = config.get("protected_paths")
    if isinstance(protected_paths, list) and not protected_paths:
        errors.append(f"{PI_VERIF_V1_ID}: protected_paths must be non-empty")
    pristine = config.get("pristine_test_sha256")
    if isinstance(pristine, str) and not pristine.strip():
        errors.append(f"{PI_VERIF_V1_ID}: pristine_test_sha256 must be non-empty")
    return errors


PI_VERIF_V1 = PerturbationSpec(
    id=PI_VERIF_V1_ID,
    version="v1",
    name="Pristine trusted verifier",
    description=(
        "Reset the test tree to a pristine tarball, apply the prod-only patch, "
        "and grade via a trusted out-of-workspace runner to detect verifier "
        "tampering and test-path edits."
    ),
    supported_channels=("verifier_tampering",),
    config_schema=PI_VERIF_V1_CONFIG_SCHEMA,
    expected_outputs=(
        "Y_verif",
        "audit.status",
        "audit.success",
        "audit.pristine_test_sha256",
        "audit.warnings",
    ),
    executor_stub=_executor_stub,
    validator=_validator,
)
