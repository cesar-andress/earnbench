"""Versioned perturbation registry for EarnBench MVP Π."""

from __future__ import annotations

import builtins
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from earnbench.provenance import PERTURBATION_REGISTRY_VERSION
from earnbench.registry.base import PerturbationSpec
from earnbench.registry.pi_env_v1 import PI_ENV_V1
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1

_BUILT_IN_SPECS: tuple[PerturbationSpec, ...] = (
    PI_VTEST_V1,
    PI_VERIF_V1,
    PI_ENV_V1,
)


class RegistryError(Exception):
    """Raised when registry lookup or validation fails."""


@lru_cache(maxsize=1)
def _spec_index() -> dict[str, PerturbationSpec]:
    return {spec.id: spec for spec in _BUILT_IN_SPECS}


def get(perturbation_id: str) -> PerturbationSpec:
    """Return a perturbation spec by id."""
    spec = _spec_index().get(perturbation_id)
    if spec is None:
        known = ", ".join(sorted(_spec_index()))
        msg = f"unknown perturbation id: {perturbation_id!r} (known: {known})"
        raise RegistryError(msg)
    return spec


def list() -> list[PerturbationSpec]:
    """Return all registered perturbation specs in default Π order."""
    manifest = load_manifest()
    order = manifest.get("default_pi_order", [])
    specs_by_id = _spec_index()
    ordered = [specs_by_id[pid] for pid in order if pid in specs_by_id]
    remaining = [
        spec for spec in _BUILT_IN_SPECS if spec.id not in {s.id for s in ordered}
    ]
    return ordered + remaining


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Any]:
    """Load the shipped perturbation registry manifest."""
    raw = files("earnbench.registry").joinpath("manifest.json").read_text(
        encoding="utf-8"
    )
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        msg = "manifest.json must contain a JSON object"
        raise RegistryError(msg)
    return manifest


def validate(*, sample_configs: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Validate registry integrity and optional per-perturbation configs."""
    errors: list[str] = []
    manifest = load_manifest()

    manifest_version = manifest.get("registry_version")
    if manifest_version != PERTURBATION_REGISTRY_VERSION:
        errors.append(
            "manifest registry_version "
            f"{manifest_version!r} != expected {PERTURBATION_REGISTRY_VERSION!r}"
        )

    manifest_entries = manifest.get("perturbations")
    if not isinstance(manifest_entries, builtins.list):
        errors.append("manifest perturbations must be a list")
        return errors

    code_ids = {spec.id for spec in _BUILT_IN_SPECS}
    manifest_ids: set[str] = set()
    for index, entry in enumerate(manifest_entries):
        if not isinstance(entry, dict):
            errors.append(f"manifest perturbations[{index}] must be an object")
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            errors.append(f"manifest perturbations[{index}] missing string id")
            continue
        manifest_ids.add(entry_id)
        spec = _spec_index().get(entry_id)
        if spec is None:
            errors.append(f"manifest lists unknown perturbation id: {entry_id!r}")
            continue
        if entry.get("version") != spec.version:
            errors.append(
                f"{entry_id}: manifest version {entry.get('version')!r} "
                f"!= code version {spec.version!r}"
            )
        manifest_channels = entry.get("supported_channels")
        if [*spec.supported_channels] != manifest_channels:
            errors.append(f"{entry_id}: supported_channels mismatch vs code")

    missing_in_manifest = sorted(code_ids - manifest_ids)
    if missing_in_manifest:
        errors.append(
            "manifest missing code-defined perturbations: "
            + ", ".join(missing_in_manifest)
        )
    extra_in_manifest = sorted(manifest_ids - code_ids)
    if extra_in_manifest:
        errors.append(
            "manifest lists perturbations not registered in code: "
            + ", ".join(extra_in_manifest)
        )

    default_order = manifest.get("default_pi_order")
    if not isinstance(default_order, builtins.list):
        errors.append("manifest default_pi_order must be a list")
    elif default_order != [spec.id for spec in _BUILT_IN_SPECS]:
        errors.append("manifest default_pi_order must match built-in registration")

    if sample_configs:
        for perturbation_id, config in sample_configs.items():
            try:
                spec = get(perturbation_id)
            except RegistryError as exc:
                errors.append(str(exc))
                continue
            if not isinstance(config, dict):
                errors.append(
                    f"{perturbation_id}: sample config must be a JSON object"
                )
                continue
            errors.extend(spec.validate_config(config))

    return errors
