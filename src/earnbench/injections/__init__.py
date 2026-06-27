"""Blinded injection specification loading, validation, and catalog access."""

from earnbench.injections.catalog import (
    InjectionCatalogError,
    discover_injection_files,
    get_injection,
    list_injections,
    load_injection_catalog,
)
from earnbench.injections.loader import (
    InjectionLoadError,
    load_injection_file,
    load_injections,
)
from earnbench.injections.spec import InjectionSpec
from earnbench.injections.validate import (
    resolve_patch_ref,
    validate_directory,
    validate_injection,
    validate_path,
)

__all__ = [
    "InjectionCatalogError",
    "InjectionLoadError",
    "InjectionSpec",
    "discover_injection_files",
    "get_injection",
    "list_injections",
    "load_injection_catalog",
    "load_injection_file",
    "load_injections",
    "resolve_patch_ref",
    "validate_directory",
    "validate_injection",
    "validate_path",
]
