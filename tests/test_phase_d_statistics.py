"""Tests for Phase D statistics.json generation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from earnbench.outcomes import OutcomeStatus
from earnbench.phase_d_regrade import (
    AGENT_RESULTS_COLUMNS,
    PhaseDRegradeConfig,
    run_phase_d,
    summarize_phase_d,
    task_key,
    write_agent_results_csv,
)
from earnbench.phase_d_statistics import (
    STATISTICS_JSON,
    build_phase_d_statistics,
    repo_from_instance_id,
    write_phase_d_statistics,
)
from earnbench.registry.pi_env_v1 import PI_ENV_V1_ID
from earnbench.registry.pi_verif_v1 import PI_VERIF_V1_ID

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
INSTANCE_ID = "psf__requests-1724"


def _blank_row(**overrides: object) -> dict[str, object]:
    row = {col: "" for col in AGENT_RESULTS_COLUMNS}
    row.update(
        {
            "agent": "alpha",
            "provider": "external_cli",
            "model": "m1",
            "instance_id": INSTANCE_ID,
            "replicate": 0,
            "attempt_status": "ok",
            "patch_path": "p.patch",
            "grade_status": "ok",
            "run_id": "phase_d_test",
            "config_digest": "digest",
        }
    )
    row.update(overrides)
    return row


def _write_grade(
    path: Path,
    *,
    success: bool,
    started_at: str = "2026-01-01T00:00:00Z",
    completed_at: str = "2026-01-01T00:01:40Z",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "instance_id": INSTANCE_ID,
                "status": OutcomeStatus.OK.value,
                "success": success,
                "started_at_utc": started_at,
                "completed_at_utc": completed_at,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_repo_from_instance_id() -> None:
    assert repo_from_instance_id("psf__requests-1724") == "psf/requests"
    assert repo_from_instance_id("django__django-13279") == "django/django"


def test_patch_applied_and_rates_from_rows() -> None:
    rows = [
        _blank_row(
            y0=True,
            y_verif=True,
            y_env=False,
            pi_verif_status="ok",
            pi_env_status="ok",
        ),
        _blank_row(
            agent="beta",
            failure_reason="malformed_patch",
            failure_stage="validate",
            grade_status="failed",
        ),
        _blank_row(
            agent="gamma",
            y0=False,
            failure_reason="nominal_failed",
            failure_stage="nominal",
            grade_status="ok",
        ),
    ]
    stats = build_phase_d_statistics(
        output_dir=Path("/tmp/unused"),
        rows={
            task_key(str(row["agent"]), INSTANCE_ID, 0): {
                col: row.get(col, "") for col in AGENT_RESULTS_COLUMNS
            }
            for row in rows
        },
        failure_rows=[],
        run_id="phase_d_test",
    )

    assert stats["patch_application_rate"]["numerator"] == 2
    assert stats["patch_application_rate"]["denominator"] == 3
    assert stats["patch_application_rate"]["rate"] == pytest.approx(2 / 3)
    assert stats["nominal_success_rate"]["numerator"] == 1
    assert stats["nominal_success_rate"]["denominator"] == 2
    assert stats["perturbation_success_rate"]["numerator"] == 1
    assert stats["perturbation_success_rate"]["denominator"] == 2
    assert stats["failure_taxonomy"]["by_failure_reason"]["malformed_patch"] == 1
    assert stats["failure_taxonomy"]["by_failure_reason"]["nominal_failed"] == 1
    assert stats["by_repository"]["psf/requests"]["graded_count"] == 3


def test_median_grading_time_from_cell_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "phase_d"
    cell_dir = output_dir / "cells/alpha" / INSTANCE_ID
    _write_grade(cell_dir / "nominal/grade.json", success=True, completed_at="2026-01-01T00:02:00Z")
    _write_grade(
        cell_dir / PI_VERIF_V1_ID / "grade.json",
        success=True,
        started_at="2026-01-01T00:02:00Z",
        completed_at="2026-01-01T00:03:00Z",
    )

    row = _blank_row(y0=True)
    stats = build_phase_d_statistics(
        output_dir=output_dir,
        rows={
            task_key("alpha", INSTANCE_ID, 0): {
                col: row.get(col, "") for col in AGENT_RESULTS_COLUMNS
            }
        },
        failure_rows=[],
    )
    assert stats["median_grading_time_seconds"] == pytest.approx(180.0)
    assert stats["grading_time_sample_count"] == 1


def test_write_phase_d_statistics(tmp_path: Path) -> None:
    payload = build_phase_d_statistics(
        output_dir=tmp_path,
        rows={},
        failure_rows=[],
        run_id="phase_d_test",
    )
    path = write_phase_d_statistics(tmp_path, payload)
    assert path.name == STATISTICS_JSON
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == payload["schema_version"]
    assert loaded["run_id"] == "phase_d_test"


def test_run_phase_d_writes_statistics_json(tmp_path: Path) -> None:
    import csv

    from earnbench.adapters.swebench import prepare_exploit, supported_perturbations
    from earnbench.adapters.swebench_config import SWEBenchRunConfig
    from earnbench.adapters.swebench_metadata import load_verified_instance
    from earnbench.phase_d_regrade import (
        PhaseDCellResult,
        PhaseDTask,
        build_agent_result_row,
    )
    from earnbench.agents.schemas import AttemptRecord
    from earnbench.scheduler import aggregate_instance

    sample_diff = (
        "diff --git a/requests/models.py b/requests/models.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/requests/models.py\n"
        "+++ b/requests/models.py\n"
        "@@ -1,3 +1,4 @@\n"
        " # header\n"
        "+fixed prod change\n"
    )

    def _write_attempts_csv(path: Path) -> None:
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
        row = {
            "agent": "ollama_devstral",
            "model": "devstral",
            "provider": "ollama",
            "instance_id": INSTANCE_ID,
            "replicate": 0,
            "seed": 1,
            "scaffold_id": "earnbench_phase_c_v1",
            "prompt_sha256": "abc",
            "patch_path": "patches/ollama_devstral/psf__requests-1724/replicate_0.patch",
            "patch_sha256": "def",
            "trajectory_log_ref": "",
            "status": "ok",
            "started_at_utc": "2026-01-01T00:00:00Z",
            "completed_at_utc": "2026-01-01T00:00:01Z",
            "error": "",
        }
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerow(row)

    phase_c = tmp_path / "phase_c"
    phase_c.mkdir()
    patch_path = phase_c / "patches/ollama_devstral/psf__requests-1724/replicate_0.patch"
    patch_path.parent.mkdir(parents=True)
    patch_path.write_text(sample_diff, encoding="utf-8")
    _write_attempts_csv(phase_c / "attempts.csv")
    attempt = AttemptRecord.from_dict(
        {
            "agent": "ollama_devstral",
            "model": "devstral",
            "provider": "ollama",
            "instance_id": INSTANCE_ID,
            "replicate": 0,
            "seed": 1,
            "scaffold_id": "earnbench_phase_c_v1",
            "prompt_sha256": "abc",
            "patch_path": str(patch_path.relative_to(phase_c)),
            "patch_sha256": "def",
            "trajectory_log_ref": "",
            "status": "ok",
            "started_at_utc": "2026-01-01T00:00:00Z",
            "completed_at_utc": "2026-01-01T00:00:01Z",
            "error": "",
        }
    )
    output = tmp_path / "phase_d"

    def _mock_cell(**kwargs):
        task: PhaseDTask = kwargs["task"]
        prepare_exploit(
            metadata_path=METADATA_FIXTURE,
            instance_id=INSTANCE_ID,
            exploit_id="mock_cell",
            patch_content=sample_diff,
            output_dir=task.work_root,
            run_id="phase_d_test",
            patch_class="agent_patch",
            y0_policy="prod_only",
        )
        record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
        scheduled = supported_perturbations(INSTANCE_ID, record.fail_to_pass)
        instance_dir = task.work_root / INSTANCE_ID
        _write_grade(instance_dir / "nominal/grade.json", success=True)
        for perturbation_id in ("pi_verif.v1", "pi_env.v1"):
            if perturbation_id in scheduled:
                _write_grade(instance_dir / perturbation_id / "grade.json", success=True)
        csv_row = aggregate_instance(
            metadata_path=METADATA_FIXTURE,
            output_dir=task.work_root,
            instance_id=INSTANCE_ID,
            scheduled_perturbations=scheduled,
            run_id="phase_d_test",
        )
        report_payload = json.loads((instance_dir / "report.json").read_text(encoding="utf-8"))
        row = build_agent_result_row(
            attempt=attempt,
            csv_row=csv_row,
            report_payload=report_payload,
        )
        return PhaseDCellResult(task_key=task.task_key, row=row, failures=())

    with patch("earnbench.phase_d_regrade.run_phase_d_cell", side_effect=_mock_cell):
        result = run_phase_d(
            PhaseDRegradeConfig(
                phase_c_run=phase_c,
                metadata_path=METADATA_FIXTURE,
                output_dir=output,
                run_config=SWEBenchRunConfig(
                    workers=1,
                    max_parallel_containers=1,
                    max_parallel_builds=1,
                    reuse_images=True,
                    allow_build=False,
                    cache_dir=None,
                    timeout_seconds=60,
                ),
            )
        )

    assert result.statistics_path.is_file()
    stats = json.loads(result.statistics_path.read_text(encoding="utf-8"))
    assert stats["graded_count"] == 1
    assert "patch_application_rate" in stats
    assert "failure_taxonomy" in stats
    assert "by_repository" in stats


def test_summarize_phase_d_regenerates_statistics_json(tmp_path: Path) -> None:
    rows = {
        task_key("alpha", INSTANCE_ID, 0): {
            col: ""
            for col in AGENT_RESULTS_COLUMNS
        }
    }
    rows[task_key("alpha", INSTANCE_ID, 0)].update(
        {
            "agent": "alpha",
            "instance_id": INSTANCE_ID,
            "replicate": 0,
            "y0": True,
            "y_verif": True,
            "pi_verif_status": "ok",
            "ef_status": "defined",
            "ef_pi": 1.0,
            "grade_status": "ok",
        }
    )
    write_agent_results_csv(tmp_path, rows)
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "run_id": "phase_d_test",
                "summary": {"skipped_ineligible_count": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "failures.csv").write_text(
        "agent,instance_id,replicate,stage,failure_reason,error,failure_detail,timestamp_utc\n",
        encoding="utf-8",
    )

    summarize_phase_d(output_dir=tmp_path)
    stats = json.loads((tmp_path / STATISTICS_JSON).read_text(encoding="utf-8"))
    assert stats["run_id"] == "phase_d_test"
    assert stats["nominal_success_rate"]["numerator"] == 1
