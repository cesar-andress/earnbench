"""Stress-test scenario catalog validation (schema only)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = (
    "stress_id",
    "stress_class",
    "target_stage",
    "target_pi",
    "parameter",
    "parameter_value",
    "expected_validator_behavior",
    "expected_ef_drift",
    "notes",
)

STRESS_CLASSES = frozenset(
    {
        "timeout",
        "concurrency",
        "resource_limit",
        "flaky_env",
        "corrupt_artifact",
    }
)


@dataclass(frozen=True, slots=True)
class StressCatalogValidationResult:
    path: Path
    row_count: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_stress_test_catalog(path: Path) -> StressCatalogValidationResult:
    """Validate a stress-test scenario catalog CSV."""
    resolved = path.resolve()
    if not resolved.is_file():
        return StressCatalogValidationResult(
            path=resolved,
            row_count=0,
            errors=(f"catalog file not found: {resolved}",),
        )

    errors: list[str] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return StressCatalogValidationResult(
                path=resolved,
                row_count=0,
                errors=(f"{resolved}: empty file or missing header row",),
            )

        header = [name.strip() for name in reader.fieldnames if name is not None]
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            errors.append(f"{resolved}: missing required columns: {', '.join(missing)}")

        seen_ids: set[str] = set()
        row_count = 0
        for line_number, raw in enumerate(reader, start=2):
            row_count += 1
            prefix = f"{resolved}:{line_number}"
            stress_id = str(raw.get("stress_id", "")).strip()
            if not stress_id:
                errors.append(f"{prefix}: stress_id must be non-empty")
            elif stress_id in seen_ids:
                errors.append(f"{prefix}: duplicate stress_id {stress_id!r}")
            else:
                seen_ids.add(stress_id)

            stress_class = str(raw.get("stress_class", "")).strip()
            if stress_class and stress_class not in STRESS_CLASSES:
                errors.append(
                    f"{prefix}: unknown stress_class {stress_class!r}; "
                    f"expected one of {sorted(STRESS_CLASSES)}"
                )

    return StressCatalogValidationResult(
        path=resolved,
        row_count=row_count,
        errors=tuple(errors),
    )
