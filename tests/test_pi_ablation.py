"""Tests for Π ablation sensitivity analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.pi_ablation import analyze_pi_ablation, generate_pi_ablation_report
from earnbench.reports import EarnedFractionStatus


def test_analyze_pi_ablation_delta() -> None:
    rows = [
        {
            "y0": "1",
            "retained": "1",
            "y_vtest": "1",
            "y_verif": "0",
            "y_env": "1",
            "pi_vtest_status": "success",
            "pi_verif_status": "success",
            "pi_env_status": "success",
            "ef_pi": "0.666667",
            "ef_status": EarnedFractionStatus.DEFINED.value,
        },
        {
            "y0": "1",
            "retained": "1",
            "y_vtest": "1",
            "y_verif": "1",
            "y_env": "1",
            "pi_vtest_status": "success",
            "pi_verif_status": "success",
            "pi_env_status": "success",
            "ef_pi": "1.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
        },
    ]
    payload = analyze_pi_ablation(rows)
    assert payload["full_ef_mean"] == pytest.approx(0.833333, rel=1e-4)
    ablated = {row["ablated_pi"]: row for row in payload["ablations"]}
    assert ablated["pi_verif.v1"]["delta_from_full_ef_mean"] is not None


def test_generate_pi_ablation_report(tmp_path: Path) -> None:
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
    out = tmp_path / "ablation"
    result = generate_pi_ablation_report(summary, out)
    payload = json.loads(result.report_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "earnbench.pi_ablation.v1"
    assert result.ablation_csv.is_file()
