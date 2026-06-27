"""Certified correct control manifest validation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = (
    "control_id",
    "instance_id",
    "repo",
    "patch_source",
    "patch_ref",
    "upstream_commit",
    "issue_ref",
    "certification_basis",
    "production_only",
    "touches_tests",
    "touches_verifier",
    "touches_environment",
    "minimality_score",
    "issue_alignment_score",
    "certification_status",
    "undecidable_reason",
    "notes",
)

CERTIFICATION_STATUSES = frozenset({"certified_correct", "rejected", "undecidable"})


@dataclass(frozen=True, slots=True)
class ManifestValidationResult:
    path: Path
    row_count: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _parse_bool(value: object, *, prefix: str) -> tuple[bool | None, str | None]:
    if value is None or str(value).strip() == "":
        return None, f"{prefix}: boolean field must be non-empty"
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True, None
    if text in {"0", "false", "no"}:
        return False, None
    return None, f"{prefix}: invalid boolean value {value!r}"


def validate_certified_controls_manifest(path: Path) -> ManifestValidationResult:
    """Validate a certified correct controls manifest CSV."""
    resolved = path.resolve()
    if not resolved.is_file():
        return ManifestValidationResult(
            path=resolved,
            row_count=0,
            errors=(f"manifest file not found: {resolved}",),
        )

    errors: list[str] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return ManifestValidationResult(
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

            control_id = str(raw.get("control_id", "")).strip()
            if not control_id:
                errors.append(f"{prefix}: control_id must be non-empty")
            elif control_id in seen_ids:
                errors.append(f"{prefix}: duplicate control_id {control_id!r}")
            else:
                seen_ids.add(control_id)

            instance_id = str(raw.get("instance_id", "")).strip()
            if not instance_id:
                errors.append(f"{prefix}: instance_id must be non-empty")

            status = str(raw.get("certification_status", "")).strip()
            if status not in CERTIFICATION_STATUSES:
                errors.append(
                    f"{prefix}: certification_status must be one of "
                    f"{sorted(CERTIFICATION_STATUSES)}, got {status!r}"
                )

            production_only, prod_error = _parse_bool(
                raw.get("production_only"),
                prefix=f"{prefix}:production_only",
            )
            if prod_error:
                errors.append(prod_error)

            touches_tests, tests_error = _parse_bool(
                raw.get("touches_tests"),
                prefix=f"{prefix}:touches_tests",
            )
            if tests_error:
                errors.append(tests_error)

            touches_verifier, verif_error = _parse_bool(
                raw.get("touches_verifier"),
                prefix=f"{prefix}:touches_verifier",
            )
            if verif_error:
                errors.append(verif_error)

            touches_environment, env_error = _parse_bool(
                raw.get("touches_environment"),
                prefix=f"{prefix}:touches_environment",
            )
            if env_error:
                errors.append(env_error)

            if status == "certified_correct":
                if touches_tests is True:
                    errors.append(
                        f"{prefix}: certified_correct row must not have "
                        "touches_tests=True"
                    )
                if touches_verifier is True:
                    errors.append(
                        f"{prefix}: certified_correct row must not have "
                        "touches_verifier=True"
                    )
                if touches_environment is True:
                    errors.append(
                        f"{prefix}: certified_correct row must not have "
                        "touches_environment=True"
                    )
                if production_only is False:
                    errors.append(
                        f"{prefix}: certified_correct row must have "
                        "production_only=True"
                    )

            if status == "undecidable":
                reason = str(raw.get("undecidable_reason", "")).strip()
                if not reason:
                    errors.append(
                        f"{prefix}: undecidable row must have non-empty "
                        "undecidable_reason"
                    )

    return ManifestValidationResult(
        path=resolved,
        row_count=row_count,
        errors=tuple(errors),
    )


def load_certified_controls_manifest(path: Path) -> list[dict[str, str]]:
    """Load manifest rows after schema validation."""
    result = validate_certified_controls_manifest(path)
    if not result.ok:
        msg = "; ".join(result.errors)
        raise ValueError(msg)
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]
