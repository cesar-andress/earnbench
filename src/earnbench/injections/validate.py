"""Validate blinded injection specifications and on-disk catalogs."""

from __future__ import annotations

from pathlib import Path

from earnbench.injections.catalog import InjectionCatalogError, load_injection_catalog
from earnbench.injections.loader import InjectionLoadError, load_injection_file
from earnbench.injections.spec import InjectionSpec


def validate_injection(spec: InjectionSpec) -> list[str]:
    """Validate one injection spec and return human-readable errors."""
    return spec.validate()


def validate_path(path: Path) -> list[str]:
    """Validate an injection spec file or a directory of specs."""
    resolved = path.resolve()
    if resolved.is_dir():
        return validate_directory(resolved)
    if resolved.is_file():
        return validate_file(resolved)
    return [f"path not found: {resolved}"]


def validate_file(path: Path) -> list[str]:
    """Validate one injection specification file."""
    errors: list[str] = []
    try:
        specs = load_injection_file(path)
    except InjectionLoadError as exc:
        return [str(exc)]

    seen_ids: set[str] = set()
    for spec in specs:
        if spec.injection_id in seen_ids:
            errors.append(
                f"{path}: duplicate injection_id {spec.injection_id!r} in file"
            )
        seen_ids.add(spec.injection_id)
        for error in validate_injection(spec):
            errors.append(f"{path}: {error}")
    return errors


def validate_directory(directory: Path) -> list[str]:
    """Validate all injection specification files in ``directory``."""
    errors: list[str] = []
    try:
        catalog = load_injection_catalog(directory)
    except InjectionCatalogError as exc:
        return [str(exc)]

    for spec in catalog.values():
        for error in validate_injection(spec):
            errors.append(f"{directory}: {error}")
        errors.extend(_validate_patch_refs(directory, spec))
    return errors


def resolve_patch_ref(directory: Path, patch_ref: str) -> Path:
    """Resolve a patch reference relative to an injection catalog directory."""
    candidate = Path(patch_ref)
    if candidate.is_absolute():
        return candidate
    direct = (directory / candidate).resolve()
    if direct.is_file():
        return direct
    nested = (directory / "patches" / candidate.name).resolve()
    if nested.is_file():
        return nested
    return direct


def _validate_patch_refs(directory: Path, spec: InjectionSpec) -> list[str]:
    errors: list[str] = []
    prefix = spec.injection_id
    for field_name, patch_ref in (
        ("paired_clean_patch_ref", spec.paired_clean_patch_ref),
        ("injected_patch_ref", spec.injected_patch_ref),
    ):
        resolved = resolve_patch_ref(directory, patch_ref)
        if not resolved.is_file():
            errors.append(
                f"{directory}: {prefix}: {field_name} not found: {patch_ref!r}"
            )
    return errors
