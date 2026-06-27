"""Tests for the perturbation registry."""

from __future__ import annotations

import json

import pytest

from earnbench.provenance import PERTURBATION_REGISTRY_VERSION
from earnbench.registry import RegistryError, get, list, load_manifest, validate
from earnbench.registry.pi_env_v1 import PI_ENV_V1
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1


def test_load_manifest_contains_mvp_registry_version() -> None:
    manifest = load_manifest()
    assert manifest["registry_version"] == PERTURBATION_REGISTRY_VERSION
    assert len(manifest["perturbations"]) == 3


def test_list_returns_default_pi_order() -> None:
    specs = list()
    assert [spec.id for spec in specs] == [
        "pi_vtest.v1",
        "pi_verif.v1",
        "pi_env.v1",
    ]


def test_get_returns_known_spec() -> None:
    spec = get("pi_vtest.v1")
    assert spec is PI_VTEST_V1
    assert spec.supported_channels == (
        "visible_test_overfitting",
        "hidden_oracle_inference",
    )


def test_get_unknown_raises() -> None:
    with pytest.raises(RegistryError, match="unknown perturbation"):
        get("pi_unknown.v1")


def test_validate_registry_passes() -> None:
    assert validate() == []


def test_to_dict_includes_metadata_fields() -> None:
    payload = PI_VERIF_V1.to_dict()
    assert payload["id"] == "pi_verif.v1"
    assert payload["config_schema"]["required"] == [
        "protected_paths",
        "pristine_test_sha256",
    ]
    assert "audit.success" in payload["expected_outputs"]


def test_pi_vtest_validator_accepts_minimal_config() -> None:
    errors = PI_VTEST_V1.validate_config(
        {"holdout_salt": "earnbench_v0.1_holdout_salt", "holdout_k": 2}
    )
    assert errors == []


def test_pi_vtest_validator_rejects_missing_fields() -> None:
    errors = PI_VTEST_V1.validate_config({})
    assert any("holdout_salt" in error for error in errors)


def test_pi_verif_validator_rejects_empty_paths() -> None:
    errors = PI_VERIF_V1.validate_config(
        {"protected_paths": [], "pristine_test_sha256": "abc"}
    )
    assert any("protected_paths must be non-empty" in error for error in errors)


def test_pi_env_validator_accepts_minimal_config() -> None:
    errors = PI_ENV_V1.validate_config(
        {"image_digest": "sha256:img", "network_disabled": True}
    )
    assert errors == []


def test_executor_stub_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="pi_env.v1"):
        PI_ENV_V1.executor_stub(
            instance_id="django__django-13279",
            patch="diff --git a/foo b/foo",
            config={"image_digest": "sha256:img", "network_disabled": True},
        )


def test_validate_with_sample_configs() -> None:
    errors = validate(
        sample_configs={
            "pi_vtest.v1": {
                "holdout_salt": "earnbench_v0.1_holdout_salt",
                "holdout_k": 2,
            },
            "pi_verif.v1": {
                "protected_paths": ["**/tests/**"],
                "pristine_test_sha256": "deadbeef",
            },
            "pi_env.v1": {
                "image_digest": "sha256:docker",
                "network_disabled": True,
            },
        }
    )
    assert errors == []


def test_manifest_json_is_valid() -> None:
    manifest = load_manifest()
    json.dumps(manifest)
