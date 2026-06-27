"""Tests for Phase A markdown report generation."""

from __future__ import annotations

import json
from pathlib import Path

from earnbench.phase_a_report import (
    PHASE_A_REPORT_MD,
    generate_phase_a_report,
    render_phase_a_report,
)
from earnbench.reports import EarnedFractionStatus

INSTANCE_A = "django__django-13279"
INSTANCE_B = "psf__requests-1724"


def _write_summary(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "instance_id,repo,y0,y_vtest,y_verif,y_env,pi_vtest_status,pi_verif_status,pi_env_status,valid_pi_count,ef_pi,ef_exclude_invalid,ef_invalid_as_fail,invalid_pi_count,invalid_pi_rate,ef_sensitivity_gap,ef_status,false_unearned,retained,exclude_reason,run_id,config_digest",
                f"{INSTANCE_A},django/django,True,True,True,True,ok,ok,ok,3,1.0,1.0,1.0,0,0.0,0.0,{EarnedFractionStatus.DEFINED.value},False,True,,phase_a_test,sha256:cfg",
                f"{INSTANCE_B},psf/requests,True,True,True,False,ok,ok,invalid,2,1.0,1.0,0.6666666666666666,1,0.3333333333333333,0.3333333333333333,{EarnedFractionStatus.DEFINED.value},False,True,,phase_a_test,sha256:cfg",
                "bad__instance,bad/repo,True,False,True,True,ok,ok,ok,3,0.6666666666666666,0.6666666666666666,0.6666666666666666,0,0.0,0.0,defined,True,False,false_unearned,phase_a_test,sha256:cfg",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_failures(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "instance_id,stage,error,timestamp_utc",
                "missing__instance,prepare,metadata row not found,2026-01-01T00:00:00Z",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_statistics(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "instance_count": 3,
                "retained_count": 2,
                "false_unearned_count": 1,
                "ef_defined_count": 3,
                "ef_undefined_count": 0,
                "ef_mean": 0.8888888888888888,
                "ef_min": 0.6666666666666666,
                "ef_max": 1.0,
                "invalid_pi_total": 1,
                "invalid_pi_rate_mean": 0.1111111111111111,
                "ef_sensitivity_gap_mean": 0.1111111111111111,
                "ef_sensitivity_gap_max": 0.3333333333333333,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps({"run_id": "phase_a_test"}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_render_phase_a_report_is_deterministic() -> None:
    summary_rows = [
        {
            "instance_id": INSTANCE_B,
            "repo": "psf/requests",
            "y0": "True",
            "ef_pi": "1.0",
            "ef_exclude_invalid": "1.0",
            "ef_invalid_as_fail": "0.6666666666666666",
            "invalid_pi_count": "1",
            "invalid_pi_rate": "0.3333333333333333",
            "ef_sensitivity_gap": "0.3333333333333333",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "retained": "True",
            "false_unearned": "False",
        },
        {
            "instance_id": INSTANCE_A,
            "repo": "django/django",
            "y0": "True",
            "ef_pi": "1.0",
            "ef_exclude_invalid": "1.0",
            "ef_invalid_as_fail": "1.0",
            "invalid_pi_count": "0",
            "invalid_pi_rate": "0.0",
            "ef_sensitivity_gap": "0.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "retained": "True",
            "false_unearned": "False",
        },
    ]
    kwargs = {
        "summary_rows": summary_rows,
        "failure_rows": [],
        "statistics": {"ef_mean": 1.0, "ef_sensitivity_gap_mean": 0.0},
        "manifest": {"run_id": "phase_a_test"},
    }
    first = render_phase_a_report(**kwargs)
    second = render_phase_a_report(**kwargs)
    assert first == second
    assert "## EF@Π histogram" in first
    assert "## Invalid-π histogram" in first
    assert "## EF sensitivity gap" in first
    assert INSTANCE_A in first
    assert INSTANCE_B in first


def test_generate_phase_a_report_writes_markdown(tmp_path: Path) -> None:
    _write_summary(tmp_path / "summary.csv")
    _write_failures(tmp_path / "failures.csv")
    _write_statistics(tmp_path / "statistics.json")
    _write_manifest(tmp_path / "run_manifest.json")

    result = generate_phase_a_report(tmp_path)
    assert result.report_path == tmp_path / PHASE_A_REPORT_MD
    body = result.report_path.read_text(encoding="utf-8")

    assert "# Phase A Golden Validation Report" in body
    assert "## Retained instances" in body
    assert "## Excluded instances" in body
    assert "bad__instance" in body
    assert "false_unearned" in body
    assert "missing__instance" in body
    assert "0.333333" in body
    assert "Phase A golden validation run `phase_a_test`" in body


def test_generate_phase_a_report_missing_summary_raises(tmp_path: Path) -> None:
    try:
        generate_phase_a_report(tmp_path)
    except FileNotFoundError as exc:
        assert "summary.csv" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_cli_report_phase_a(capsys, tmp_path: Path) -> None:
    from earnbench.cli import main

    _write_summary(tmp_path / "summary.csv")
    _write_statistics(tmp_path / "statistics.json")
    _write_manifest(tmp_path / "run_manifest.json")

    exit_code = main(["report", "phase-a", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["report_path"].endswith(PHASE_A_REPORT_MD)
    assert (tmp_path / PHASE_A_REPORT_MD).is_file()
