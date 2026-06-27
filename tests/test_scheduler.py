"""Tests for the Phase A parallel scheduler (no Docker)."""

from __future__ import annotations

import csv
import json
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.adapters.swebench_nominal import NominalRunRequest, NominalRunResult
from earnbench.adapters.swebench_patch import sha256_hex
from earnbench.scheduler import (
    CSV_COLUMNS,
    PhaseASchedulerConfig,
    build_csv_row,
    build_job_graph,
    job_id,
    load_scheduler_state,
    run_phase_a_scheduler,
    state_path,
)

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
INSTANCE_ID = "psf__requests-1724"


class _InlineProcessPool:
    """Run submitted callables synchronously so unittest mocks apply."""

    def __init__(self, max_workers: int | None = None) -> None:
        del max_workers

    def __enter__(self) -> _InlineProcessPool:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def submit(self, fn, /, *args, **kwargs):
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


def _mock_nominal_runner(request: NominalRunRequest) -> NominalRunResult:
    return NominalRunResult(
        success=True,
        status="ok",
        harness_command="/bin/bash /eval.sh",
        log_text="mock nominal harness\n",
        tests_run=("tests.test_models.TestCase.test_redirect",),
        warnings=(),
        started_at_utc="2025-06-01T12:00:00+00:00",
        completed_at_utc="2025-06-01T12:05:00+00:00",
        patch_sha256=sha256_hex(request.patch_content),
    )


def _mock_pi_verif_runner(request: NominalRunRequest) -> NominalRunResult:
    return NominalRunResult(
        success=True,
        status="ok",
        harness_command="/bin/bash /eval.sh",
        log_text="mock pi_verif harness\n",
        tests_run=("tests.test_models.TestCase.test_redirect",),
        warnings=(),
        started_at_utc="2025-06-01T12:10:00+00:00",
        completed_at_utc="2025-06-01T12:15:00+00:00",
        patch_sha256=sha256_hex(request.patch_content),
    )


def _default_run_config() -> SWEBenchRunConfig:
    return SWEBenchRunConfig(
        workers=1,
        max_parallel_containers=1,
        max_parallel_builds=1,
        reuse_images=True,
        allow_build=False,
        cache_dir=None,
        timeout_seconds=1800,
    )


def _scheduler_config(tmp_path: Path, *, resume: bool = False) -> PhaseASchedulerConfig:
    return PhaseASchedulerConfig(
        metadata_path=METADATA_FIXTURE,
        output_dir=tmp_path,
        instance_ids=(INSTANCE_ID,),
        workers=1,
        parallel_perturbations=3,
        resume=resume,
        retry_failed=False,
        run_config=_default_run_config(),
        run_id="phase_a_test",
    )


@patch("earnbench.scheduler.ProcessPoolExecutor", _InlineProcessPool)
@patch("earnbench.scheduler.run_swebench_preflight")
@patch("earnbench.scheduler.run_nominal_grading")
@patch("earnbench.scheduler.run_pi_vtest_grading")
@patch("earnbench.scheduler.run_pi_verif_grading")
@patch("earnbench.scheduler.run_pi_env_grading")
def test_run_phase_a_scheduler_writes_csv_and_state(
    mock_pi_env,
    mock_pi_verif,
    mock_pi_vtest,
    mock_nominal,
    mock_preflight,
    tmp_path: Path,
) -> None:
    mock_preflight.return_value = {"status": "ok"}
    mock_nominal.side_effect = lambda **kwargs: _write_nominal_artifacts(**kwargs)
    mock_pi_vtest.side_effect = lambda **kwargs: _write_pi_vtest_artifacts(**kwargs)
    mock_pi_verif.side_effect = lambda **kwargs: _write_pi_verif_artifacts(**kwargs)
    mock_pi_env.side_effect = lambda **kwargs: _write_pi_env_artifacts(**kwargs)

    summary = run_phase_a_scheduler(_scheduler_config(tmp_path))

    assert summary["completed_instances"] == 1
    assert summary["failed_instances"] == 0
    assert state_path(tmp_path).is_file()
    csv_file = tmp_path / "golden_validation.csv"
    assert csv_file.is_file()
    with csv_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["instance_id"] == INSTANCE_ID
    assert rows[0]["y0"] == "True"
    assert rows[0]["pi_verif_status"] == "ok"
    assert rows[0]["pi_env_status"] == "ok"
    assert rows[0]["pi_vtest_status"] == "ok"
    assert (tmp_path / INSTANCE_ID / "report.json").is_file()


def _write_nominal_artifacts(**kwargs) -> dict[str, object]:
    from earnbench.adapters.swebench_nominal import run_nominal_grading

    return run_nominal_grading(
        runner=_mock_nominal_runner,
        **kwargs,
    )


def _write_pi_verif_artifacts(**kwargs) -> dict[str, object]:
    from earnbench.adapters.swebench_pi_verif import run_pi_verif_grading

    return run_pi_verif_grading(
        runner=_mock_pi_verif_runner,
        **kwargs,
    )


def _write_pi_vtest_artifacts(**kwargs) -> dict[str, object]:
    from earnbench.adapters.swebench_pi_vtest import run_pi_vtest_grading

    return run_pi_vtest_grading(
        runner=_mock_pi_verif_runner,
        **kwargs,
    )


def _write_pi_env_artifacts(**kwargs) -> dict[str, object]:
    from earnbench.adapters.swebench_pi_env import run_pi_env_grading

    return run_pi_env_grading(
        runner=_mock_pi_env_runner,
        **kwargs,
    )


