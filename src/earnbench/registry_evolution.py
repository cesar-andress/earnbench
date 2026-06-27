"""Registry evolution scenario validation (schema only)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_KEYS = (
    "scenario_id",
    "base_registry_version",
    "target_registry_version",
    "added_pi",
    "removed_pi",
    "frozen_instance_set",
    "expected_coverage_delta",
    "notes",
)


@dataclass(frozen=True, slots=True)
class RegistryEvolutionValidationResult:
    path: Path
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_registry_evolution_scenario(path: Path) -> RegistryEvolutionValidationResult:
    """Validate a registry evolution scenario JSON file."""
    resolved = path.resolve()
    if not resolved.is_file():
        return RegistryEvolutionValidationResult(
            path=resolved,
            errors=(f"scenario file not found: {resolved}",),
        )

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return RegistryEvolutionValidationResult(
            path=resolved,
            errors=(f"{resolved}: invalid JSON: {exc}",),
        )

    if not isinstance(payload, dict):
        return RegistryEvolutionValidationResult(
            path=resolved,
            errors=(f"{resolved}: root must be a JSON object",),
        )

    errors: list[str] = []
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        errors.append(f"{resolved}: missing required keys: {', '.join(missing)}")

    for list_key in ("added_pi", "removed_pi"):
        value = payload.get(list_key)
        if value is not None and not isinstance(value, list):
            errors.append(f"{resolved}: {list_key} must be a JSON array")

    scenario_id = str(payload.get("scenario_id", "")).strip()
    if "scenario_id" in payload and not scenario_id:
        errors.append(f"{resolved}: scenario_id must be non-empty")

    delta = payload.get("expected_coverage_delta")
    if delta is not None and not isinstance(delta, (int, float)):
        errors.append(f"{resolved}: expected_coverage_delta must be numeric")

    frozen = payload.get("frozen_instance_set")
    if frozen is not None and not isinstance(frozen, (str, list)):
        errors.append(f"{resolved}: frozen_instance_set must be string or array")

    return RegistryEvolutionValidationResult(path=resolved, errors=tuple(errors))


def scenario_summary(path: Path) -> dict[str, Any]:
    """Return normalized scenario metadata after validation."""
    result = validate_registry_evolution_scenario(path)
    if not result.ok:
        msg = "; ".join(result.errors)
        raise ValueError(msg)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "scenario_id": payload["scenario_id"],
        "base_registry_version": payload["base_registry_version"],
        "target_registry_version": payload["target_registry_version"],
        "added_pi_count": len(payload.get("added_pi", [])),
        "removed_pi_count": len(payload.get("removed_pi", [])),
    }
