"""Execution manifest for external unearned anchor grading."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from earnbench.external_unearned.catalog import load_external_unearned_catalog

EXECUTION_MANIFEST_CSV = "execution_manifest.csv"
PATCH_MANIFEST_JSON = "patch_manifest.json"
EXECUTION_MANIFEST_JSON = "execution_manifest.json"

MANIFEST_REQUIRED_COLUMNS = (
    "external_id",
    "instance_id",
    "patch_ref",
)

MANIFEST_OPTIONAL_COLUMNS = ("y0_policy",)

MANIFEST_COLUMNS = MANIFEST_REQUIRED_COLUMNS + MANIFEST_OPTIONAL_COLUMNS

DEFAULT_Y0_POLICY = "prod_only"
ALLOWED_Y0_POLICIES = frozenset({"prod_only", "raw_full"})


@dataclass(frozen=True, slots=True)
class ExecutionManifestValidationResult:
    path: Path
    row_count: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class ExternalUnearnedExecutionRow:
    external_id: str
    instance_id: str
    patch_ref: str
    y0_policy: str = DEFAULT_Y0_POLICY

    def to_dict(self) -> dict[str, str]:
        return {
            "external_id": self.external_id,
            "instance_id": self.instance_id,
            "patch_ref": self.patch_ref,
            "y0_policy": self.y0_policy,
        }


def validate_execution_manifest(
    path: Path,
    *,
    catalog_path: Path | None = None,
    patches_root: Path | None = None,
    require_patch_files: bool = False,
) -> ExecutionManifestValidationResult:
    """Validate an external unearned execution manifest CSV."""
    resolved = path.resolve()
    if not resolved.is_file():
        return ExecutionManifestValidationResult(
            path=resolved,
            row_count=0,
            errors=(f"execution manifest not found: {resolved}",),
        )

    catalog_by_id: dict[str, dict[str, str]] = {}
    if catalog_path is not None:
        catalog_by_id = {
            str(row["external_id"]).strip(): row
            for row in load_external_unearned_catalog(catalog_path)
        }

    manifest_dir = resolved.parent
    patch_root = (patches_root or manifest_dir).resolve()

    errors: list[str] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return ExecutionManifestValidationResult(
                path=resolved,
                row_count=0,
                errors=(f"{resolved}: empty file or missing header row",),
            )

        header = [name.strip() for name in reader.fieldnames if name is not None]
        missing = [column for column in MANIFEST_REQUIRED_COLUMNS if column not in header]
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

            instance_id = str(raw.get("instance_id", "")).strip()
            if not instance_id:
                errors.append(f"{prefix}: instance_id must be non-empty")

            patch_ref = str(raw.get("patch_ref", "")).strip()
            if not patch_ref:
                errors.append(f"{prefix}: patch_ref must be non-empty")
            elif require_patch_files:
                patch_path = _resolve_patch_ref(patch_ref, manifest_dir, patch_root)
                if not patch_path.is_file():
                    errors.append(f"{prefix}: patch file not found: {patch_path}")

            y0_policy = str(raw.get("y0_policy", DEFAULT_Y0_POLICY)).strip() or DEFAULT_Y0_POLICY
            if y0_policy not in ALLOWED_Y0_POLICIES:
                errors.append(
                    f"{prefix}: y0_policy {y0_policy!r} must be one of "
                    f"{sorted(ALLOWED_Y0_POLICIES)}"
                )

            if catalog_by_id and external_id and external_id not in catalog_by_id:
                errors.append(
                    f"{prefix}: external_id {external_id!r} not found in catalog"
                )

        if row_count == 0 and not errors:
            errors.append(f"{resolved}: execution manifest contains no data rows")

    return ExecutionManifestValidationResult(
        path=resolved,
        row_count=row_count,
        errors=tuple(errors),
    )


def load_execution_manifest(path: Path) -> list[ExternalUnearnedExecutionRow]:
    """Load execution manifest rows after validation."""
    result = validate_execution_manifest(path)
    if not result.ok:
        msg = "; ".join(result.errors)
        raise ValueError(msg)

    rows: list[ExternalUnearnedExecutionRow] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw is None:
                continue
            y0_policy = str(raw.get("y0_policy", DEFAULT_Y0_POLICY)).strip() or DEFAULT_Y0_POLICY
            rows.append(
                ExternalUnearnedExecutionRow(
                    external_id=str(raw["external_id"]).strip(),
                    instance_id=str(raw["instance_id"]).strip(),
                    patch_ref=str(raw["patch_ref"]).strip(),
                    y0_policy=y0_policy,
                )
            )
    return sorted(rows, key=lambda row: row.external_id)


def _resolve_patch_ref(
    patch_ref: str,
    manifest_dir: Path,
    patches_root: Path,
) -> Path:
    candidate = Path(patch_ref)
    if candidate.is_file():
        return candidate.resolve()
    relative_manifest = (manifest_dir / patch_ref).resolve()
    if relative_manifest.is_file():
        return relative_manifest
    relative_root = (patches_root / patch_ref).resolve()
    if relative_root.is_file():
        return relative_root
    return relative_manifest


def resolve_execution_patch_path(
    row: ExternalUnearnedExecutionRow,
    *,
    manifest_dir: Path,
    patches_root: Path,
) -> Path:
    """Resolve a manifest patch reference to an on-disk patch file."""
    return _resolve_patch_ref(row.patch_ref, manifest_dir, patches_root)
