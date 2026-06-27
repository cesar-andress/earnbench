"""Tests for Validation 11 registry structure analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.registry_structure_validation import (
    analyze_registry_structure,
    generate_registry_structure_report,
)
from earnbench.reports import EarnedFractionStatus


def _base_row(**overrides: object) -> dict[str, str]:
    row = {
        "instance_id": "inst-1",
        "y0": "1",
        "y_vtest": "1",
        "y_verif": "1",
        "y_env": "1",
        "pi_vtest_status": "success",
        "pi_verif_status": "success",
        "pi_env_status": "success",
        "ef_pi": "1.0",
        "ef_status": EarnedFractionStatus.DEFINED.value,
        "invalid_pi_count": "0",
        "retained": "1",
        "false_unearned": "0",
    }
    row.update({key: str(value) for key, value in overrides.items()})
    return row


def test_all_channels_survive() -> None:
    payload = analyze_registry_structure(
        [_base_row(instance_id="a"), _base_row(instance_id="b")]
    )
    assert payload["primary_row_count"] == 2
    unique = {row["channel"]: row for row in payload["unique_detection"]}
    assert unique["vtest"]["fail_count"] == 0


def test_single_channel_failure() -> None:
    payload = analyze_registry_structure(
        [_base_row(instance_id="v", y_vtest="0", ef_pi="0.666667")]
    )
    unique = {row["channel"]: row for row in payload["unique_detection"]}
    assert unique["vtest"]["unique_fail_count"] == 1
    assert unique["verif"]["unique_fail_count"] == 0


def test_multi_channel_failure() -> None:
    payload = analyze_registry_structure(
        [
            _base_row(
                instance_id="all",
                y_vtest="0",
                y_verif="0",
                y_env="0",
                ef_pi="0.0",
            )
        ]
    )
    unique = {row["channel"]: row for row in payload["unique_detection"]}
    assert unique["vtest"]["shared_fail_count"] == 1


def test_invalid_rows_excluded_and_counted() -> None:
    payload = analyze_registry_structure(
        [
            _base_row(instance_id="ok"),
            _base_row(
                instance_id="bad",
                pi_env_status="invalid",
                y_env="",
                invalid_pi_count="1",
            ),
        ]
    )
    assert payload["primary_row_count"] == 1
    assert payload["excluded_from_primary"]["invalid_pi_status"] == 1
    invalid = {row["channel"]: row for row in payload["invalid_distribution"]}
    assert invalid["env"]["invalid_count"] == 1


def test_same_ef_different_profiles() -> None:
    payload = analyze_registry_structure(
        [
            _base_row(instance_id="p101", y_verif="0", ef_pi="0.666667"),
            _base_row(instance_id="p011", y_vtest="0", ef_pi="0.666667"),
        ]
    )
    assert len(payload["same_ef_different_profile_examples"]) == 1
    assert payload["same_ef_different_profile_examples"][0]["ef_pi"] == pytest.approx(
        0.666667
    )


def test_redundant_channels_toy_example() -> None:
    rows = [
        _base_row(
            instance_id=f"r{i}",
            y_verif="0",
            y_env="0",
            ef_pi="0.333333",
        )
        for i in range(4)
    ]
    payload = analyze_registry_structure(rows)
    overlap = {
        (row["channel_a"], row["channel_b"]): row for row in payload["overlap"]
    }
    pair = overlap[("verif", "env")]
    assert pair["jaccard_overlap"] == pytest.approx(1.0)
    assert pair["high_co_failure"] is True


def test_unique_channel_toy_example() -> None:
    payload = analyze_registry_structure(
        [
            _base_row(instance_id="only-v", y_vtest="0", ef_pi="0.666667"),
            _base_row(instance_id="both", y_vtest="0", y_verif="0", ef_pi="0.333333"),
        ]
    )
    info = {row["channel"]: row for row in payload["information_content"]}
    unique = {row["channel"]: row for row in payload["unique_detection"]}
    assert unique["vtest"]["unique_fail_count"] == 1
    assert info["vtest"]["ef_change_count"] == 2


def test_eigenvalue_dimensionality_toy_example() -> None:
    rows = [
        _base_row(instance_id=f"v{i}", y_vtest="0", ef_pi="0.666667")
        for i in range(4)
    ] + [
        _base_row(instance_id="r1", y_verif="0", ef_pi="0.666667"),
    ]
    payload = analyze_registry_structure(rows)
    dim = payload["dimensionality"]
    assert len(dim["eigenvalues"]) == 3
    assert dim["dominant_eigenvalue_fraction"] is not None
    assert dim["dominant_eigenvalue_fraction"] > 0.5
    assert "component" in dim["interpretation"].lower()


def test_generate_registry_structure_report(tmp_path: Path) -> None:
    summary = tmp_path / "summary.csv"
    summary.write_text(
        "instance_id,repo,y0,y_vtest,y_verif,y_env,pi_vtest_status,pi_verif_status,"
        "pi_env_status,valid_pi_count,ef_pi,ef_exclude_invalid,ef_invalid_as_fail,"
        "invalid_pi_count,invalid_pi_rate,ef_sensitivity_gap,ef_status,false_unearned,"
        "retained,exclude_reason,run_id,config_digest\n"
        "inst-1,repo/a,1,1,0,1,success,success,success,3,0.666667,0.666667,0.666667,"
        "0,0.0,0.0,defined,0,1,,run-1,digest\n",
        encoding="utf-8",
    )
    out = tmp_path / "structure"
    result = generate_registry_structure_report(summary, out)
    payload = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "earnbench.registry_structure_validation.v1"
    assert payload["validation_layer"] == 11
    assert result.cofailure_matrix_csv.is_file()
    assert result.overlap_csv.is_file()
    assert result.unique_detection_csv.is_file()
    assert result.information_content_csv.is_file()
    assert result.same_ef_profiles_csv.is_file()
    assert result.dimensionality_json.is_file()
    assert result.invalid_distribution_csv.is_file()
    assert "Registry Structure Validation" in result.report_md.read_text(encoding="utf-8")
