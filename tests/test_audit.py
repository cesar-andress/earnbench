import json

import pytest

from earnbench import __version__
from earnbench.audit import AUDIT_SCHEMA_VERSION, AuditRecord, AuditStatus


def _sample_record(**overrides: object) -> AuditRecord:
    defaults = {
        "instance_id": "django__django-13279",
        "perturbation_id": "pi_vtest.v1",
        "config_digest": "sha256:abc",
        "patch_sha256": "deadbeef" * 8,
        "status": AuditStatus.OK,
        "success": True,
        "tests_run": ("tests.test_foo.TestCase.test_bar",),
        "warnings": (),
        "timestamp_utc": "2026-06-27T12:00:00Z",
        "earnbench_version": __version__,
    }
    defaults.update(overrides)
    return AuditRecord(**defaults)  # type: ignore[arg-type]


def test_audit_record_construction() -> None:
    record = _sample_record(
        pristine_test_sha256="cafebabe" * 8,
        image_digest="sha256:img",
        log_ref="pi_vtest.v1/harness.log",
    )
    assert record.schema_version == AUDIT_SCHEMA_VERSION
    assert record.earnbench_version == __version__


def test_audit_record_rejects_empty_instance_id() -> None:
    with pytest.raises(ValueError, match="instance_id"):
        _sample_record(instance_id="")


def test_audit_record_rejects_empty_patch_sha256() -> None:
    with pytest.raises(ValueError, match="patch_sha256"):
        _sample_record(patch_sha256="")


def test_audit_record_requires_success_when_ok() -> None:
    with pytest.raises(ValueError, match="success must be set"):
        _sample_record(status=AuditStatus.OK, success=None)


def test_audit_record_forbids_success_when_invalid() -> None:
    with pytest.raises(ValueError, match="success must be None"):
        _sample_record(status=AuditStatus.INVALID, success=False)


def test_audit_record_to_dict_is_json_serializable() -> None:
    record = _sample_record()
    payload = record.to_dict()
    json.dumps(payload)
    assert payload["status"] == "ok"
    assert payload["tests_run"] == ["tests.test_foo.TestCase.test_bar"]
    assert payload["warnings"] == []


def test_audit_record_to_json_is_deterministic_except_timestamp() -> None:
    record_a = _sample_record(timestamp_utc="2026-06-27T12:00:00Z")
    record_b = _sample_record(timestamp_utc="2026-06-27T13:00:00Z")
    assert record_a.to_json() != record_b.to_json()
    assert record_a.to_json() == record_a.to_json()
    parsed = json.loads(record_a.to_json())
    assert parsed["timestamp_utc"] == "2026-06-27T12:00:00Z"


def test_audit_record_omits_empty_optional_fields() -> None:
    record = _sample_record()
    payload = record.to_dict()
    assert "pristine_test_sha256" not in payload
    assert "image_digest" not in payload
    assert "log_ref" not in payload


def test_audit_record_from_dict_round_trip() -> None:
    record = _sample_record(
        pristine_test_sha256="abc",
        image_digest="sha256:docker",
        log_ref="logs/harness.log",
        warnings=("holdout thin",),
    )
    restored = AuditRecord.from_dict(record.to_dict())
    assert restored == record


def test_audit_record_invalid_status_without_success() -> None:
    record = AuditRecord(
        instance_id="django__django-13279",
        perturbation_id="pi_verif.v1",
        config_digest="sha256:cfg",
        patch_sha256="abc123",
        status=AuditStatus.INVALID,
        warnings=("patch apply failed",),
    )
    payload = record.to_dict()
    assert "success" not in payload
    assert payload["warnings"] == ["patch apply failed"]
