"""Tests for Phase A instance investigation."""

from __future__ import annotations

import json
from pathlib import Path

from earnbench.investigate import (
    INVESTIGATION_JSON,
    INVESTIGATION_MD,
    build_phase_a_investigation,
    write_phase_a_investigation,
)
from earnbench.reports import EarnedFractionStatus

INSTANCE_ID = "psf__requests-1921"


def _write_summary(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "instance_id,repo,y0,y_vtest,y_verif,y_env,pi_vtest_status,pi_verif_status,pi_env_status,valid_pi_count,ef_pi,ef_status,false_unearned,retained,exclude_reason,run_id,config_digest",
                f"{INSTANCE_ID},psf/requests,True,False,False,,ok,ok,invalid,2,0.0,{EarnedFractionStatus.DEFINED.value},True,False,false_unearned,phase_a_test,sha256:cfg",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_grade(path: Path, *, success: bool, status: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "instance_id": INSTANCE_ID,
                "status": status,
                "success": success,
                "outcome": "fail" if not success else "success",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_harness_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report_line = (
        "report: {'psf__requests-1921': {'resolved': False, 'tests_status': "
        "{'FAIL_TO_PASS': {'success': ['test_ok'], 'failure': []}, "
        "'PASS_TO_PASS': {'success': ['test_ok'], 'failure': "
        "['test_requests.py::RequestsTestCase::test_cookie_quote_wrapped']}}}}"
    )
    path.write_text(report_line + "\n", encoding="utf-8")


def test_build_phase_a_investigation_detects_p2p_regression(tmp_path: Path) -> None:
    run_dir = tmp_path / "phase_a"
    instance_dir = run_dir / INSTANCE_ID
    instance_dir.mkdir(parents=True)
    _write_summary(run_dir / "summary.csv")
    _write_grade(instance_dir / "nominal" / "grade.json", success=True)
    _write_grade(instance_dir / "pi_vtest.v1" / "grade.json", success=False)
    _write_harness_log(instance_dir / "pi_vtest.v1" / "harness.log")
    (instance_dir / "report.json").write_text(
        json.dumps({"status": "defined", "earned_fraction": 0.0}, sort_keys=True),
        encoding="utf-8",
    )

    payload = build_phase_a_investigation(
        phase_a_run=run_dir,
        instance_id=INSTANCE_ID,
    )

    vtest = next(item for item in payload["stages"] if item["stage"] == "pi_vtest.v1")
    assert vtest["failure_mode"] == "pass_to_pass_regression"
    assert payload["confound_register_suggestion"]["retained"] is False
    assert payload["summary"]["exclude_reason"] == "false_unearned"


def test_write_phase_a_investigation_writes_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "phase_a"
    instance_dir = run_dir / INSTANCE_ID
    instance_dir.mkdir(parents=True)
    _write_summary(run_dir / "summary.csv")
    _write_grade(instance_dir / "nominal" / "grade.json", success=True)

    result = write_phase_a_investigation(
        phase_a_run=run_dir,
        instance_id=INSTANCE_ID,
    )

    assert result.investigation_json.name == INVESTIGATION_JSON
    assert result.investigation_md.name == INVESTIGATION_MD
    body = result.investigation_md.read_text(encoding="utf-8")
    assert f"# Phase A investigation — `{INSTANCE_ID}`" in body


def test_cli_investigate(capsys, tmp_path: Path) -> None:
    from earnbench.cli import main

    run_dir = tmp_path / "phase_a"
    instance_dir = run_dir / INSTANCE_ID
    instance_dir.mkdir(parents=True)
    _write_summary(run_dir / "summary.csv")
    _write_grade(instance_dir / "nominal" / "grade.json", success=True)

    exit_code = main(
        [
            "investigate",
            "--phase-a-run",
            str(run_dir),
            "--instance-id",
            INSTANCE_ID,
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["instance_id"] == INSTANCE_ID
    assert (instance_dir / INVESTIGATION_MD).is_file()
