"""Registry agreement table validation (schema only)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = (
    "instance_id",
    "artifact_id",
    "registry_a_version",
    "registry_b_version",
    "ef_registry_a",
    "ef_registry_b",
    "ef_delta",
    "rank_a",
    "rank_b",
    "rank_delta",
    "notes",
)


@dataclass(frozen=True, slots=True)
class RegistryAgreementValidationResult:
    path: Path
    row_count: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_registry_agreement_table(path: Path) -> RegistryAgreementValidationResult:
    """Validate a registry agreement comparison CSV."""
    resolved = path.resolve()
    if not resolved.is_file():
        return RegistryAgreementValidationResult(
            path=resolved,
            row_count=0,
            errors=(f"table file not found: {resolved}",),
        )

    errors: list[str] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return RegistryAgreementValidationResult(
                path=resolved,
                row_count=0,
                errors=(f"{resolved}: empty file or missing header row",),
            )

        header = [name.strip() for name in reader.fieldnames if name is not None]
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            errors.append(f"{resolved}: missing required columns: {', '.join(missing)}")

        seen_keys: set[tuple[str, str]] = set()
        row_count = 0
        for line_number, raw in enumerate(reader, start=2):
            row_count += 1
            prefix = f"{resolved}:{line_number}"
            instance_id = str(raw.get("instance_id", "")).strip()
            artifact_id = str(raw.get("artifact_id", "")).strip()
            if not instance_id:
                errors.append(f"{prefix}: instance_id must be non-empty")
            if not artifact_id:
                errors.append(f"{prefix}: artifact_id must be non-empty")
            key = (instance_id, artifact_id)
            if instance_id and artifact_id:
                if key in seen_keys:
                    errors.append(
                        f"{prefix}: duplicate (instance_id, artifact_id) "
                        f"{instance_id!r}, {artifact_id!r}"
                    )
                else:
                    seen_keys.add(key)

            for numeric_column in ("ef_registry_a", "ef_registry_b", "ef_delta"):
                value = str(raw.get(numeric_column, "")).strip()
                if value and _parse_optional_float(value) is None:
                    errors.append(f"{prefix}: {numeric_column} must be numeric or empty")

    return RegistryAgreementValidationResult(
        path=resolved,
        row_count=row_count,
        errors=tuple(errors),
    )


def _parse_optional_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
