"""Phase C′0 variance-pilot manifest validation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = (
    "agent",
    "model",
    "provider",
    "instance_id",
    "replicate_count",
    "temperature",
    "seed_policy",
    "difficulty_bin",
    "patch_loc",
    "files_touched",
    "notes",
)


@dataclass(frozen=True, slots=True)
class ManifestValidationResult:
    path: Path
    row_count: int
    agent_count: int
    instance_count: int
    total_attempts: int
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _parse_positive_int(value: object, *, prefix: str, field: str) -> tuple[int | None, str | None]:
    text = str(value if value is not None else "").strip()
    if not text:
        return None, f"{prefix}: {field} must be a positive integer"
    if not text.isdigit():
        return None, f"{prefix}: {field} must be a positive integer, got {value!r}"
    parsed = int(text)
    if parsed < 1:
        return None, f"{prefix}: {field} must be >= 1, got {parsed}"
    return parsed, None


def _parse_temperature(value: object, *, prefix: str) -> str | None:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        float(text)
    except ValueError:
        return f"{prefix}: temperature must be a float or empty, got {value!r}"
    return None


def validate_phase_c_prime_manifest(path: Path) -> ManifestValidationResult:
    """Validate a Phase C′ pilot manifest CSV (schema only; no agent execution)."""
    resolved = path.resolve()
    if not resolved.is_file():
        return ManifestValidationResult(
            path=resolved,
            row_count=0,
            agent_count=0,
            instance_count=0,
            total_attempts=0,
            errors=(f"manifest file not found: {resolved}",),
        )

    errors: list[str] = []
    row_count = 0
    agents: set[str] = set()
    instances: set[str] = set()
    total_attempts = 0
    seen_agent_instance: set[tuple[str, str]] = set()

    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return ManifestValidationResult(
                path=resolved,
                row_count=0,
                agent_count=0,
                instance_count=0,
                total_attempts=0,
                errors=(f"{resolved}: empty file or missing header row",),
            )

        header = [name.strip() for name in reader.fieldnames if name is not None]
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            errors.append(f"{resolved}: missing required columns: {', '.join(missing)}")

        for line_number, raw in enumerate(reader, start=2):
            if raw is None:
                continue
            row_count += 1
            prefix = f"{resolved}:{line_number}"

            agent = str(raw.get("agent", "")).strip()
            instance_id = str(raw.get("instance_id", "")).strip()
            if not agent:
                errors.append(f"{prefix}: agent must be non-empty")
            if not instance_id:
                errors.append(f"{prefix}: instance_id must be non-empty")

            key = (agent, instance_id)
            if agent and instance_id:
                if key in seen_agent_instance:
                    errors.append(
                        f"{prefix}: duplicate agent-instance row for "
                        f"agent={agent!r} instance_id={instance_id!r}"
                    )
                else:
                    seen_agent_instance.add(key)

            replicate_count, replicate_error = _parse_positive_int(
                raw.get("replicate_count"),
                prefix=prefix,
                field="replicate_count",
            )
            if replicate_error:
                errors.append(replicate_error)
            elif replicate_count is not None:
                total_attempts += replicate_count

            temperature_error = _parse_temperature(raw.get("temperature"), prefix=prefix)
            if temperature_error:
                errors.append(temperature_error)

            seed_policy = str(raw.get("seed_policy", "")).strip()
            if not seed_policy:
                errors.append(f"{prefix}: seed_policy must be non-empty")

            if agent:
                agents.add(agent)
            if instance_id:
                instances.add(instance_id)

    return ManifestValidationResult(
        path=resolved,
        row_count=row_count,
        agent_count=len(agents),
        instance_count=len(instances),
        total_attempts=total_attempts,
        errors=tuple(errors),
    )


def load_phase_c_prime_manifest(path: Path) -> list[dict[str, str]]:
    """Load manifest rows after schema validation."""
    result = validate_phase_c_prime_manifest(path)
    if not result.ok:
        msg = "; ".join(result.errors)
        raise ValueError(msg)
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]
