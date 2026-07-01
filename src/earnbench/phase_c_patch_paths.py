"""Resolve and link Phase C patch artifacts for Phase D input."""

from __future__ import annotations

import csv
from pathlib import Path


class PhaseCPatchError(Exception):
    """Invalid or missing Phase C patch path."""


def resolve_phase_c_patch(
    phase_c_run: Path,
    patch_path: str,
    *,
    agent_patches_csv: Path | None = None,
) -> Path:
    """Resolve a patch file referenced by ``attempts.csv`` patch_path column.

    Resolution order:
    1. Absolute path if it exists as a file.
    2. ``phase_c_run / patch_path`` (follows symlinks).
    3. ``source_patch_resolved`` from ``agent_patches.csv`` when present.
    """
    phase_c_run = phase_c_run.resolve()
    rel = str(patch_path or "").strip()
    if not rel:
        msg = "empty patch_path"
        raise PhaseCPatchError(msg)

    candidate = Path(rel)
    if candidate.is_file():
        return candidate.resolve()

    relative = phase_c_run / rel
    if relative.is_file():
        return relative.resolve()

    if relative.is_symlink():
        try:
            resolved = relative.resolve()
            if resolved.is_file():
                return resolved
        except OSError:
            pass

    if agent_patches_csv is None:
        agent_patches_csv = phase_c_run / "agent_patches.csv"
    if agent_patches_csv.is_file():
        with agent_patches_csv.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("patch_path", "")).strip() != rel:
                    continue
                source = str(row.get("source_patch_resolved", "")).strip()
                if source:
                    source_path = Path(source)
                    if source_path.is_file():
                        return source_path.resolve()

    msg = f"patch not found: {rel} (under {phase_c_run})"
    raise PhaseCPatchError(msg)


def symlink_patch_into_run(
    output_run: Path,
    patch_path: str,
    target: Path,
    *,
    absolute: bool = True,
) -> Path:
    """Create ``output_run / patch_path`` symlink to ``target``."""
    output_run = output_run.resolve()
    target = target.resolve()
    if not target.is_file():
        msg = f"refusing to link missing target: {target}"
        raise PhaseCPatchError(msg)

    link_path = output_run / patch_path
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()

    if absolute:
        link_path.symlink_to(target)
    else:
        depth = len(Path(patch_path).parts)
        rel_target = Path(*([".."] * depth)) / target.parent.name / patch_path
        link_path.symlink_to(rel_target)
    return link_path


def patch_readable(phase_c_run: Path, patch_path: str) -> bool:
    try:
        resolve_phase_c_patch(phase_c_run, patch_path)
    except PhaseCPatchError:
        return False
    return True


__all__ = [
    "PhaseCPatchError",
    "patch_readable",
    "resolve_phase_c_patch",
    "symlink_patch_into_run",
]
