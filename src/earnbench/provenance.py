"""Execution provenance for EarnBench reports and audit records."""

from __future__ import annotations

import os
import platform
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROVENANCE_SCHEMA_VERSION = "earnbench_provenance.v1"
PERTURBATION_REGISTRY_VERSION = "earnbench_perturbation_registry.v1"


def _default_earnbench_version() -> str:
    try:
        from importlib.metadata import version

        return version("earnbench")
    except Exception:
        return "0.1.0"


def resolve_git_commit() -> str:
    """Return the installed package git commit hash when available."""
    env_commit = os.environ.get("EARNBENCH_GIT_COMMIT", "").strip()
    if env_commit:
        return env_commit

    repo_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        commit = result.stdout.strip()
        return commit or "unknown"
    except Exception:
        return "unknown"


def utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    stamp = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
    return stamp.replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Provenance:
    """Structured provenance attached to EarnBench measurement outputs."""

    earnbench_version: str
    git_commit: str
    python_version: str
    platform: str
    perturbation_registry_version: str
    config_digest: str
    timestamp_utc: str
    random_seed: int | None
    execution_uuid: str
    docker_image_digest: str | None = None
    hostname: str | None = None
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVENANCE_SCHEMA_VERSION:
            msg = f"unsupported provenance schema_version: {self.schema_version}"
            raise ValueError(msg)
        if not self.execution_uuid:
            msg = "execution_uuid must be non-empty"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Serialize provenance to a JSON-friendly mapping."""
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "earnbench_version": self.earnbench_version,
            "git_commit": self.git_commit,
            "python_version": self.python_version,
            "platform": self.platform,
            "perturbation_registry_version": self.perturbation_registry_version,
            "config_digest": self.config_digest,
            "timestamp_utc": self.timestamp_utc,
            "random_seed": self.random_seed,
            "execution_uuid": self.execution_uuid,
        }
        if self.docker_image_digest is not None:
            payload["docker_image_digest"] = self.docker_image_digest
        if self.hostname is not None:
            payload["hostname"] = self.hostname
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        """Parse a provenance object from JSON."""
        random_seed = data.get("random_seed")
        parsed_seed: int | None
        if random_seed is None:
            parsed_seed = None
        else:
            parsed_seed = int(random_seed)

        return cls(
            schema_version=data.get("schema_version", PROVENANCE_SCHEMA_VERSION),
            earnbench_version=str(
                data.get("earnbench_version", _default_earnbench_version())
            ),
            git_commit=str(data.get("git_commit", "unknown")),
            python_version=str(data.get("python_version", platform.python_version())),
            platform=str(data.get("platform", platform.platform())),
            perturbation_registry_version=str(
                data.get(
                    "perturbation_registry_version",
                    PERTURBATION_REGISTRY_VERSION,
                )
            ),
            config_digest=str(data.get("config_digest", "")),
            timestamp_utc=str(data.get("timestamp_utc", "")),
            random_seed=parsed_seed,
            execution_uuid=str(data["execution_uuid"]),
            docker_image_digest=data.get("docker_image_digest"),
            hostname=data.get("hostname"),
        )


def build_provenance(
    *,
    config_digest: str = "",
    docker_image_digest: str | None = None,
    random_seed: int | None = None,
    include_hostname: bool = True,
    execution_uuid: str | None = None,
    timestamp_utc: str | None = None,
    git_commit: str | None = None,
    earnbench_version: str | None = None,
    python_version: str | None = None,
    platform_string: str | None = None,
    perturbation_registry_version: str | None = None,
    hostname: str | None = None,
) -> Provenance:
    """Build provenance for the current execution environment."""
    resolved_hostname: str | None
    if hostname is not None:
        resolved_hostname = hostname
    elif include_hostname:
        resolved_hostname = platform.node() or None
    else:
        resolved_hostname = None

    return Provenance(
        earnbench_version=earnbench_version or _default_earnbench_version(),
        git_commit=git_commit if git_commit is not None else resolve_git_commit(),
        python_version=python_version or platform.python_version(),
        platform=platform_string or platform.platform(),
        perturbation_registry_version=(
            perturbation_registry_version or PERTURBATION_REGISTRY_VERSION
        ),
        config_digest=config_digest,
        timestamp_utc=timestamp_utc or utc_timestamp(),
        random_seed=random_seed,
        execution_uuid=execution_uuid or str(uuid.uuid4()),
        docker_image_digest=docker_image_digest,
        hostname=resolved_hostname,
    )
