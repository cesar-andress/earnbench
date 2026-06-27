"""Load blinded injection specifications from JSON or YAML files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from earnbench.injections.spec import InjectionSpec

SUPPORTED_SUFFIXES = frozenset({".json", ".yaml", ".yml"})


class InjectionLoadError(Exception):
    """Raised when an injection specification file cannot be loaded."""


def load_injections(payload: Any, *, source: str = "<payload>") -> list[InjectionSpec]:
    """Parse one injection spec or a catalog payload into a list of specs."""
    if isinstance(payload, list):
        return [
            _parse_spec_item(item, source=source, index=index)
            for index, item in enumerate(payload)
        ]
    if isinstance(payload, dict):
        if "injections" in payload:
            injections_raw = payload["injections"]
            if not isinstance(injections_raw, list):
                msg = f"{source}: 'injections' must be a list"
                raise InjectionLoadError(msg)
            return [
                _parse_spec_item(item, source=source, index=index)
                for index, item in enumerate(injections_raw)
            ]
        if "injection_id" in payload:
            return [InjectionSpec.from_dict(payload)]
        msg = f"{source}: mapping must contain 'injection_id' or an 'injections' list"
        raise InjectionLoadError(msg)
    msg = f"{source}: payload must be a mapping or list"
    raise InjectionLoadError(msg)


def load_injection_file(path: Path) -> list[InjectionSpec]:
    """Load injection specs from a JSON or YAML file."""
    resolved = path.resolve()
    if not resolved.is_file():
        msg = f"injection spec file not found: {resolved}"
        raise InjectionLoadError(msg)
    suffix = resolved.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        msg = (
            f"unsupported injection spec format {suffix!r}; "
            f"expected one of {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )
        raise InjectionLoadError(msg)
    payload = _load_payload(resolved)
    return load_injections(payload, source=str(resolved))


def _load_payload(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"invalid JSON in {path}: {exc}"
            raise InjectionLoadError(msg) from exc
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            msg = (
                "PyYAML is required to load YAML injection specs; "
                "install with `pip install pyyaml`"
            )
            raise InjectionLoadError(msg) from exc
        try:
            payload = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            msg = f"invalid YAML in {path}: {exc}"
            raise InjectionLoadError(msg) from exc
        if payload is None:
            msg = f"empty YAML document in {path}"
            raise InjectionLoadError(msg)
        return payload
    msg = f"unsupported injection spec format: {suffix}"
    raise InjectionLoadError(msg)


def _parse_spec_item(item: Any, *, source: str, index: int) -> InjectionSpec:
    if not isinstance(item, dict):
        msg = f"{source}: injection entry [{index}] must be a mapping"
        raise InjectionLoadError(msg)
    try:
        return InjectionSpec.from_dict(item)
    except (TypeError, ValueError) as exc:
        msg = f"{source}: injection entry [{index}]: {exc}"
        raise InjectionLoadError(msg) from exc
