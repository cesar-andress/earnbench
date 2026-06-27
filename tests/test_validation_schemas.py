"""Tests for validation ladder schema validators."""

from __future__ import annotations

import json
from pathlib import Path

from earnbench.registry_agreement import validate_registry_agreement_table
from earnbench.registry_evolution import validate_registry_evolution_scenario
from earnbench.stress_test_schema import validate_stress_test_catalog


def test_validate_stress_test_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "stress.csv"
    catalog.write_text(
        "stress_id,stress_class,target_stage,target_pi,parameter,parameter_value,"
        "expected_validator_behavior,expected_ef_drift,notes\n"
        "S001,timeout,nominal,,timeout_sec,120,reject_corrupt,within_ci,note\n",
        encoding="utf-8",
    )
    result = validate_stress_test_catalog(catalog)
    assert result.ok


def test_validate_registry_evolution_scenario(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "scenario_id": "rev-001",
                "base_registry_version": "earnbench_perturbation_registry.v1",
                "target_registry_version": "earnbench_perturbation_registry.v2",
                "added_pi": ["pi_git.v1"],
                "removed_pi": [],
                "frozen_instance_set": "phase_a_retained",
                "expected_coverage_delta": 0.05,
                "notes": "hypothetical extension",
            }
        ),
        encoding="utf-8",
    )
    result = validate_registry_evolution_scenario(scenario)
    assert result.ok


def test_validate_registry_agreement_table(tmp_path: Path) -> None:
    table = tmp_path / "agreement.csv"
    table.write_text(
        "instance_id,artifact_id,registry_a_version,registry_b_version,"
        "ef_registry_a,ef_registry_b,ef_delta,rank_a,rank_b,rank_delta,notes\n"
        "inst-1,patch-a,v1,v2,1.0,0.9,0.1,1,2,1,note\n",
        encoding="utf-8",
    )
    result = validate_registry_agreement_table(table)
    assert result.ok
