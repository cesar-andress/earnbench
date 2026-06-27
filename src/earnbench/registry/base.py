"""Base types for the EarnBench perturbation registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class PerturbationSpec:
    """Metadata and hooks for one versioned perturbation in Π."""

    id: str
    version: str
    name: str
    description: str
    supported_channels: tuple[str, ...]
    config_schema: dict[str, Any]
    expected_outputs: tuple[str, ...]
    executor_stub: Callable[..., None] = field(repr=False, compare=False)
    validator: Callable[[dict[str, Any]], list[str]] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.id:
            msg = "perturbation id must be non-empty"
            raise ValueError(msg)
        if not self.version:
            msg = "perturbation version must be non-empty"
            raise ValueError(msg)
        if not self.name:
            msg = "perturbation name must be non-empty"
            raise ValueError(msg)
        if not self.supported_channels:
            msg = "supported_channels must be non-empty"
            raise ValueError(msg)
        if not self.expected_outputs:
            msg = "expected_outputs must be non-empty"
            raise ValueError(msg)

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate a perturbation config dict; return human-readable errors."""
        return self.validator(config)

    def to_dict(self) -> dict[str, Any]:
        """Serialize registry metadata (excluding callables)."""
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "supported_channels": list(self.supported_channels),
            "config_schema": self.config_schema,
            "expected_outputs": list(self.expected_outputs),
            "executor_stub": f"{self.id}.executor",
        }


def _validate_required_fields(
    config: dict[str, Any],
    *,
    required: tuple[str, ...],
    perturbation_id: str,
) -> list[str]:
    errors: list[str] = []
    for key in required:
        if key not in config:
            errors.append(f"{perturbation_id}: missing required config field '{key}'")
    return errors


def _validate_field_types(
    config: dict[str, Any],
    *,
    perturbation_id: str,
    properties: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for key, spec in properties.items():
        if key not in config:
            continue
        value = config[key]
        expected_type = spec.get("type")
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"{perturbation_id}: config field '{key}' must be a string")
        elif expected_type == "integer" and not isinstance(value, int):
            errors.append(f"{perturbation_id}: config field '{key}' must be an integer")
        elif expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{perturbation_id}: config field '{key}' must be a boolean")
        elif expected_type == "array" and not isinstance(value, list):
            errors.append(f"{perturbation_id}: config field '{key}' must be an array")
        minimum = spec.get("minimum")
        if (
            expected_type == "integer"
            and isinstance(value, int)
            and minimum is not None
            and value < minimum
        ):
            errors.append(
                f"{perturbation_id}: config field '{key}' must be >= {minimum}"
            )
    return errors
