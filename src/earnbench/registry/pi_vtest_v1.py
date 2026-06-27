"""``pi_vtest.v1`` — holdout F2P re-grade perturbation spec."""

from __future__ import annotations

from typing import Any

from earnbench.registry.base import (
    PerturbationSpec,
    _validate_field_types,
    _validate_required_fields,
)

PI_VTEST_V1_ID = "pi_vtest.v1"

PI_VTEST_V1_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["holdout_salt", "holdout_k"],
    "properties": {
        "holdout_salt": {
            "type": "string",
            "description": "Deterministic salt for holdout partition H(x).",
        },
        "holdout_k": {
            "type": "integer",
            "minimum": 2,
            "description": "Modulus K for hash(instance_id + salt + test) mod K.",
        },
        "holdout_manifest_ref": {
            "type": "string",
            "description": "Optional holdout_manifest.json path or digest reference.",
        },
        "include_visible_f2p": {
            "type": "boolean",
            "description": "When true, grade visible F2P union holdout F2P union P2P.",
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
        f"{PI_VTEST_V1_ID} harness executor is not implemented. "
        f"instance_id={instance_id!r}, config_keys={sorted(config)}"
    )


def _validator(config: dict[str, Any]) -> list[str]:
    errors = _validate_required_fields(
        config,
        required=("holdout_salt", "holdout_k"),
        perturbation_id=PI_VTEST_V1_ID,
    )
    errors.extend(
        _validate_field_types(
            config,
            perturbation_id=PI_VTEST_V1_ID,
            properties=PI_VTEST_V1_CONFIG_SCHEMA["properties"],
        )
    )
    holdout_k = config.get("holdout_k")
    if isinstance(holdout_k, int) and holdout_k < 2:
        errors.append(f"{PI_VTEST_V1_ID}: holdout_k must be >= 2")
    return errors


PI_VTEST_V1 = PerturbationSpec(
    id=PI_VTEST_V1_ID,
    version="v1",
    name="Holdout F2P re-grade",
    description=(
        "Re-grade a prod-only patch under an expanded test predicate that "
        "includes withheld fail-to-pass tests H(x), closing visible-test "
        "overfitting and partial hidden-oracle inference."
    ),
    supported_channels=(
        "visible_test_overfitting",
        "hidden_oracle_inference",
    ),
    config_schema=PI_VTEST_V1_CONFIG_SCHEMA,
    expected_outputs=(
        "Y_vtest",
        "audit.status",
        "audit.success",
        "audit.tests_run",
        "audit.config_digest",
    ),
    executor_stub=_executor_stub,
    validator=_validator,
)