def _mock_pi_env_runner(request):
    from earnbench.adapters.swebench_pi_env import (
        HARDENING_FLAG_NAMES,
        PiEnvHarnessResult,
    )

    return PiEnvHarnessResult(
        outcome=_mock_pi_verif_runner(request),
        hardening_flags_requested=HARDENING_FLAG_NAMES,
        hardening_flags_enforced=(
            "network_disabled",
            "python_nousersite",
            "pip_no_index",
        ),
        hardening_flags_not_enforced=("tests_mount_readonly",),
        image_digest="sha256:mock-instance-image",
    )


@patch("earnbench.scheduler.ProcessPoolExecutor", _InlineProcessPool)
@patch("earnbench.scheduler.run_swebench_preflight")
@patch("earnbench.scheduler.run_nominal_grading")
@patch("earnbench.scheduler.run_pi_vtest_grading")
@patch("earnbench.scheduler.run_pi_verif_grading")
@patch("earnbench.scheduler.run_pi_env_grading")
def test_resume_skips_completed_jobs(
    mock_pi_env,
    mock_pi_verif,
    mock_pi_vtest,
    mock_nominal,
    mock_preflight,
    tmp_path: Path,
) -> None:
    mock_preflight.return_value = {"status": "ok"}
    mock_nominal.side_effect = lambda **kwargs: _write_nominal_artifacts(**kwargs)
    mock_pi_vtest.side_effect = lambda **kwargs: _write_pi_vtest_artifacts(**kwargs)
    mock_pi_verif.side_effect = lambda **kwargs: _write_pi_verif_artifacts(**kwargs)
    mock_pi_env.side_effect = lambda **kwargs: _write_pi_env_artifacts(**kwargs)

    config = _scheduler_config(tmp_path)
    run_phase_a_scheduler(config)
    assert mock_nominal.call_count == 1

    run_phase_a_scheduler(_scheduler_config(tmp_path, resume=True))
    assert mock_nominal.call_count == 1
    assert mock_pi_vtest.call_count == 1
    assert mock_pi_verif.call_count == 1
    assert mock_pi_env.call_count == 1


def test_build_job_graph_is_deterministic() -> None:
    graph_a = build_job_graph(["b", "a"])
    graph_b = build_job_graph(["a", "b"])
    assert list(graph_a.keys()) == list(graph_b.keys())
    assert graph_a[job_id("a", "prepare")].instance_id == "a"


def test_build_csv_row_matches_golden_gate_columns() -> None:
    row = build_csv_row(
        instance_id=INSTANCE_ID,
        repo="psf/requests",
        report_payload={
            "nominal_success": True,
            "status": "defined",
            "earned_fraction": 1.0,
            "valid_count": 1,
            "reason": "",
        },
        pi_statuses={
            "pi_vtest.v1": "missing",
            "pi_verif.v1": "ok",
            "pi_env.v1": "missing",
        },
        pi_successes={
            "pi_vtest.v1": None,
            "pi_verif.v1": True,
            "pi_env.v1": None,
        },
        run_id="phase_a_test",
        config_digest="sha256:abc",
    )
    assert tuple(row.keys()) == CSV_COLUMNS
    assert row["ef_pi"] == 1.0
    assert row["retained"] is True


@patch("earnbench.scheduler.ProcessPoolExecutor", _InlineProcessPool)
@patch("earnbench.scheduler.run_swebench_preflight")
@patch("earnbench.scheduler.run_nominal_grading")
@patch("earnbench.scheduler.run_pi_verif_grading")
@patch("earnbench.scheduler.run_pi_env_grading")
def test_scheduler_persists_state_after_run(
    mock_pi_env,
    mock_pi_verif,
    mock_nominal,
    mock_preflight,
    tmp_path: Path,
) -> None:
    mock_preflight.return_value = {"status": "ok"}
    mock_nominal.side_effect = lambda **kwargs: _write_nominal_artifacts(**kwargs)
    mock_pi_verif.side_effect = lambda **kwargs: _write_pi_verif_artifacts(**kwargs)
    mock_pi_env.side_effect = lambda **kwargs: _write_pi_env_artifacts(**kwargs)

    run_phase_a_scheduler(_scheduler_config(tmp_path))
    state = load_scheduler_state(tmp_path)
    assert state is not None
    assert state.run_id == "phase_a_test"
    assert state.jobs[job_id(INSTANCE_ID, "nominal")].status.value in {
        "completed",
        "skipped",
    }


def test_cli_phase_a_smoke(capsys, tmp_path: Path) -> None:
    from earnbench.cli import main

    with (
        patch("earnbench.cli.run_phase_a_scheduler") as mock_run,
        patch("earnbench.cli.print_swebench_execution_summary"),
    ):
        mock_run.return_value = {
            "run_id": "phase_a_test",
            "instance_count": 1,
            "completed_instances": 1,
            "failed_instances": 0,
            "interrupted": False,
            "state_path": str(state_path(tmp_path)),
            "csv_path": str(tmp_path / "golden_validation.csv"),
        }
        exit_code = main(
            [
                "phase-a",
                "--metadata-parquet",
                str(METADATA_FIXTURE),
                "--instances",
                INSTANCE_ID,
                "--output",
                str(tmp_path),
                "--workers",
                "1",
                "--parallel-perturbations",
                "2",
                "--no-build",
            ]
        )

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["completed_instances"] == 1
    mock_run.assert_called_once()
