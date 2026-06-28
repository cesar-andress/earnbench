"""Tests for Phase D agent patch re-grade pipeline."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from earnbench.adapters.swebench import prepare_exploit, supported_perturbations
from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.outcomes import OutcomeStatus
from earnbench.phase_d_regrade import (
    AGENT_RESULTS_COLUMNS,
    PhaseDRegradeConfig,
    build_agent_result_row,
    filter_eligible_attempts,
    load_attempts_csv,
    run_phase_d,
    summarize_phase_d,
    task_key,
    write_agent_results_csv,
)
from earnbench.rank_stability import analyze_rank_stability, load_agent_results
from earnbench.scheduler import aggregate_instance

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
INSTANCE_ID = "psf__requests-1724"

SAMPLE_DIFF = (
    "diff --git a/requests/models.py b/requests/models.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/requests/models.py\n"
    "+++ b/requests/models.py\n"
    "@@ -1,3 +1,4 @@\n"
    " # header\n"
    "+fixed prod change\n"
)


def _write_attempts_csv(path: Path, rows: list[dict[str, object]]) -> None:
    header = [
        "agent",
        "model",
        "provider",
        "instance_id",
        "replicate",
        "seed",
        "scaffold_id",
        "prompt_sha256",
        "patch_path",
        "patch_sha256",
        "trajectory_log_ref",
        "status",
        "started_at_utc",
        "completed_at_utc",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _attempt_row(
    *,
    agent: str = "ollama_devstral",
    status: str = "ok",
    patch_path: str = "patches/ollama_devstral/psf__requests-1724/replicate_0.patch",
    replicate: int = 0,
    instance_id: str = INSTANCE_ID,
) -> dict[str, object]:
    return {
        "agent": agent,
        "model": "devstral",
        "provider": "ollama",
        "instance_id": instance_id,
        "replicate": replicate,
        "seed": 1,
        "scaffold_id": "earnbench_phase_c_v1",
        "prompt_sha256": "abc",
        "patch_path": patch_path,
        "patch_sha256": "def",
        "trajectory_log_ref": "",
        "status": status,
        "started_at_utc": "2026-01-01T00:00:00Z",
        "completed_at_utc": "2026-01-01T00:00:01Z",
        "error": "",
    }


def _setup_phase_c_run(tmp_path: Path, *, rows: list[dict[str, object]]) -> Path:
    phase_c = tmp_path / "phase_c"
    phase_c.mkdir()
    patch_path = phase_c / "patches/ollama_devstral/psf__requests-1724/replicate_0.patch"
    patch_path.parent.mkdir(parents=True)
    patch_path.write_text(SAMPLE_DIFF, encoding="utf-8")
    _write_attempts_csv(phase_c / "attempts.csv", rows)
    return phase_c


def _run_config() -> SWEBenchRunConfig:
    return SWEBenchRunConfig(
        workers=1,
        max_parallel_containers=1,
        max_parallel_builds=1,
        reuse_images=True,
        allow_build=False,
        cache_dir=None,
        timeout_seconds=60,
    )


def _write_grade(path: Path, *, success: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "instance_id": INSTANCE_ID,
                "status": OutcomeStatus.OK.value,
                "success": success,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    audit_path = path.parent / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "instance_id": INSTANCE_ID,
                "status": OutcomeStatus.OK.value,
                "success": success,
                "schema_version": "earnbench_audit.v1",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_mock_graded_cell(
    *,
    work_root: Path,
    metadata_path: Path,
    run_id: str,
    attempt_row: dict[str, object],
    y0: bool,
    pi_success: dict[str, bool],
) -> dict[str, object]:
    prepare_exploit(
        metadata_path=metadata_path,
        instance_id=INSTANCE_ID,
        exploit_id="mock_cell",
        patch_content=SAMPLE_DIFF,
        output_dir=work_root,
        run_id=run_id,
        patch_class="agent_patch",
        y0_policy="prod_only",
    )
    record = load_verified_instance(metadata_path, INSTANCE_ID)
    scheduled = supported_perturbations(INSTANCE_ID, record.fail_to_pass)
    instance_dir = work_root / INSTANCE_ID
    _write_grade(instance_dir / "nominal" / "grade.json", success=y0)
    for perturbation_id, success in pi_success.items():
        if perturbation_id in scheduled:
            _write_grade(instance_dir / perturbation_id / "grade.json", success=success)
    csv_row = aggregate_instance(
        metadata_path=metadata_path,
        output_dir=work_root,
        instance_id=INSTANCE_ID,
        scheduled_perturbations=scheduled,
        run_id=run_id,
    )
    report_payload = json.loads((instance_dir / "report.json").read_text(encoding="utf-8"))
    from earnbench.agents.schemas import AttemptRecord

    attempt = AttemptRecord.from_dict(attempt_row)
    return build_agent_result_row(
        attempt=attempt,
        csv_row=csv_row,
        report_payload=report_payload,
    )


def test_filter_eligible_attempts_skips_failed_phase_c_rows(tmp_path: Path) -> None:
    phase_c = _setup_phase_c_run(
        tmp_path,
        rows=[
            _attempt_row(status="ok"),
            _attempt_row(status="error"),
            _attempt_row(status="no_patch", patch_path=""),
        ],
    )
    records = load_attempts_csv(phase_c / "attempts.csv")
    eligible, skipped = filter_eligible_attempts(records)
    assert len(eligible) == 1
    assert eligible[0].status == "ok"
    assert skipped == 2


def test_run_phase_d_writes_agent_results_schema(tmp_path: Path) -> None:
    attempt = _attempt_row(status="ok")
    phase_c = _setup_phase_c_run(tmp_path, rows=[attempt])
    output = tmp_path / "phase_d"

    def _mock_cell(**kwargs):
        from earnbench.phase_d_regrade import PhaseDCellResult, PhaseDTask

        task: PhaseDTask = kwargs["task"]
        work_root = task.work_root
        row = _write_mock_graded_cell(
            work_root=work_root,
            metadata_path=METADATA_FIXTURE,
            run_id="phase_d_test",
            attempt_row=attempt,
            y0=True,
            pi_success={
                "pi_vtest.v1": True,
                "pi_verif.v1": False,
                "pi_env.v1": True,
            },
        )
        return PhaseDCellResult(task_key=task.task_key, row=row, failure=None)

    with patch("earnbench.phase_d_regrade.run_phase_d_cell", side_effect=_mock_cell):
        result = run_phase_d(
            PhaseDRegradeConfig(
                phase_c_run=phase_c,
                metadata_path=METADATA_FIXTURE,
                output_dir=output,
                run_config=_run_config(),
            )
        )

    assert result.agent_results_csv.is_file()
    with result.agent_results_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(AGENT_RESULTS_COLUMNS)
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["agent"] == "ollama_devstral"
    assert rows[0]["y0"] in {"True", "true", "1"}
    assert rows[0]["ef_status"] == "defined"


def test_run_phase_d_resume_skips_completed_cell(tmp_path: Path) -> None:
    attempt = _attempt_row(status="ok")
    phase_c = _setup_phase_c_run(tmp_path, rows=[attempt])
    output = tmp_path / "phase_d"
    calls = {"count": 0}

    def _mock_cell(**kwargs):
        from earnbench.phase_d_regrade import PhaseDCellResult, PhaseDTask

        calls["count"] += 1
        task: PhaseDTask = kwargs["task"]
        row = _write_mock_graded_cell(
            work_root=task.work_root,
            metadata_path=METADATA_FIXTURE,
            run_id="phase_d_test",
            attempt_row=attempt,
            y0=True,
            pi_success={"pi_verif.v1": True, "pi_env.v1": True},
        )
        return PhaseDCellResult(task_key=task.task_key, row=row, failure=None)

    config = PhaseDRegradeConfig(
        phase_c_run=phase_c,
        metadata_path=METADATA_FIXTURE,
        output_dir=output,
        run_config=_run_config(),
        resume=True,
    )
    with patch("earnbench.phase_d_regrade.run_phase_d_cell", side_effect=_mock_cell):
        run_phase_d(config)
        run_phase_d(config)
    assert calls["count"] == 1


def test_run_phase_d_records_missing_patch_failure(tmp_path: Path) -> None:
    phase_c = tmp_path / "phase_c"
    phase_c.mkdir()
    _write_attempts_csv(
        phase_c / "attempts.csv",
        [_attempt_row(status="ok", patch_path="patches/missing.patch")],
    )
    output = tmp_path / "phase_d"
    result = run_phase_d(
        PhaseDRegradeConfig(
            phase_c_run=phase_c,
            metadata_path=METADATA_FIXTURE,
            output_dir=output,
            run_config=_run_config(),
        )
    )
    assert result.graded_count == 0
    assert result.failure_count == 1
    with result.failures_path.open(encoding="utf-8", newline="") as handle:
        failures = list(csv.DictReader(handle))
    assert failures[0]["stage"] == "pipeline"
    assert "patch file not found" in failures[0]["error"]


def test_run_phase_d_calls_nominal_grading_with_expected_patch(tmp_path: Path) -> None:
    phase_c = _setup_phase_c_run(tmp_path, rows=[_attempt_row(status="ok")])
    output = tmp_path / "phase_d"
    seen: dict[str, Path] = {}

    def _mock_nominal_grading(**kwargs):
        seen["patch_path"] = kwargs["patch_path"]
        instance_dir = kwargs["output_dir"] / kwargs["instance_id"]
        _write_grade(instance_dir / "nominal" / "grade.json", success=True)

    def _mock_preflight(**kwargs):
        instance_dir = kwargs["output_dir"] / kwargs["instance_id"]
        instance_dir.mkdir(parents=True, exist_ok=True)
        (instance_dir / "preflight.json").write_text(
            json.dumps({"status": "ok"}) + "\n",
            encoding="utf-8",
        )

    def _mock_pi(**kwargs):
        perturbation_id = kwargs["perturbation_id"]
        instance_dir = kwargs["output_dir"] / kwargs["instance_id"]
        _write_grade(instance_dir / perturbation_id / "grade.json", success=True)

    with (
        patch("earnbench.phase_d_regrade.prepare_exploit") as mock_prepare,
        patch("earnbench.phase_d_regrade.run_preflight_stage", side_effect=_mock_preflight),
        patch(
            "earnbench.phase_b_batch.run_nominal_grading",
            side_effect=_mock_nominal_grading,
        ),
        patch(
            "earnbench.phase_d_regrade.run_exploit_perturbation_stage",
            side_effect=_mock_pi,
        ),
    ):
        mock_prepare.side_effect = lambda **kwargs: prepare_exploit(**kwargs)
        run_phase_d(
            PhaseDRegradeConfig(
                phase_c_run=phase_c,
                metadata_path=METADATA_FIXTURE,
                output_dir=output,
                run_config=_run_config(),
            )
        )

    expected_patch = (
        output
        / "cells/ollama_devstral"
        / INSTANCE_ID
        / "patch"
        / "prod_only.patch"
    )
    assert seen["patch_path"] == expected_patch
    assert "+fixed prod change" in seen["patch_path"].read_text(encoding="utf-8")


def test_build_agent_result_row_computes_ef_from_mock_outcomes(tmp_path: Path) -> None:
    attempt = _attempt_row(status="ok")
    _setup_phase_c_run(tmp_path, rows=[attempt])
    work_root = tmp_path / "cells/ollama_devstral"
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    scheduled = supported_perturbations(INSTANCE_ID, record.fail_to_pass)
    pi_success = {
        pid: (False if pid == "pi_verif.v1" else True) for pid in scheduled
    }
    row = _write_mock_graded_cell(
        work_root=work_root,
        metadata_path=METADATA_FIXTURE,
        run_id="phase_d_test",
        attempt_row=attempt,
        y0=True,
        pi_success=pi_success,
    )
    expected_ef = (len(scheduled) - 1) / len(scheduled)
    assert row["ef_exclude_invalid"] == pytest.approx(expected_ef)
    assert row["ef_invalid_as_fail"] == pytest.approx(expected_ef)
    assert "verif" in str(row["failed_mechanisms"])


def test_agent_results_accepted_by_rank_stability(tmp_path: Path) -> None:
    rows = {
        task_key("alpha", "inst-1", 0): {
            col: ""
            for col in AGENT_RESULTS_COLUMNS
        },
        task_key("beta", "inst-1", 0): {
            col: ""
            for col in AGENT_RESULTS_COLUMNS
        },
    }
    rows[task_key("alpha", "inst-1", 0)].update(
        {
            "agent": "alpha",
            "provider": "external_cli",
            "model": "m1",
            "instance_id": "inst-1",
            "replicate": 0,
            "attempt_status": "ok",
            "patch_path": "p.patch",
            "y0": True,
            "ef_exclude_invalid": 1.0,
            "ef_invalid_as_fail": 1.0,
            "ef_status": "defined",
            "failed_mechanisms": "",
            "invalid_pi_count": 0,
            "run_id": "r1",
            "config_digest": "d1",
        }
    )
    rows[task_key("beta", "inst-1", 0)].update(
        {
            "agent": "beta",
            "provider": "external_cli",
            "model": "m2",
            "instance_id": "inst-1",
            "replicate": 0,
            "attempt_status": "ok",
            "patch_path": "p.patch",
            "y0": True,
            "ef_exclude_invalid": 0.0,
            "ef_invalid_as_fail": 0.0,
            "ef_status": "defined",
            "failed_mechanisms": "visible_test_overfitting",
            "invalid_pi_count": 0,
            "run_id": "r1",
            "config_digest": "d1",
        }
    )
    path = write_agent_results_csv(tmp_path, rows)
    loaded = load_agent_results(path)
    payload = analyze_rank_stability(loaded, bootstrap_draws=50, bootstrap_seed=0)
    assert payload["agent_count"] == 2


def test_summarize_phase_d_reads_completed_run(tmp_path: Path) -> None:
    attempt = _attempt_row(status="ok")
    phase_c = _setup_phase_c_run(tmp_path, rows=[attempt])
    output = tmp_path / "phase_d"

    def _mock_cell(**kwargs):
        from earnbench.phase_d_regrade import PhaseDCellResult, PhaseDTask

        task: PhaseDTask = kwargs["task"]
        row = _write_mock_graded_cell(
            work_root=task.work_root,
            metadata_path=METADATA_FIXTURE,
            run_id="phase_d_test",
            attempt_row=attempt,
            y0=True,
            pi_success={"pi_verif.v1": True, "pi_env.v1": True},
        )
        return PhaseDCellResult(task_key=task.task_key, row=row, failure=None)

    with patch("earnbench.phase_d_regrade.run_phase_d_cell", side_effect=_mock_cell):
        run_phase_d(
            PhaseDRegradeConfig(
                phase_c_run=phase_c,
                metadata_path=METADATA_FIXTURE,
                output_dir=output,
                run_config=_run_config(),
            )
        )

    summary = summarize_phase_d(output_dir=output)
    assert summary.graded_count == 1
    assert "ollama_devstral" in summary.by_agent
