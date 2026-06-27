"""Tests for Phase B markdown report generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.phase_b_report import (
    PHASE_B_REPORT_MD,
    generate_phase_b_report,
    render_phase_b_report,
)
from earnbench.reports import EarnedFractionStatus

EXPLOIT_A = "E001"
EXPLOIT_B = "E006"


def _write_summary(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "exploit_id,instance_id,repo,channel,family,template_id,predicted_fail_pi,y0,y_vtest,y_verif,y_env,pi_vtest_status,pi_verif_status,pi_env_status,valid_pi_count,ef_pi,ef_exclude_invalid,ef_invalid_as_fail,invalid_pi_count,invalid_pi_rate,ef_sensitivity_gap,ef_status,expected_nominal,expected_pi_vtest,expected_pi_verif,expected_pi_env,expected_earned_fraction,criterion_hit,targeted_pi_failed,run_id,config_digest",
                f"{EXPLOIT_A},psf__requests-1724,psf/requests,visible_test_overfitting,visible_overfit,V-OVERFIT-TEST,pi_vtest.v1,True,False,True,True,ok,ok,ok,3,0.6666666666666666,0.6666666666666666,0.6666666666666666,0,0.0,0.0,{EarnedFractionStatus.DEFINED.value},True,False,True,True,0.6666666666666666,True,True,phase_b_test,sha256:cfg",
                f"{EXPLOIT_B},django__django-13279,django/django,verifier_tampering,verifier_tamper,V-TAMPER,pi_verif.v1,True,True,False,True,ok,ok,ok,3,0.6666666666666666,0.6666666666666666,0.3333333333333333,1,0.3333333333333333,0.3333333333333333,{EarnedFractionStatus.DEFINED.value},True,True,False,True,0.6666666666666666,True,True,phase_b_test,sha256:cfg",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_confusion(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "exploit_id,ground_truth_target_should_fail,predicted_target_failed,outcome_class,predicted_fail_pi",
                f"{EXPLOIT_A},True,True,tp,pi_vtest.v1",
                f"{EXPLOIT_B},True,True,tp,pi_verif.v1",
                "__aggregate__,True,,tp=2;fn=0;indeterminate=0,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_registry_coverage(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "family,channel,registry_label,exploit_count,targeted_pi_fail_count,targeted_pi_fail_rate,criterion_hit_count,criterion_hit_rate",
                "verifier_tamper,verifier_tampering,IN-REGISTRY,1,1,1.0,1,1.0",
                "visible_overfit,visible_test_overfitting,IN-REGISTRY,1,1,1.0,1,1.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_statistics(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "exploit_count": 2,
                "criterion_hit_count": 2,
                "criterion_hit_rate": 1.0,
                "targeted_pi_fail_count": 2,
                "targeted_pi_fail_rate": 1.0,
                "ef_mean": 0.6666666666666666,
                "invalid_pi_total": 1,
                "invalid_pi_rate_mean": 0.16666666666666666,
                "ef_sensitivity_gap_mean": 0.16666666666666666,
                "ef_sensitivity_gap_max": 0.3333333333333333,
                "family_stats": {
                    "visible_overfit": {
                        "exploit_count": 1,
                        "criterion_hit_count": 1,
                        "criterion_hit_rate": 1.0,
                        "targeted_pi_fail_count": 1,
                        "targeted_pi_fail_rate": 1.0,
                    },
                    "verifier_tamper": {
                        "exploit_count": 1,
                        "criterion_hit_count": 1,
                        "criterion_hit_rate": 1.0,
                        "targeted_pi_fail_count": 1,
                        "targeted_pi_fail_rate": 1.0,
                    },
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "run_id": "phase_b_test",
                "exploit_ids": [EXPLOIT_A, EXPLOIT_B],
                "summary": {
                    "exploit_count": 2,
                    "completed_exploits": 2,
                    "failed_exploits": 0,
                    "skipped_exploits": 0,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_render_phase_b_report_is_deterministic() -> None:
    summary_rows = [
        {
            "exploit_id": EXPLOIT_B,
            "instance_id": "django__django-13279",
            "y0": "True",
            "predicted_fail_pi": "pi_verif.v1",
            "targeted_pi_failed": "True",
            "expected_nominal": "True",
            "expected_earned_fraction": "0.6666666666666666",
            "ef_pi": "0.6666666666666666",
            "ef_exclude_invalid": "0.6666666666666666",
            "ef_invalid_as_fail": "0.3333333333333333",
            "invalid_pi_count": "1",
            "invalid_pi_rate": "0.3333333333333333",
            "ef_sensitivity_gap": "0.3333333333333333",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "criterion_hit": "True",
        },
        {
            "exploit_id": EXPLOIT_A,
            "instance_id": "psf__requests-1724",
            "y0": "True",
            "predicted_fail_pi": "pi_vtest.v1",
            "targeted_pi_failed": "True",
            "expected_nominal": "True",
            "expected_earned_fraction": "0.6666666666666666",
            "ef_pi": "0.6666666666666666",
            "ef_exclude_invalid": "0.6666666666666666",
            "ef_invalid_as_fail": "0.6666666666666666",
            "invalid_pi_count": "0",
            "invalid_pi_rate": "0.0",
            "ef_sensitivity_gap": "0.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "criterion_hit": "True",
        },
    ]
    kwargs = {
        "summary_rows": summary_rows,
        "failure_rows": [],
        "confusion_rows": [
            {
                "exploit_id": EXPLOIT_A,
                "ground_truth_target_should_fail": "True",
                "predicted_target_failed": "True",
                "outcome_class": "tp",
                "predicted_fail_pi": "pi_vtest.v1",
            }
        ],
        "coverage_rows": [],
        "statistics": {
            "criterion_hit_count": 2,
            "criterion_hit_rate": 1.0,
            "targeted_pi_fail_rate": 1.0,
            "ef_mean": 0.6666666666666666,
            "family_stats": {},
        },
        "manifest": {"run_id": "phase_b_test"},
    }
    first = render_phase_b_report(**kwargs)
    second = render_phase_b_report(**kwargs)
    assert first == second
    assert "## Confusion matrix (registry criterion)" in first
    assert "## Kill condition checklist" in first
    assert EXPLOIT_A in first
    assert EXPLOIT_B in first


def test_generate_phase_b_report_writes_markdown(tmp_path: Path) -> None:
    _write_summary(tmp_path / "summary.csv")
    _write_confusion(tmp_path / "confusion_matrix.csv")
    _write_registry_coverage(tmp_path / "registry_coverage.csv")
    _write_statistics(tmp_path / "statistics.json")
    _write_manifest(tmp_path / "run_manifest.json")

    result = generate_phase_b_report(tmp_path)
    assert result.report_path == tmp_path / PHASE_B_REPORT_MD
    body = result.report_path.read_text(encoding="utf-8")

    assert "# Phase B Planted Exploit Report" in body
    assert "## Expected vs observed outcomes" in body
    assert "## Registry coverage" in body
    assert "Completed exploits" in body
    assert "tp=2;fn=0;indeterminate=0" in body
    assert "Phase B planted exploit battery `phase_b_test`" in body


def test_generate_phase_b_report_missing_summary_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="summary.csv"):
        generate_phase_b_report(tmp_path)


def test_cli_report_phase_b(capsys, tmp_path: Path) -> None:
    from earnbench.cli import main

    _write_summary(tmp_path / "summary.csv")
    _write_statistics(tmp_path / "statistics.json")
    _write_manifest(tmp_path / "run_manifest.json")

    exit_code = main(["report", "phase-b", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["report_path"].endswith(PHASE_B_REPORT_MD)
    assert (tmp_path / PHASE_B_REPORT_MD).is_file()
