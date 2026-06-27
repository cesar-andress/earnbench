"""External unearned anchor catalog validation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = (
    "external_id",
    "source",
    "paper_or_url",
    "original_benchmark",
    "original_task_id",
    "external_label",
    "external_label_quote",
    "external_label_type",
    "artifact_available",
    "patch_available",
    "trace_available",
    "reproducible",
    "mapped_channel",
    "registry_label",
    "expected_failed_pi",
    "expected_ef_behavior",
    "expected_detection",
    "inclusion_decision",
    "exclusion_reason",
    "notes",
)

EXTERNAL_LABEL_TYPES = frozenset(
    {
        "reward_hacking",
        "benchmark_exploit",
        "hacked_resolved",
        "shortcut_success",
        "impossible_success",
        "verifier_tampering",
        "future_history_leakage",
        "visible_test_overfit",
        "other",
    }
)

REGISTRY_LABELS = frozenset({"IN_REGISTRY", "OUT_OF_REGISTRY", "UNKNOWN"})

EXPECTED_DETECTION_VALUES = frozenset({"detect", "miss_expected", "unknown"})

INCLUSION_DECISIONS = frozenset({"include", "exclude", "defer"})


@dataclass(frozen=True, slots=True)
class ExternalUnearnedCatalogValidationResult:
    path: Path
    row_count: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_external_unearned_catalog(path: Path) -> ExternalUnearnedCatalogValidationResult:
    """Validate an external unearned anchor catalog CSV."""
    resolved = path.resolve()
    if not resolved.is_file():
        return ExternalUnearnedCatalogValidationResult(
            path=resolved,
            row_count=0,
            errors=(f"catalog file not found: {resolved}",),
        )

    errors: list[str] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return ExternalUnearnedCatalogValidationResult(
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
            if raw is None:
                continue
            row_count += 1
            prefix = f"{resolved}:{line_number}"

            external_id = str(raw.get("external_id", "")).strip()
            if not external_id:
                errors.append(f"{prefix}: external_id must be non-empty")
            elif external_id in seen_ids:
                errors.append(f"{prefix}: duplicate external_id {external_id!r}")
            else:
                seen_ids.add(external_id)

            external_label = str(raw.get("external_label", "")).strip()
            if not external_label:
                errors.append(f"{prefix}: external_label must be non-empty")

            label_type = str(raw.get("external_label_type", "")).strip()
            if label_type not in EXTERNAL_LABEL_TYPES:
                errors.append(
                    f"{prefix}: external_label_type {label_type!r} must be one of "
                    f"{sorted(EXTERNAL_LABEL_TYPES)}"
                )

            registry_label = str(raw.get("registry_label", "")).strip().upper()
            if registry_label not in REGISTRY_LABELS:
                errors.append(
                    f"{prefix}: registry_label must be one of {sorted(REGISTRY_LABELS)}"
                )

            expected_detection = str(raw.get("expected_detection", "")).strip()
            if expected_detection not in EXPECTED_DETECTION_VALUES:
                errors.append(
                    f"{prefix}: expected_detection must be one of "
                    f"{sorted(EXPECTED_DETECTION_VALUES)}"
                )

            inclusion_decision = str(raw.get("inclusion_decision", "")).strip()
            if inclusion_decision not in INCLUSION_DECISIONS:
                errors.append(
                    f"{prefix}: inclusion_decision must be one of "
                    f"{sorted(INCLUSION_DECISIONS)}"
                )

            mapped_channel = str(raw.get("mapped_channel", "")).strip()
            if not mapped_channel:
                errors.append(f"{prefix}: mapped_channel must be non-empty")

        if row_count == 0 and not errors:
            errors.append(f"{resolved}: catalog contains no data rows")

    return ExternalUnearnedCatalogValidationResult(
        path=resolved,
        row_count=row_count,
        errors=tuple(errors),
    )


def load_external_unearned_catalog(path: Path) -> list[dict[str, str]]:
    """Load catalog rows after validation."""
    result = validate_external_unearned_catalog(path)
    if not result.ok:
        msg = "; ".join(result.errors)
        raise ValueError(msg)
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]
