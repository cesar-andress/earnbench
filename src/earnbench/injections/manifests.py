"""Prepare and verify blind injection manifests and lockfiles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.injections.catalog import load_injection_catalog
from earnbench.injections.spec import InjectionSpec
from earnbench.provenance import utc_timestamp

INJECTOR_MANIFEST = "injector_manifest.json"
EVALUATOR_MANIFEST = "evaluator_manifest.json"
BLIND_LOCKFILE = "blind_lockfile.json"

INJECTOR_SCHEMA_VERSION = "earnbench_injector_manifest.v1"
EVALUATOR_SCHEMA_VERSION = "earnbench_evaluator_manifest.v1"
LOCKFILE_SCHEMA_VERSION = "earnbench_blind_lockfile.v1"

CLEAN_ARM_SUFFIX = "__clean"


class BlindInjectionError(Exception):
    """Raised when blind injection manifest or lockfile validation fails."""


@dataclass(frozen=True, slots=True)
class PreparedInjectionManifests:
    output_dir: Path
    injector_manifest: Path
    evaluator_manifest: Path
    blind_lockfile: Path
    pair_count: int
    artifact_count: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _pair_payload(spec: InjectionSpec) -> dict[str, Any]:
    return spec.to_dict()


def _evaluator_artifact(
    spec: InjectionSpec,
    *,
    arm: str,
    patch_ref: str,
) -> dict[str, Any]:
    suffix = CLEAN_ARM_SUFFIX if arm == "clean" else ""
    artifact_id = f"{spec.injection_id}{suffix}"
    return {
        "artifact_id": artifact_id,
        "injection_id": spec.injection_id,
        "instance_id": spec.instance_id,
        "patch_ref": patch_ref,
        "arm": arm,
        "blinding_group": spec.blinding_group,
    }


def build_injector_manifest(
    specs: dict[str, InjectionSpec],
    *,
    spec_dir: Path,
    prepared_at_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": INJECTOR_SCHEMA_VERSION,
        "prepared_at_utc": prepared_at_utc or utc_timestamp(),
        "spec_dir": str(spec_dir.resolve()),
        "pairs": [_pair_payload(spec) for spec in specs.values()],
    }


def build_evaluator_manifest(
    specs: dict[str, InjectionSpec],
    *,
    prepared_at_utc: str | None = None,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for spec in specs.values():
        artifacts.append(
            _evaluator_artifact(
                spec,
                arm="clean",
                patch_ref=spec.paired_clean_patch_ref,
            )
        )
        artifacts.append(
            _evaluator_artifact(
                spec,
                arm="injected",
                patch_ref=spec.injected_patch_ref,
            )
        )
    return {
        "schema_version": EVALUATOR_SCHEMA_VERSION,
        "prepared_at_utc": prepared_at_utc or utc_timestamp(),
        "artifacts": artifacts,
    }


def build_blind_lockfile(
    *,
    spec_dir: Path,
    injector_manifest_path: Path,
    evaluator_manifest_path: Path,
    pair_count: int,
    artifact_count: int,
    frozen_at_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": LOCKFILE_SCHEMA_VERSION,
        "frozen_at_utc": frozen_at_utc or utc_timestamp(),
        "spec_dir": str(spec_dir.resolve()),
        "injector_manifest_path": injector_manifest_path.name,
        "evaluator_manifest_path": evaluator_manifest_path.name,
        "injector_manifest_sha256": sha256_file(injector_manifest_path),
        "evaluator_manifest_sha256": sha256_file(evaluator_manifest_path),
        "pair_count": pair_count,
        "artifact_count": artifact_count,
    }


def prepare_injection_manifests(
    spec_dir: Path,
    output_dir: Path,
) -> PreparedInjectionManifests:
    """Load specs and write injector/evaluator manifests plus lockfile."""
    resolved_spec_dir = spec_dir.resolve()
    resolved_output = output_dir.resolve()
    specs = load_injection_catalog(resolved_spec_dir)
    if not specs:
        msg = f"no injection specs found under {resolved_spec_dir}"
        raise BlindInjectionError(msg)

    prepared_at = utc_timestamp()
    injector_payload = build_injector_manifest(
        specs,
        spec_dir=resolved_spec_dir,
        prepared_at_utc=prepared_at,
    )
    evaluator_payload = build_evaluator_manifest(
        specs,
        prepared_at_utc=prepared_at,
    )

    injector_path = resolved_output / INJECTOR_MANIFEST
    evaluator_path = resolved_output / EVALUATOR_MANIFEST
    _write_json(injector_path, injector_payload)
    _write_json(evaluator_path, evaluator_payload)

    lockfile_payload = build_blind_lockfile(
        spec_dir=resolved_spec_dir,
        injector_manifest_path=injector_path,
        evaluator_manifest_path=evaluator_path,
        pair_count=len(specs),
        artifact_count=len(specs) * 2,
        frozen_at_utc=prepared_at,
    )
    lockfile_path = resolved_output / BLIND_LOCKFILE
    _write_json(lockfile_path, lockfile_payload)

    return PreparedInjectionManifests(
        output_dir=resolved_output,
        injector_manifest=injector_path,
        evaluator_manifest=evaluator_path,
        blind_lockfile=lockfile_path,
        pair_count=len(specs),
        artifact_count=len(specs) * 2,
    )


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"{path} must contain a JSON object"
        raise BlindInjectionError(msg)
    return payload


def load_injector_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload.get("schema_version") != INJECTOR_SCHEMA_VERSION:
        msg = f"{path}: unsupported schema_version {payload.get('schema_version')!r}"
        raise BlindInjectionError(msg)
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        msg = f"{path}: pairs must be a non-empty list"
        raise BlindInjectionError(msg)
    return payload


def load_evaluator_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload.get("schema_version") != EVALUATOR_SCHEMA_VERSION:
        msg = f"{path}: unsupported schema_version {payload.get('schema_version')!r}"
        raise BlindInjectionError(msg)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        msg = f"{path}: artifacts must be a non-empty list"
        raise BlindInjectionError(msg)
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if "injected_channel" in artifact or "expected_failed_pi" in artifact:
            msg = (
                f"{path}: evaluator manifest must not expose injected_channel "
                "or expected_failed_pi"
            )
            raise BlindInjectionError(msg)
    return payload


def load_blind_lockfile(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload.get("schema_version") != LOCKFILE_SCHEMA_VERSION:
        msg = f"{path}: unsupported schema_version {payload.get('schema_version')!r}"
        raise BlindInjectionError(msg)
    return payload


def verify_lockfile_integrity(
    lockfile_path: Path,
    *,
    injector_manifest_path: Path | None = None,
    evaluator_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Verify manifest files match lockfile SHA256 digests."""
    lockfile = load_blind_lockfile(lockfile_path)
    lock_dir = lockfile_path.parent

    injector_path = injector_manifest_path or (
        lock_dir / str(lockfile.get("injector_manifest_path", INJECTOR_MANIFEST))
    )
    evaluator_path = evaluator_manifest_path or (
        lock_dir / str(lockfile.get("evaluator_manifest_path", EVALUATOR_MANIFEST))
    )

    if not injector_path.is_file():
        msg = f"injector manifest not found: {injector_path}"
        raise BlindInjectionError(msg)
    if not evaluator_path.is_file():
        msg = f"evaluator manifest not found: {evaluator_path}"
        raise BlindInjectionError(msg)

    injector_hash = sha256_file(injector_path)
    evaluator_hash = sha256_file(evaluator_path)
    expected_injector = str(lockfile.get("injector_manifest_sha256", ""))
    expected_evaluator = str(lockfile.get("evaluator_manifest_sha256", ""))

    if injector_hash != expected_injector:
        msg = (
            "injector manifest SHA256 mismatch with lockfile "
            f"(expected {expected_injector}, got {injector_hash})"
        )
        raise BlindInjectionError(msg)
    if evaluator_hash != expected_evaluator:
        msg = (
            "evaluator manifest SHA256 mismatch with lockfile "
            f"(expected {expected_evaluator}, got {evaluator_hash})"
        )
        raise BlindInjectionError(msg)

    return lockfile


