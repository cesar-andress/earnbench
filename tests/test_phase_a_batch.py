"""Tests for the Phase A batch experiment runner."""

from __future__ import annotations

import csv
import json
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.phase_a_batch import (
    BATCH_PI_ORDER,
    PhaseABatchConfig,
    build_statistics,
    load_pilot_manifest,
    run_phase_a_batch,
    write_ef_distribution_csv,
)
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID
from earnbench.registry.pi_vtest_v1 import PI_VTEST_V1_ID
from earnbench.reports import EarnedFractionStatus

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
MANIFEST_FIXTURE = FIXTURES / "pilot_manifest_smoke.json"
INSTANCE_ID = "psf__requests-1724"


class _InlineProcessPool:
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


def _batch_config(tmp_path: Path, *, resume: bool = False) -> PhaseABatchConfig:
    return PhaseABatchConfig(
        manifest_path=MANIFEST_FIXTURE,
        metadata_path=METADATA_FIXTURE,
        output_dir=tmp_path,
        workers=1,
        resume=resume,
        run_config=SWEBenchRunConfig(
            workers=1,
            max_parallel_containers=1,
            max_parallel_builds=1,
            reuse_images=True,
            allow_build=False,
            cache_dir=None,
            timeout_seconds=1800,
        ),
        run_id="phase_a_batch_test",
    )


def test_load_pilot_manifest_is_sorted() -> None:
    rows = load_pilot_manifest(MANIFEST_FIXTURE)
    assert len(rows) == 1
    assert rows[0]["instance_id"] == INSTANCE_ID


def test_build_statistics_and_ef_distribution(tmp_path: Path) -> None:
    rows = {
        INSTANCE_ID: {
            "instance_id": INSTANCE_ID,
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "ef_pi": "1.0",
            "retained": "True",
            "false_unearned": "False",
        }
    }
    stats = build_statistics(rows)
    assert stats["instance_count"] == 1
    assert stats["retained_count"] == 1
    assert stats["ef_mean"] == 1.0
    write_ef_distribution_csv(tmp_path, rows)
    with (tmp_path / "ef_distribution.csv").open(encoding="utf-8") as handle:
        body = list(csv.DictReader(handle))
    assert body == [{"earned_fraction": "1.0000", "count": "1"}]


def test_batch_pi_order_matches_protocol() -> None:
    assert BATCH_PI_ORDER == (
        PI_VERIF_V1_ID,
        PI_VTEST_V1_ID,
        PI_ENV_V1_ID,
    )


@patch("earnbench.phase_a_batch.ProcessPoolExecutor", _InlineProcessPool)
@patch("earnbench.phase_a_batch.run_instance_batch_pipeline")
def test_run_phase_a_batch_writes_outputs(
    mock_pipeline,
    tmp_path: Path,
) -> None:
    from earnbench.phase_a_batch import BatchInstanceResult

    mock_pipeline.return_value = BatchInstanceResult(
        instance_id=INSTANCE_ID,
        csv_row={
            "instance_id": INSTANCE_ID,
            "repo": "psf/requests",
            "y0": True,
            "y_vtest": True,
            "y_verif": True,
            "y_env": None,
            "pi_vtest_status": "ok",
            "pi_verif_status": "ok",
            "pi_env_status": "invalid",
            "valid_pi_count": 2,
            "ef_pi": 1.0,
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": False,
            "retained": True,
            "exclude_reason": None,
            "run_id": "phase_a_batch_test",
            "config_digest": "sha256:cfg",
        },
    )

    summary = run_phase_a_batch(_batch_config(tmp_path))

    assert summary["completed_instances"] == 1
    assert (tmp_path / "summary.csv").is_file()
    assert (tmp_path / "failures.csv").is_file()
    assert (tmp_path / "ef_distribution.csv").is_file()
    assert (tmp_path / "run_manifest.json").is_file()
    assert (tmp_path / "statistics.json").is_file()
    assert (tmp_path / "reports").is_dir()
    assert (tmp_path / "audits").is_dir()
    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "phase_a_batch_test"
    assert manifest["manifest_path"].endswith("pilot_manifest_smoke.json")


def test_cli_phase_a_run(capsys, tmp_path: Path) -> None:
    from earnbench.cli import main

    with (
        patch("earnbench.cli.run_phase_a_batch") as mock_run,
        patch("earnbench.cli.print_swebench_execution_summary"),
    ):
        mock_run.return_value = {
            "run_id": "phase_a_batch_test",
            "instance_count": 1,
            "completed_instances": 1,
            "failed_instances": 0,
            "skipped_instances": 0,
            "interrupted": False,
            "summary_csv": str(tmp_path / "summary.csv"),
        }
        exit_code = main(
            [
                "phase-a",
                "run",
                "--manifest",
                str(MANIFEST_FIXTURE),
                "--metadata-parquet",
                str(METADATA_FIXTURE),
                "--output",
                str(tmp_path),
                "--workers",
                "12",
                "--resume",
                "--no-build",
            ]
        )

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["completed_instances"] == 1
    mock_run.assert_called_once()
    config = mock_run.call_args.args[0]
    assert config.resume is True
    assert config.workers == 12
