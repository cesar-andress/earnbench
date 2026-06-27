"""Maintainer-certified correctness control manifest validation."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = (
    "control_id",
    "instance_id",
    "repo",
    "upstream_commit",
    "upstream_pr",
    "upstream_issue",
    "patch_source",
    "patch_sha256",
    "merged_by_maintainer",
    "issue_closed",
    "production_only",
    "touches_tests",
    "touches_verifier",
    "touches_ci",
    "touches_environment",
    "nominal_success",
    "certification_status",
    "exclusion_reason",
    "notes",
)

CERTIFICATION_STATUSES = frozenset({"certified_correct", "rejected", "undecidable"})
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


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
    """Validate a maintainer-certified controls manifest CSV."""
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

            bool_fields = {
                "merged_by_maintainer": "merged_by_maintainer",
                "issue_closed": "issue_closed",
                "production_only": "production_only",
                "touches_tests": "touches_tests",
                "touches_verifier": "touches_verifier",
                "touches_ci": "touches_ci",
                "touches_environment": "touches_environment",
                "nominal_success": "nominal_success",
            }
            parsed: dict[str, bool | None] = {}
            for key, column in bool_fields.items():
                value, error = _parse_bool(raw.get(column), prefix=f"{prefix}:{column}")
                if error:
                    errors.append(error)
                parsed[key] = value

            if status == "certified_correct":
                required_true = (
                    ("merged_by_maintainer", parsed["merged_by_maintainer"]),
                    ("issue_closed", parsed["issue_closed"]),
                    ("production_only", parsed["production_only"]),
                    ("nominal_success", parsed["nominal_success"]),
                )
                for field_name, field_value in required_true:
                    if field_value is not True:
                        errors.append(
                            f"{prefix}: certified_correct row must have "
                            f"{field_name}=True"
                        )

                required_false = (
                    ("touches_tests", parsed["touches_tests"]),
                    ("touches_verifier", parsed["touches_verifier"]),
                    ("touches_ci", parsed["touches_ci"]),
                    ("touches_environment", parsed["touches_environment"]),
                )
                for field_name, field_value in required_false:
                    if field_value is not False:
                        errors.append(
                            f"{prefix}: certified_correct row must have "
                            f"{field_name}=False"
                        )

                upstream_commit = str(raw.get("upstream_commit", "")).strip()
                if not upstream_commit:
                    errors.append(
                        f"{prefix}: certified_correct row must have non-empty "
                        "upstream_commit"
                    )

                patch_sha256 = str(raw.get("patch_sha256", "")).strip()
                if not patch_sha256:
                    errors.append(
                        f"{prefix}: certified_correct row must have non-empty "
                        "patch_sha256"
                    )
                elif not SHA256_PATTERN.match(patch_sha256):
                    errors.append(
                        f"{prefix}: patch_sha256 must be 64 hex characters"
                    )

            if status == "undecidable":
                reason = str(raw.get("exclusion_reason", "")).strip()
                if not reason:
                    errors.append(
                        f"{prefix}: undecidable row must have non-empty "
                        "exclusion_reason"
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
