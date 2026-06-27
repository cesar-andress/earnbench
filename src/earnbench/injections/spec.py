"""Blinded injection specification schema for construct-validity controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REQUIRED_FIELDS = (
    "injection_id",
    "instance_id",
    "paired_clean_patch_ref",
    "injected_patch_ref",
    "injected_channel",
    "in_registry",
    "expected_failed_pi",
    "expected_ef_exclude_invalid",
    "expected_ef_invalid_as_fail",
    "blinding_group",
    "generation_seed",
    "template_id",
)

OPTIONAL_FIELDS = ("notes",)

IN_REGISTRY_CHANNELS = frozenset(
    {
        "visible_test_overfitting",
        "verifier_tampering",
        "environment_hijack",
    }
)

OUT_OF_REGISTRY_CHANNELS = frozenset(
    {
        "metadata_leakage",
        "retrieval_contamination",
        "patch_shape_gaming",
    }
)

KNOWN_CHANNELS = IN_REGISTRY_CHANNELS | OUT_OF_REGISTRY_CHANNELS

CHANNEL_TO_PI = {
    "visible_test_overfitting": "pi_vtest.v1",
    "verifier_tampering": "pi_verif.v1",
    "environment_hijack": "pi_env.v1",
}

REGISTRY_PI = frozenset(CHANNEL_TO_PI.values())

EXPECTED_FAILED_PI_VALUES = REGISTRY_PI | frozenset({"none"})


@dataclass(frozen=True, slots=True)
class InjectionSpec:
    """Ground-truth metadata for one blinded mechanism injection pair."""

    injection_id: str
    instance_id: str
    paired_clean_patch_ref: str
    injected_patch_ref: str
    injected_channel: str
    in_registry: bool
    expected_failed_pi: str
    expected_ef_exclude_invalid: float | None
    expected_ef_invalid_as_fail: float | None
    blinding_group: str
    generation_seed: str
    template_id: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the injection spec to a JSON-compatible mapping."""
        payload: dict[str, Any] = {
            "injection_id": self.injection_id,
            "instance_id": self.instance_id,
            "paired_clean_patch_ref": self.paired_clean_patch_ref,
            "injected_patch_ref": self.injected_patch_ref,
            "injected_channel": self.injected_channel,
            "in_registry": self.in_registry,
            "expected_failed_pi": self.expected_failed_pi,
            "expected_ef_exclude_invalid": self.expected_ef_exclude_invalid,
            "expected_ef_invalid_as_fail": self.expected_ef_invalid_as_fail,
            "blinding_group": self.blinding_group,
            "generation_seed": self.generation_seed,
            "template_id": self.template_id,
        }
        if self.notes:
            payload["notes"] = self.notes
        return payload

    def validate(self) -> list[str]:
        """Return human-readable validation errors for this spec."""
        errors: list[str] = []
        prefix = self.injection_id or "<missing injection_id>"

        if not self.injection_id.strip():
            errors.append("injection_id must be a non-empty string")
        if not self.instance_id.strip():
            errors.append(f"{prefix}: instance_id must be a non-empty string")
        if not self.paired_clean_patch_ref.strip():
            errors.append(
                f"{prefix}: paired_clean_patch_ref must be a non-empty string"
            )
        if not self.injected_patch_ref.strip():
            errors.append(f"{prefix}: injected_patch_ref must be a non-empty string")
        if self.paired_clean_patch_ref.strip() == self.injected_patch_ref.strip():
            errors.append(
                f"{prefix}: paired_clean_patch_ref and injected_patch_ref must differ"
            )

        if not self.injected_channel.strip():
            errors.append(f"{prefix}: injected_channel must be a non-empty string")
        elif self.injected_channel not in KNOWN_CHANNELS:
            errors.append(
                f"{prefix}: injected_channel {self.injected_channel!r} is not known "
                f"(known: {', '.join(sorted(KNOWN_CHANNELS))})"
            )

        if not isinstance(self.in_registry, bool):
            errors.append(f"{prefix}: in_registry must be a boolean")

        if not self.expected_failed_pi.strip():
            errors.append(f"{prefix}: expected_failed_pi must be a non-empty string")
        elif self.expected_failed_pi not in EXPECTED_FAILED_PI_VALUES:
            allowed = ", ".join(sorted(EXPECTED_FAILED_PI_VALUES))
            errors.append(
                f"{prefix}: expected_failed_pi {self.expected_failed_pi!r} "
                f"must be one of {allowed}"
            )

        for field_name, value in (
            ("expected_ef_exclude_invalid", self.expected_ef_exclude_invalid),
            ("expected_ef_invalid_as_fail", self.expected_ef_invalid_as_fail),
        ):
            if value is not None and not isinstance(value, (int, float)):
                errors.append(f"{prefix}: {field_name} must be a number or null")
            elif isinstance(value, (int, float)) and (value < 0.0 or value > 1.0):
                errors.append(f"{prefix}: {field_name} must be between 0 and 1")

        if not self.blinding_group.strip():
            errors.append(f"{prefix}: blinding_group must be a non-empty string")
        if not self.generation_seed.strip():
            errors.append(f"{prefix}: generation_seed must be a non-empty string")
        if not self.template_id.strip():
            errors.append(f"{prefix}: template_id must be a non-empty string")
        if not isinstance(self.notes, str):
            errors.append(f"{prefix}: notes must be a string")

        errors.extend(_validate_registry_consistency(self))
        return errors

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InjectionSpec:
        """Parse an injection spec from a mapping."""
        if not isinstance(data, dict):
            msg = "injection spec must be a mapping"
            raise TypeError(msg)

        missing = [name for name in REQUIRED_FIELDS if name not in data]
        if missing:
            msg = f"injection spec missing required fields: {', '.join(missing)}"
            raise ValueError(msg)

        return cls(
            injection_id=str(data["injection_id"]),
            instance_id=str(data["instance_id"]),
            paired_clean_patch_ref=str(data["paired_clean_patch_ref"]),
            injected_patch_ref=str(data["injected_patch_ref"]),
            injected_channel=str(data["injected_channel"]),
            in_registry=bool(data["in_registry"]),
            expected_failed_pi=str(data["expected_failed_pi"]),
            expected_ef_exclude_invalid=_coerce_optional_float(
                data["expected_ef_exclude_invalid"]
            ),
            expected_ef_invalid_as_fail=_coerce_optional_float(
                data["expected_ef_invalid_as_fail"]
            ),
            blinding_group=str(data["blinding_group"]),
            generation_seed=str(data["generation_seed"]),
            template_id=str(data["template_id"]),
            notes=str(data.get("notes", "")),
        )


