"""``pi_env.v1`` — clean-slate hardened execution perturbation spec."""

from __future__ import annotations

from typing import Any

from earnbench.registry.base import (
    PerturbationSpec,
    _validate_field_types,
    _validate_required_fields,
)

PI_ENV_V1_ID = "pi_env.v1"

PI_ENV_V1_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["image_digest", "network_disabled"],
    "properties": {
        "image_digest": {
            "type": "string",
            "description": "Pinned container image digest for clean-slate execution.",
        },
        "network_disabled": {
            "type": "boolean",
            "description": "Disable outbound network inside the grading container.",
        },
        "python_nousersite": {
            "type": "boolean",
            "description": "Set PYTHONNOUSERSITE=1 during harness execution.",
        },
        "pip_no_index": {
            "type": "boolean",
            "description": "Set PIP_NO_INDEX=1 during harness execution.",
        },
        "tests_mount_readonly": {
            "type": "boolean",
            "description": "Mount official tests read-only in the container.",
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
        f"{PI_ENV_V1_ID} harness executor is not implemented. "
        f"instance_id={instance_id!r}, config_keys={sorted(config)}"
    )


def _validator(config: dict[str, Any]) -> list[str]:
    errors = _validate_required_fields(
        config,
        required=("image_digest", "network_disabled"),
        perturbation_id=PI_ENV_V1_ID,
    )
    errors.extend(
        _validate_field_types(
            config,
            perturbation_id=PI_ENV_V1_ID,
            properties=PI_ENV_V1_CONFIG_SCHEMA["properties"],
        )
    )
    image_digest = config.get("image_digest")
    if isinstance(image_digest, str) and not image_digest.strip():
        errors.append(f"{PI_ENV_V1_ID}: image_digest must be non-empty")
    return errors


PI_ENV_V1 = PerturbationSpec(
    id=PI_ENV_V1_ID,
    version="v1",
    name="Clean-slate hardened execution",
    description=(
        "Spawn a fresh container from a pinned image, apply the prod-only patch "
        "on the base snapshot, and execute tests under hardened environment "
        "flags to close environment shortcut channels."
    ),
    supported_channels=("environment_hijack",),
    config_schema=PI_ENV_V1_CONFIG_SCHEMA,
    expected_outputs=(
        "Y_env",
        "audit.status",
        "audit.success",
        "audit.image_digest",
        "audit.log_ref",
    ),
    executor_stub=_executor_stub,
    validator=_validator,
)
