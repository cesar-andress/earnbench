"""Audit record schema for benchmark-integrated EarnBench grading."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

AUDIT_SCHEMA_VERSION = "earnbench_audit.v1"


class AuditStatus(str, Enum):
    """Lifecycle status recorded in an audit log entry."""

    OK = "ok"
    INVALID = "invalid"
    ERROR = "error"


def _default_earnbench_version() -> str:
    try:
        from importlib.metadata import version

        return version("earnbench")
    except Exception:
        return "0.1.0"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Structured audit log for one nominal or perturbation grading run.

    Serialized form aligns with the perturbation audit schema in the EarnBench
    design notes (``inputs.patch_sha256``, ``outputs.tests_run``, etc.) using a
    flat, JSON-friendly layout.
    """

    instance_id: str
    perturbation_id: str
    config_digest: str
    patch_sha256: str
    status: AuditStatus
    tests_run: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    timestamp_utc: str = ""
    earnbench_version: str = field(default_factory=_default_earnbench_version)
    pristine_test_sha256: str | None = None
    image_digest: str | None = None
    success: bool | None = None
    log_ref: str | None = None
    schema_version: str = AUDIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AUDIT_SCHEMA_VERSION:
            msg = f"unsupported audit schema_version: {self.schema_version}"
            raise ValueError(msg)
        if not self.instance_id:
            msg = "instance_id must be non-empty"
            raise ValueError(msg)
        if not self.config_digest:
            msg = "config_digest must be non-empty"
            raise ValueError(msg)
        if not self.patch_sha256:
            msg = "patch_sha256 must be non-empty"
            raise ValueError(msg)
        if not isinstance(self.status, AuditStatus):
            msg = "status must be an AuditStatus"
            raise TypeError(msg)
        if self.status is AuditStatus.OK and self.success is None:
            msg = "success must be set when status is OK"
            raise ValueError(msg)
        if self.status is not AuditStatus.OK and self.success is not None:
            msg = "success must be None when status is not OK"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict with stable field order."""
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "earnbench_version": self.earnbench_version,
            "instance_id": self.instance_id,
            "perturbation_id": self.perturbation_id,
            "config_digest": self.config_digest,
            "patch_sha256": self.patch_sha256,
            "status": self.status.value,
            "tests_run": list(self.tests_run),
            "warnings": list(self.warnings),
        }
        if self.pristine_test_sha256 is not None:
            payload["pristine_test_sha256"] = self.pristine_test_sha256
        if self.image_digest is not None:
            payload["image_digest"] = self.image_digest
        if self.success is not None:
            payload["success"] = self.success
        if self.log_ref is not None:
            payload["log_ref"] = self.log_ref
        if self.timestamp_utc:
            payload["timestamp_utc"] = self.timestamp_utc
        return payload

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize to canonical JSON (sorted keys; timestamp is caller-supplied)."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditRecord:
        """Parse an ``audit.json`` payload."""
        status_raw = data["status"]
        status = (
            status_raw
            if isinstance(status_raw, AuditStatus)
            else AuditStatus(status_raw)
        )
        success = data.get("success")
        default_version = _default_earnbench_version()
        return cls(
            schema_version=data.get("schema_version", AUDIT_SCHEMA_VERSION),
            earnbench_version=data.get("earnbench_version", default_version),
            instance_id=data["instance_id"],
            perturbation_id=data.get("perturbation_id", ""),
            config_digest=data["config_digest"],
            patch_sha256=data["patch_sha256"],
            pristine_test_sha256=data.get("pristine_test_sha256"),
            image_digest=data.get("image_digest"),
            status=status,
            success=success,
            tests_run=tuple(data.get("tests_run", ())),
            log_ref=data.get("log_ref"),
            warnings=tuple(data.get("warnings", ())),
            timestamp_utc=data.get("timestamp_utc", ""),
        )