def _validate_registry_consistency(spec: InjectionSpec) -> list[str]:
    errors: list[str] = []
    prefix = spec.injection_id
    channel = spec.injected_channel
    in_registry = channel in IN_REGISTRY_CHANNELS
    out_of_registry = channel in OUT_OF_REGISTRY_CHANNELS

    if in_registry and not spec.in_registry:
        errors.append(f"{prefix}: in_registry must be true for channel {channel!r}")
    if out_of_registry and spec.in_registry:
        errors.append(f"{prefix}: in_registry must be false for channel {channel!r}")

    if spec.in_registry and channel in CHANNEL_TO_PI:
        expected_pi = CHANNEL_TO_PI[channel]
        if spec.expected_failed_pi != expected_pi:
            errors.append(
                f"{prefix}: expected_failed_pi must be {expected_pi!r} "
                f"for in-registry channel {channel!r}"
            )
        for field_name, value in (
            ("expected_ef_exclude_invalid", spec.expected_ef_exclude_invalid),
            ("expected_ef_invalid_as_fail", spec.expected_ef_invalid_as_fail),
        ):
            if value is None:
                errors.append(
                    f"{prefix}: {field_name} is required when in_registry is true"
                )

    if not spec.in_registry:
        if spec.expected_failed_pi != "none":
            errors.append(
                f"{prefix}: expected_failed_pi must be 'none' when in_registry is false"
            )

    return errors


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    msg = f"expected number or null, got {type(value).__name__}"
    raise TypeError(msg)
