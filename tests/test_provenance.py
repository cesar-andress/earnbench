"""Tests for execution provenance."""

from __future__ import annotations

import json

import pytest

from earnbench import __version__, compute_earned_fraction
from earnbench.audit import AuditRecord, AuditStatus
from earnbench.outcomes import NominalOutcome, PerturbationResult
from earnbench.provenance import (
    PERTURBATION_REGISTRY_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    Provenance,
    build_provenance,
    resolve_git_commit,
)


def _fixed_provenance(**overrides: object) -> Provenance:
    defaults = {
        "config_digest": "sha256:cfg",
        "docker_image_digest": "sha256:docker",
        "random_seed": 42,
        "include_hostname": False,
        "execution_uuid": "00000000-0000-4000-8000-000000000001",
        "timestamp_utc": "2026-06-27T12:00:00Z",
        "git_commit": "deadbeef" * 5,
        "earnbench_version": __version__,
        "python_version": "3.12.0",
        "platform_string": "Linux-test",
    }
    defaults.update(overrides)
    return build_provenance(**defaults)  # type: ignore[arg-type]


def test_build_provenance_includes_required_fields() -> None:
    provenance = _fixed_provenance()
    payload = provenance.to_dict()

    assert payload["schema_version"] == PROVENANCE_SCHEMA_VERSION
    assert payload["earnbench_version"] == __version__
    assert payload["git_commit"] == "deadbeef" * 5
    assert payload["python_version"] == "3.12.0"
    assert payload["platform"] == "Linux-test"
    assert payload["perturbation_registry_version"] == PERTURBATION_REGISTRY_VERSION
    assert payload["config_digest"] == "sha256:cfg"
    assert payload["timestamp_utc"] == "2026-06-27T12:00:00Z"
    assert payload["random_seed"] == 42
    assert payload["execution_uuid"] == "00000000-0000-4000-8000-000000000001"
    assert payload["docker_image_digest"] == "sha256:docker"
    assert "hostname" not in payload


def test_provenance_from_dict_round_trip() -> None:
    original = _fixed_provenance(hostname="test-host")
    restored = Provenance.from_dict(original.to_dict())
    assert restored == original


def test_provenance_json_serializable() -> None:
    provenance = _fixed_provenance()
    json.dumps(provenance.to_dict())


def test_resolve_git_commit_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EARNBENCH_GIT_COMMIT", "abc123")
    assert resolve_git_commit() == "abc123"


def test_earned_fraction_report_includes_provenance() -> None:
    provenance = _fixed_provenance()
    report = compute_earned_fraction(
        NominalOutcome(run_id="run-1", task_id="task-1", success=True),
        [PerturbationResult.ok("pi_vtest.v1", success=True, channel="vtest")],
        provenance=provenance,
    )
    payload = report.to_dict()

    assert payload["provenance"] == provenance.to_dict()


def test_audit_record_includes_provenance_block() -> None:
    provenance = _fixed_provenance()
    record = AuditRecord(
        instance_id="django__django-13279",
        perturbation_id="pi_vtest.v1",
        config_digest="sha256:abc",
        patch_sha256="deadbeef" * 8,
        status=AuditStatus.OK,
        success=True,
        provenance=provenance,
    )
    payload = record.to_dict()

    assert payload["provenance"] == provenance.to_dict()
    assert payload["earnbench_version"] == __version__


def test_audit_record_derives_provenance_when_missing() -> None:
    record = AuditRecord(
        instance_id="django__django-13279",
        perturbation_id="pi_vtest.v1",
        config_digest="sha256:abc",
        patch_sha256="deadbeef" * 8,
        status=AuditStatus.OK,
        success=True,
        image_digest="sha256:img",
        timestamp_utc="2026-06-27T12:00:00Z",
    )
    payload = record.to_dict()

    assert payload["provenance"]["config_digest"] == "sha256:abc"
    assert payload["provenance"]["docker_image_digest"] == "sha256:img"
    assert payload["provenance"]["timestamp_utc"] == "2026-06-27T12:00:00Z"


def test_audit_record_from_dict_with_provenance() -> None:
    provenance = _fixed_provenance()
    record = AuditRecord(
        instance_id="django__django-13279",
        perturbation_id="pi_vtest.v1",
        config_digest="sha256:abc",
        patch_sha256="deadbeef" * 8,
        status=AuditStatus.OK,
        success=True,
        provenance=provenance,
    )
    restored = AuditRecord.from_dict(record.to_dict())
    assert restored.provenance == provenance
