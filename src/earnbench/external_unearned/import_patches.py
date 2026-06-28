"""Import external unearned patch artifacts into a runnable execution bundle."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.external_unearned.catalog import load_external_unearned_catalog
from earnbench.external_unearned.manifest import (
    EXECUTION_MANIFEST_CSV,
    EXECUTION_MANIFEST_JSON,
    PATCH_MANIFEST_JSON,
    ExternalUnearnedExecutionRow,
    load_execution_manifest,
    resolve_execution_patch_path,
    validate_execution_manifest,
)
from earnbench.provenance import utc_timestamp

PATCH_MANIFEST_SCHEMA = "earnbench.external_unearned_patch_manifest.v1"
EXECUTION_MANIFEST_SCHEMA = "earnbench.external_unearned_execution.v1"


@dataclass(frozen=True, slots=True)
class ExternalUnearnedImportResult:
    output_dir: Path
    execution_manifest_csv: Path
    execution_manifest_json: Path
    patch_manifest_json: Path
    imported_count: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def import_external_unearned_patches(
    *,
    manifest_path: Path,
    output_dir: Path,
    catalog_path: Path | None = None,
    patches_root: Path | None = None,
) -> ExternalUnearnedImportResult:
    """Copy external patches and write deterministic execution/patch manifests."""
    validation = validate_execution_manifest(
        manifest_path,
        catalog_path=catalog_path,
        patches_root=patches_root,
        require_patch_files=True,
    )
    if not validation.ok:
        msg = "; ".join(validation.errors)
        raise ValueError(msg)

    manifest_dir = manifest_path.resolve().parent
    patch_root = (patches_root or manifest_dir).resolve()
    rows = load_execution_manifest(manifest_path)

    catalog_by_id: dict[str, dict[str, str]] = {}
    if catalog_path is not None:
        catalog_by_id = {
            str(row["external_id"]).strip(): row
            for row in load_external_unearned_catalog(catalog_path)
        }

    output_dir = output_dir.resolve()
    patches_dir = output_dir / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)

    imported_rows: list[dict[str, str]] = []
    patch_entries: list[dict[str, str]] = []

    for row in rows:
        source_patch = resolve_execution_patch_path(
            row,
            manifest_dir=manifest_dir,
            patches_root=patch_root,
        )
        target_name = f"{row.external_id}.patch"
        target_patch = patches_dir / target_name
        shutil.copyfile(source_patch, target_patch)

        imported_rows.append(
            {
                "external_id": row.external_id,
                "instance_id": row.instance_id,
                "patch_ref": f"patches/{target_name}",
                "y0_policy": row.y0_policy,
            }
        )
        patch_entries.append(
            {
                "external_id": row.external_id,
                "path": f"patches/{target_name}",
                "sha256": _sha256_file(target_patch),
                "source_patch": str(source_patch),
            }
        )
        if catalog_by_id:
            catalog_row = catalog_by_id[row.external_id]
            imported_rows[-1]["inclusion_decision"] = str(
                catalog_row.get("inclusion_decision", "")
            ).strip()

    execution_manifest_csv = output_dir / EXECUTION_MANIFEST_CSV
    with execution_manifest_csv.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["external_id", "instance_id", "patch_ref", "y0_policy"]
        if catalog_by_id:
            fieldnames.append("inclusion_decision")
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for imported in sorted(imported_rows, key=lambda item: item["external_id"]):
            writer.writerow(imported)

    execution_manifest_json = output_dir / EXECUTION_MANIFEST_JSON
    _write_json(
        execution_manifest_json,
        {
            "schema_version": EXECUTION_MANIFEST_SCHEMA,
            "source_manifest": str(manifest_path.resolve()),
            "catalog_path": str(catalog_path.resolve()) if catalog_path else "",
            "rows": sorted(imported_rows, key=lambda item: item["external_id"]),
            "imported_at_utc": utc_timestamp(),
        },
    )

    patch_manifest_json = output_dir / PATCH_MANIFEST_JSON
    _write_json(
        patch_manifest_json,
        {
            "schema_version": PATCH_MANIFEST_SCHEMA,
            "patches": sorted(patch_entries, key=lambda item: item["external_id"]),
            "imported_at_utc": utc_timestamp(),
        },
    )

    return ExternalUnearnedImportResult(
        output_dir=output_dir,
        execution_manifest_csv=execution_manifest_csv,
        execution_manifest_json=execution_manifest_json,
        patch_manifest_json=patch_manifest_json,
        imported_count=len(imported_rows),
    )
