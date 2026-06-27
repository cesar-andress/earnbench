"""Tests for the blinded injection specification subsystem."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.cli import main
from earnbench.injections import (
    InjectionLoadError,
    InjectionSpec,
    get_injection,
    list_injections,
    load_injection_file,
    load_injections,
    resolve_patch_ref,
    validate_injection,
    validate_path,
)

FIXTURES = Path(__file__).parent / "fixtures" / "injections"
VALID_JSON = FIXTURES / "BI-001.json"
VALID_YAML = FIXTURES / "BI-002.yaml"
INVALID_JSON = FIXTURES / "invalid_channel.json"


def test_injection_spec_from_dict_round_trip() -> None:
    payload = json.loads(VALID_JSON.read_text(encoding="utf-8"))
    spec = InjectionSpec.from_dict(payload)
    assert spec.injection_id == "BI-001"
    assert spec.in_registry is True
    assert spec.expected_failed_pi == "pi_vtest.v1"
    assert spec.to_dict() == payload


def test_load_injection_file_json() -> None:
    specs = load_injection_file(VALID_JSON)
    assert len(specs) == 1
    assert specs[0].injected_channel == "visible_test_overfitting"


def test_load_injection_file_yaml() -> None:
    pytest.importorskip("yaml")
    specs = load_injection_file(VALID_YAML)
    assert len(specs) == 1
    assert specs[0].injection_id == "BI-002"
    assert specs[0].in_registry is False


def test_load_injections_catalog_wrapper() -> None:
    payload = {
        "injections": [
            json.loads(VALID_JSON.read_text(encoding="utf-8")),
            json.loads(INVALID_JSON.read_text(encoding="utf-8")),
        ]
    }
    specs = load_injections(payload)
    assert len(specs) == 2


def test_validate_injection_valid_spec() -> None:
    spec = InjectionSpec.from_dict(json.loads(VALID_JSON.read_text(encoding="utf-8")))
    assert validate_injection(spec) == []


def test_validate_injection_invalid_channel() -> None:
    spec = InjectionSpec.from_dict(json.loads(INVALID_JSON.read_text(encoding="utf-8")))
    errors = validate_injection(spec)
    assert any("injected_channel" in error for error in errors)


def test_validate_path_directory() -> None:
    errors = validate_path(FIXTURES)
    assert any("not_a_real_channel" in error for error in errors)
    assert not any("BI-001" in error and "not found" in error for error in errors)


def test_resolve_patch_ref_supports_patches_subdirectory() -> None:
    resolved = resolve_patch_ref(FIXTURES, "patches/BI-001_clean.patch")
    assert resolved.is_file()


def test_list_and_get_injection_catalog() -> None:
    specs = list_injections(FIXTURES)
    assert [spec.injection_id for spec in specs] == sorted(
        spec.injection_id for spec in specs
    )
    spec = get_injection(FIXTURES, "BI-001")
    assert spec.instance_id == "django__django-13279"


def test_get_unknown_injection_raises() -> None:
    with pytest.raises(Exception, match="unknown injection id"):
        get_injection(FIXTURES, "missing_injection")


def test_load_injection_file_missing_raises() -> None:
    with pytest.raises(InjectionLoadError, match="not found"):
        load_injection_file(Path("/nonexistent/injection.json"))


def test_cli_injection_list(capsys) -> None:
    exit_code = main(["injection", "list", str(FIXTURES)])
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    ids = [item["injection_id"] for item in payload["injections"]]
    assert "BI-001" in ids
    assert "BI-002" in ids


def test_cli_injection_show(capsys) -> None:
    exit_code = main(["injection", "show", "BI-001", str(FIXTURES)])
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["injection_id"] == "BI-001"
    assert payload["expected_failed_pi"] == "pi_vtest.v1"


def test_cli_injection_validate_success(capsys) -> None:
    exit_code = main(["injection", "validate", str(VALID_JSON)])
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"


def test_cli_injection_validate_directory_failure(capsys) -> None:
    exit_code = main(["injection", "validate", str(FIXTURES)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not_a_real_channel" in captured.err
