"""Integration tests for external unearned execution pipeline."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from earnbench.adapters.swebench import prepare_exploit, supported_perturbations
from earnbench.adapters.swebench_config import SWEBenchRunConfig
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.cli import main
from earnbench.external_unearned import (
    ExternalUnearnedExecuteConfig,
    generate_external_unearned_report,
    import_external_unearned_patches,
    load_external_unearned_results,
    run_external_unearned_execution,
    validate_execution_manifest,
    validate_external_unearned_results,
)
from earnbench.external_unearned.report import RESULTS_REQUIRED_COLUMNS
from earnbench.outcomes import OutcomeStatus
from earnbench.scheduler import aggregate_instance

FIXTURES = Path(__file__).parent / "fixtures" / "external_unearned"
METADATA_FIXTURE = Path(__file__).parent / "fixtures" / "swebench_smoke_metadata.json"
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


def _mock_external_case(
    *,
    work_root: Path,
    external_id: str,
    y0: bool,
    pi_success: dict[str, bool],
) -> dict[str, str]:
    prepare_exploit(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        exploit_id=external_id,
        patch_content=SAMPLE_DIFF,
        output_dir=work_root,
        run_id="external_unearned_test",
        patch_class="external_unearned",
        y0_policy="prod_only",
    )
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    scheduled = supported_perturbations(INSTANCE_ID, record.fail_to_pass)
    instance_dir = work_root / INSTANCE_ID
    _write_grade(instance_dir / "nominal" / "grade.json", success=y0)
    for perturbation_id, success in pi_success.items():
        if perturbation_id in scheduled:
            _write_grade(instance_dir / perturbation_id / "grade.json", success=success)
    csv_row = aggregate_instance(
        metadata_path=METADATA_FIXTURE,
        output_dir=work_root,
        instance_id=INSTANCE_ID,
        scheduled_perturbations=scheduled,
        run_id="external_unearned_test",
    )
    report_payload = json.loads((instance_dir / "report.json").read_text(encoding="utf-8"))
    from earnbench.external_unearned.execute import build_external_unearned_result_row

    return build_external_unearned_result_row(
        external_id=external_id,
        csv_row=csv_row,
        report_payload=report_payload,
    )


def test_validate_execution_manifest_with_catalog_and_patches() -> None:
    result = validate_execution_manifest(
        FIXTURES / "execution_manifest.csv",
        catalog_path=FIXTURES / "synthetic_catalog.csv",
        patches_root=FIXTURES,
        require_patch_files=True,
    )
    assert result.ok
    assert result.row_count == 3


def test_import_patches_writes_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    result = import_external_unearned_patches(
        manifest_path=FIXTURES / "execution_manifest.csv",
        output_dir=bundle,
        catalog_path=FIXTURES / "synthetic_catalog.csv",
        patches_root=FIXTURES,
    )
    assert result.imported_count == 3
    assert (bundle / "patches" / "EU001.patch").is_file()
    assert (bundle / "execution_manifest.csv").is_file()
    assert (bundle / "patch_manifest.json").is_file()
    manifest = json.loads((bundle / "patch_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["patches"]) == 3


def test_run_execution_exports_results_csv(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    import_external_unearned_patches(
        manifest_path=FIXTURES / "execution_manifest.csv",
        output_dir=bundle,
        catalog_path=FIXTURES / "synthetic_catalog.csv",
        patches_root=FIXTURES,
    )
    output = tmp_path / "run"

    def _mock_case(task):
        from earnbench.external_unearned.execute import ExternalUnearnedExecutionResult

        work_root = Path(task.output_dir) / "cases" / task.row.external_id.replace("/", "_")
        if task.row.external_id == "EU001":
            row = _mock_external_case(
                work_root=work_root,
                external_id="EU001",
                y0=True,
                pi_success={
                    "pi_vtest.v1": False,
                    "pi_verif.v1": True,
                    "pi_env.v1": True,
                },
            )
        else:
            row = _mock_external_case(
                work_root=work_root,
                external_id="EU002",
                y0=True,
                pi_success={
                    "pi_vtest.v1": True,
                    "pi_verif.v1": True,
                    "pi_env.v1": True,
                },
            )
        return ExternalUnearnedExecutionResult(
            external_id=task.row.external_id,
            row=row,
            failure=None,
        )

    with patch(
        "earnbench.external_unearned.execute.run_external_unearned_case",
        side_effect=_mock_case,
    ):
        result = run_external_unearned_execution(
            ExternalUnearnedExecuteConfig(
                catalog_path=FIXTURES / "synthetic_catalog.csv",
                bundle_dir=bundle,
                metadata_path=METADATA_FIXTURE,
                output_dir=output,
                run_config=_run_config(),
            )
        )

    assert result.completed_count == 2
    assert result.results_csv.is_file()
    rows = load_external_unearned_results(result.results_csv)
    assert [row["external_id"] for row in rows] == ["EU001", "EU002"]
    eu001 = rows[0]
    assert eu001["y0"] == "1"
    assert eu001["ef_status"] == "defined"
    assert float(eu001["ef_pi"]) == pytest.approx(0.666667, rel=1e-5)
    assert eu001["failed_mechanisms"] == "visible_test_overfitting"
    validation = validate_external_unearned_results(result.results_csv)
    assert validation.ok


def test_execution_results_feed_existing_report(tmp_path: Path) -> None:
    output = tmp_path / "run"
    results_csv = output / "external_unearned_results.csv"
    output.mkdir()
    results_csv.write_text(
        (FIXTURES / "synthetic_results.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    report_out = tmp_path / "report"
    report = generate_external_unearned_report(
        FIXTURES / "synthetic_catalog.csv",
        results_csv,
        report_out,
    )
    summary = json.loads(report.summary_json.read_text(encoding="utf-8"))
    assert summary["included_anchor_count"] == 2
    assert summary["in_registry_detection"]["detection_rate"] == pytest.approx(1.0)

    with results_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(RESULTS_REQUIRED_COLUMNS)


def test_cli_import_and_validate_manifest(capsys, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    exit_code = main(
        [
            "external-unearned",
            "import-patches",
            "--manifest",
            str(FIXTURES / "execution_manifest.csv"),
            "--catalog",
            str(FIXTURES / "synthetic_catalog.csv"),
            "--patches-dir",
            str(FIXTURES),
            "--output",
            str(bundle),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["imported_count"] == 3

    exit_code = main(
        [
            "external-unearned",
            "validate-manifest",
            str(bundle / "execution_manifest.csv"),
            "--catalog",
            str(FIXTURES / "synthetic_catalog.csv"),
            "--require-patches",
        ]
    )
    assert exit_code == 0
