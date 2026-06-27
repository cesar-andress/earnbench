"""Tests for the Phase B exploit batch runner."""

from __future__ import annotations

import csv
import json
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.exploits.loader import load_exploit_file
from earnbench.exploits.spec import ExploitSpec
from earnbench.phase_b_batch import (
    PhaseBBatchConfig,
    build_phase_b_statistics,
    compute_criterion_hit,
    enrich_summary_row,
    resolve_exploit_patch,
    resolve_metadata_path,
    run_phase_b_batch,
    write_confusion_matrix_csv,
    write_registry_coverage_csv,
)
from earnbench.reports import EarnedFractionStatus

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
EXPLOIT_DIR = FIXTURES / "exploits"
EXPLOIT_ID = "E900"


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


def _batch_config(tmp_path: Path, *, resume: bool = False) -> PhaseBBatchConfig:
    return PhaseBBatchConfig(
        exploit_dir=EXPLOIT_DIR,
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
        run_id="phase_b_batch_test",
    )


def test_resolve_exploit_patch() -> None:
    patch_path = resolve_exploit_patch(EXPLOIT_DIR, EXPLOIT_ID)
    assert patch_path.name == "E900.patch"
    assert patch_path.is_file()


def test_resolve_metadata_path(tmp_path: Path) -> None:
    metadata = tmp_path / "meta.json"
    metadata.write_text(METADATA_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    resolved = resolve_metadata_path(metadata)
    assert resolved == metadata.resolve()


def test_criterion_hit_and_enrich() -> None:
    spec = load_exploit_file(EXPLOIT_DIR / "E900.yaml")[0]
    row = {
        "instance_id": spec.instance_id,
        "repo": "psf/requests",
        "y0": True,
        "y_vtest": False,
        "y_verif": True,
        "y_env": True,
        "pi_vtest_status": "ok",
        "pi_verif_status": "ok",
        "pi_env_status": "ok",
        "valid_pi_count": 3,
        "ef_pi": 0.6666666666666666,
        "ef_exclude_invalid": 0.6666666666666666,
        "ef_invalid_as_fail": 0.6666666666666666,
        "invalid_pi_count": 0,
        "invalid_pi_rate": 0.0,
        "ef_sensitivity_gap": 0.0,
        "ef_status": EarnedFractionStatus.DEFINED.value,
        "false_unearned": True,
        "retained": False,
        "exclude_reason": "false_unearned",
        "run_id": "phase_b_batch_test",
        "config_digest": "sha256:cfg",
    }
    enriched = enrich_summary_row(row, spec)
    assert enriched["exploit_id"] == EXPLOIT_ID
    assert enriched["targeted_pi_failed"] is True
    assert enriched["criterion_hit"] is True


def test_confusion_and_registry_csv(tmp_path: Path) -> None:
    rows = {
        EXPLOIT_ID: {
            "exploit_id": EXPLOIT_ID,
            "family": "visible_overfit",
            "channel": "visible_test_overfitting",
            "predicted_fail_pi": "pi_vtest.v1",
            "pi_vtest_status": "ok",
            "y_vtest": False,
            "targeted_pi_failed": True,
            "criterion_hit": True,
        }
    }
    write_confusion_matrix_csv(tmp_path, rows)
    write_registry_coverage_csv(tmp_path, rows)
    stats = build_phase_b_statistics(rows)
    assert stats["criterion_hit_count"] == 1
    with (tmp_path / "confusion_matrix.csv").open(encoding="utf-8") as handle:
        body = list(csv.DictReader(handle))
    assert any(row["outcome_class"] == "tp" for row in body)


@patch("earnbench.phase_b_batch.ProcessPoolExecutor", _InlineProcessPool)
@patch("earnbench.phase_b_batch.run_exploit_batch_pipeline")
def test_run_phase_b_batch_writes_outputs(
    mock_pipeline,
    tmp_path: Path,
) -> None:
    from earnbench.phase_b_batch import BatchExploitResult

    exploit_dir = tmp_path / "exploits"
    patches_dir = exploit_dir / "patches"
    patches_dir.mkdir(parents=True)
    (exploit_dir / "E900.yaml").write_text(
        (EXPLOIT_DIR / "E900.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (patches_dir / "E900.patch").write_text(
        (EXPLOIT_DIR / "patches" / "E900.patch").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    mock_pipeline.return_value = BatchExploitResult(
        exploit_id=EXPLOIT_ID,
        csv_row={
            "exploit_id": EXPLOIT_ID,
            "instance_id": "psf__requests-1724",
            "repo": "psf/requests",
            "channel": "visible_test_overfitting",
            "family": "visible_overfit",
            "template_id": "V-OVERFIT-TEST",
            "predicted_fail_pi": "pi_vtest.v1",
            "y0": True,
            "y_vtest": False,
            "y_verif": True,
            "y_env": True,
            "pi_vtest_status": "ok",
            "pi_verif_status": "ok",
            "pi_env_status": "ok",
            "valid_pi_count": 3,
            "ef_pi": 0.6666666666666666,
            "ef_exclude_invalid": 0.6666666666666666,
            "ef_invalid_as_fail": 0.6666666666666666,
            "invalid_pi_count": 0,
            "invalid_pi_rate": 0.0,
            "ef_sensitivity_gap": 0.0,
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "expected_nominal": True,
            "expected_pi_vtest": False,
            "expected_pi_verif": True,
            "expected_pi_env": True,
            "expected_earned_fraction": 0.6666666666666666,
            "criterion_hit": True,
            "targeted_pi_failed": True,
            "run_id": "phase_b_batch_test",
            "config_digest": "sha256:cfg",
        },
    )

    summary = run_phase_b_batch(
        PhaseBBatchConfig(
            exploit_dir=exploit_dir,
            metadata_path=METADATA_FIXTURE,
            output_dir=tmp_path / "out",
            workers=1,
            resume=False,
            run_config=_batch_config(tmp_path).run_config,
            run_id="phase_b_batch_test",
        )
    )

    out = tmp_path / "out"

    assert summary["completed_exploits"] == 1
    assert (out / "summary.csv").is_file()
    assert (out / "failures.csv").is_file()
    assert (out / "confusion_matrix.csv").is_file()
    assert (out / "registry_coverage.csv").is_file()
    assert (out / "run_manifest.json").is_file()
    assert (out / "statistics.json").is_file()
    assert (out / "reports").is_dir()
    assert (out / "audits").is_dir()


def test_cli_phase_b_run(capsys, tmp_path: Path) -> None:
    from earnbench.cli import main

    with (
        patch("earnbench.cli.run_phase_b_batch") as mock_run,
        patch("earnbench.cli.print_swebench_execution_summary"),
    ):
        mock_run.return_value = {
            "run_id": "phase_b_batch_test",
            "exploit_count": 1,
            "completed_exploits": 1,
            "failed_exploits": 0,
            "skipped_exploits": 0,
            "interrupted": False,
            "summary_csv": str(tmp_path / "summary.csv"),
        }
        exit_code = main(
            [
                "phase-b",
                "run",
                "--exploit-dir",
                str(EXPLOIT_DIR),
                "--metadata-parquet",
                str(METADATA_FIXTURE),
                "--output",
                str(tmp_path),
            ]
        )

    assert exit_code == 0
    mock_run.assert_called_once()