def injector_specs_from_manifest(
    injector_manifest: dict[str, Any],
) -> dict[str, InjectionSpec]:
    specs: dict[str, InjectionSpec] = {}
    pairs = injector_manifest.get("pairs", [])
    for raw in pairs:
        if not isinstance(raw, dict):
            msg = "injector manifest pair entries must be objects"
            raise BlindInjectionError(msg)
        spec = InjectionSpec.from_dict(raw)
        errors = spec.validate()
        if errors:
            joined = "; ".join(errors)
            msg = f"injector manifest invalid spec: {joined}"
            raise BlindInjectionError(msg)
        if spec.injection_id in specs:
            msg = f"duplicate injection_id in injector manifest: {spec.injection_id!r}"
            raise BlindInjectionError(msg)
        specs[spec.injection_id] = spec
    return specs


def results_injection_id(artifact: dict[str, Any]) -> str:
    arm = str(artifact.get("arm", "")).strip().lower()
    injection_id = str(artifact.get("injection_id", "")).strip()
    if arm == "clean":
        return f"{injection_id}{CLEAN_ARM_SUFFIX}"
    if arm == "injected":
        return injection_id
    msg = f"unknown artifact arm {arm!r} for injection_id {injection_id!r}"
    raise BlindInjectionError(msg)
