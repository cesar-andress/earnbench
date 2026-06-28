"""Tests for registry geometry post-hoc analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.registry_geometry import (
    analyze_registry_geometry,
    generate_registry_geometry_report,
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


def test_all_survive_profile() -> None:
    payload = analyze_registry_geometry([_base_row(instance_id="a"), _base_row(instance_id="b")])
    profiles = {row["profile_label"]: row for row in payload["profiles"]}
    assert profiles["survive_all"]["count"] == 2
    assert profiles["survive_all"]["fraction"] == pytest.approx(1.0)
    assert profiles["survive_all"]["mean_ef"] == pytest.approx(1.0)


def test_single_channel_failures() -> None:
    rows = [
        _base_row(
            instance_id="v",
            y_vtest="0",
            ef_pi="0.666667",
        ),
        _base_row(
            instance_id="r",
            y_verif="0",
            ef_pi="0.666667",
        ),
        _base_row(
            instance_id="e",
            y_env="0",
            ef_pi="0.666667",
        ),
    ]
    payload = analyze_registry_geometry(rows)
    profiles = {row["profile_label"]: row for row in payload["profiles"]}
    assert profiles["fail_vtest"]["count"] == 1
    assert profiles["fail_verif"]["count"] == 1
    assert profiles["fail_env"]["count"] == 1


def test_multi_channel_failures() -> None:
    rows = [
        _base_row(
            instance_id="all",
            y_vtest="0",
            y_verif="0",
            y_env="0",
            ef_pi="0.0",
        ),
        _base_row(
            instance_id="ve",
            y_vtest="0",
            y_verif="0",
            y_env="1",
            ef_pi="0.333333",
        ),
    ]
    payload = analyze_registry_geometry(rows)
    profiles = {row["profile_label"]: row for row in payload["profiles"]}
    assert profiles["fail_all"]["count"] == 1
    assert profiles["fail_vtest_verif"]["count"] == 1


def test_invalid_rows_excluded_from_primary_but_counted() -> None:
    rows = [
        _base_row(instance_id="ok"),
        _base_row(
            instance_id="bad",
            pi_env_status="invalid",
            y_env="",
            invalid_pi_count="1",
        ),
        _base_row(instance_id="fail-y0", y0="0"),
    ]
    payload = analyze_registry_geometry(rows)
    assert payload["primary_row_count"] == 1
    assert payload["y0_row_count"] == 2
    assert payload["excluded_from_primary"]["counts_by_reason"]["invalid_status"] == 1
    assert payload["excluded_from_primary"]["partial_measurement_y0_rows"] == 0


def test_same_ef_different_profiles() -> None:
    rows = [
        _base_row(instance_id="p101", y_verif="0", ef_pi="0.666667"),
        _base_row(instance_id="p011", y_vtest="0", ef_pi="0.666667"),
    ]
    payload = analyze_registry_geometry(rows)
    examples = payload["same_ef_different_profile_examples"]
    assert len(examples) == 1
    assert examples[0]["ef_pi"] == pytest.approx(0.666667)
    assert set(examples[0]["profiles"]) == {"fail_verif", "fail_vtest"}


def test_redundant_channels_toy_example() -> None:
    rows = [
        _base_row(instance_id=f"r{i}", y_verif="0", y_env="0", ef_pi="0.333333")
        for i in range(4)
    ] + [
        _base_row(instance_id="unique", y_vtest="0", ef_pi="0.666667"),
    ]
    payload = analyze_registry_geometry(rows)
    correlations = {
        (row["channel_a"], row["channel_b"]): row
        for row in payload["channel_correlations"]
    }
    pair = correlations[("verif", "env")]
    assert pair["count_both_fail"] == 4
    assert pair["jaccard"] == pytest.approx(1.0)
    assert pair["high_co_failure"] is True


def test_unique_channel_contribution_toy_example() -> None:
    rows = [
        _base_row(instance_id="only-v", y_vtest="0", ef_pi="0.666667"),
        _base_row(instance_id="both", y_vtest="0", y_verif="0", ef_pi="0.333333"),
    ]
    payload = analyze_registry_geometry(rows)
    marginal = {row["channel"]: row for row in payload["marginal_contribution"]}
    assert marginal["vtest"]["only_failed_channel_count"] == 1
    assert marginal["vtest"]["unique_detection_count"] == 1
    assert marginal["vtest"]["ef_change_count"] == 2
    assert marginal["verif"]["only_failed_channel_count"] == 0


def test_profiles_by_agent_when_column_present() -> None:
    rows = [
        _base_row(instance_id="a1", agent="agent-a"),
        _base_row(instance_id="a2", agent="agent-a", y_vtest="0", ef_pi="0.666667"),
        _base_row(instance_id="b1", agent="agent-b", y_verif="0", ef_pi="0.666667"),
    ]
    payload = analyze_registry_geometry(rows)
    assert payload["by_agent"] is not None
    assert payload["by_agent"]["agent-a"][0]["count"] == 1
    assert payload["by_agent"]["agent-a"][1]["count"] == 1


def test_generate_registry_geometry_report(tmp_path: Path) -> None:
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
    out = tmp_path / "geometry"
    result = generate_registry_geometry_report(summary, out)
    payload = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "earnbench.registry_geometry.v1"
    assert result.profiles_csv.is_file()
    assert result.cofailure_matrix_csv.is_file()
    assert result.channel_correlations_csv.is_file()
    assert result.marginal_contribution_csv.is_file()
    assert result.report_md.is_file()
    assert "Registry geometry report" in result.report_md.read_text(encoding="utf-8")
