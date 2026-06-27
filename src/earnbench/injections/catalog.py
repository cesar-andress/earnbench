"""Discover and index blinded injection specifications on disk."""

from __future__ import annotations

from pathlib import Path

from earnbench.injections.loader import (
    SUPPORTED_SUFFIXES,
    InjectionLoadError,
    load_injection_file,
)
from earnbench.injections.spec import InjectionSpec


class InjectionCatalogError(Exception):
    """Raised when injection catalog lookup or loading fails."""


def discover_injection_files(directory: Path) -> list[Path]:
    """Return sorted injection spec files under ``directory`` (non-recursive)."""
    if not directory.is_dir():
        msg = f"injection directory not found: {directory}"
        raise InjectionCatalogError(msg)
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def load_injection_catalog(directory: Path) -> dict[str, InjectionSpec]:
    """Load all injection specs in ``directory`` keyed by ``injection_id``."""
    catalog: dict[str, InjectionSpec] = {}
    for path in discover_injection_files(directory):
        try:
            specs = load_injection_file(path)
        except InjectionLoadError as exc:
            raise InjectionCatalogError(str(exc)) from exc
        for spec in specs:
            if spec.injection_id in catalog:
                msg = (
                    f"duplicate injection_id {spec.injection_id!r} "
                    f"in {path} and existing catalog entries"
                )
                raise InjectionCatalogError(msg)
            catalog[spec.injection_id] = spec
    return catalog


def list_injections(directory: Path) -> list[InjectionSpec]:
    """Return injection specs sorted by ``injection_id``."""
    catalog = load_injection_catalog(directory)
    return [catalog[key] for key in sorted(catalog)]


def get_injection(directory: Path, injection_id: str) -> InjectionSpec:
    """Return one injection spec by id from ``directory``."""
    catalog = load_injection_catalog(directory)
    spec = catalog.get(injection_id)
    if spec is None:
        known = ", ".join(sorted(catalog))
        msg = f"unknown injection id: {injection_id!r} (known: {known})"
        raise InjectionCatalogError(msg)
    return spec
