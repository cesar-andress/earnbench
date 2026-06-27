"""Tests for certified correct control manifest and report."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from earnbench.certified_controls import (
    analyze_certified_controls,
    generate_certified_controls_report,
    validate_certified_controls_manifest,
)
from earnbench.cli import main
from earnbench.reports import EarnedFractionStatus

MANIFEST_HEADER = (
    "control_id",
    "instance_id",
    "repo",
    "patch_source",
    "patch_ref",
    "upstream_commit",
    "issue_ref",
    "certification_basis",
    "production_only",
    "touches_tests",
    "touches_verifier",
    "touches_environment",
    "minimality_score",
    "issue_alignment_score",
    "certification_status",
    "undecidable_reason",
    "notes",
)

SUMMARY_HEADER = (
    "instance_id",
    "repo",
    "y0",
    "y_vtest",
    "y_verif",
    "y_env",
    "pi_vtest_status",
    "pi_verif_status",
    "pi_env_status",
    "valid_pi_count",
    "ef_pi",
    "ef_exclude_invalid",
    "ef_invalid_as_fail",
    "invalid_pi_count",
    "invalid_pi_rate",
    "ef_sensitivity_gap",
    "ef_status",
    "false_unearned",
    "retained",
    "exclude_reason",
    "run_id",
    "config_digest",
)


def _write_manifest(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MANIFEST_HEADER)
        writer.writerows(rows)


def _write_phase_a_summary(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SUMMARY_HEADER)
        writer.writerows(rows)


def test_validate_certified_correct_row_ok(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [
            [
                "CC001",
                "inst-1",
                "org/repo",
                "upstream_merge",
                "abc123",
                "abc123",
                "https://example.org/1",
                "merged_upstream_pr;issue_linked;prod_only_extract",
                "yes",
                "no",
                "no",
                "no",
                "0.9",
                "0.95",
                "certified_correct",
                "",
                "",
            ],
        ],
    )
    result = validate_certified_controls_manifest(manifest)
    assert result.ok


def test_validate_certified_correct_rejects_test_touch(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [
            [
                "CC001",
                "inst-1",
                "org/repo",
                "upstream_merge",
                "abc123",
                "abc123",
                "https://example.org/1",
                "merged_upstream_pr;issue_linked;prod_only_extract",
                "yes",
                "yes",
                "no",
                "no",
                "0.9",
                "0.95",
                "certified_correct",
                "",
                "",
            ],
        ],
    )
    result = validate_certified_controls_manifest(manifest)
    assert not result.ok
    assert any("touches_tests=True" in error for error in result.errors)


def test_validate_undecidable_requires_reason(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [
            [
                "CC-U1",
                "inst-1",
                "org/repo",
                "upstream_merge",
                "abc123",
                "abc123",
                "https://example.org/1",
                "merged_upstream_pr",
                "yes",
                "no",
                "no",
                "no",
                "0.5",
                "0.5",
                "undecidable",
                "",
                "",
            ],
        ],
    )
    result = validate_certified_controls_manifest(manifest)
    assert not result.ok
    assert any("undecidable_reason" in error for error in result.errors)


def test_analyze_false_unearned_rate() -> None:
    manifest_rows = [
        {
            "control_id": "CC001",
            "instance_id": "inst-1",
            "certification_status": "certified_correct",
            "certification_basis": "merged_upstream_pr",
            "notes": "",
        },
        {
            "control_id": "CC002",
            "instance_id": "inst-2",
            "certification_status": "certified_correct",
            "certification_basis": "merged_upstream_pr",
            "notes": "",
        },
        {
            "control_id": "CC-U1",
            "instance_id": "inst-3",
            "certification_status": "undecidable",
            "certification_basis": "",
            "notes": "",
        },
    ]
    phase_a_rows = [
        {
            "instance_id": "inst-1",
            "y0": True,
            "y_vtest": True,
            "y_verif": True,
            "y_env": True,
            "pi_vtest_status": "success",
            "pi_verif_status": "success",
            "pi_env_status": "success",
            "ef_pi": 1.0,
            "ef_exclude_invalid": 1.0,
            "ef_invalid_as_fail": 1.0,
            "invalid_pi_count": 0,
            "invalid_pi_rate": 0.0,
            "ef_sensitivity_gap": 0.0,
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": False,
        },
        {
            "instance_id": "inst-2",
            "y0": True,
            "y_vtest": False,
            "y_verif": True,
            "y_env": True,
            "pi_vtest_status": "success",
            "pi_verif_status": "success",
            "pi_env_status": "success",
            "ef_pi": 0.666667,
            "ef_exclude_invalid": 0.666667,
            "ef_invalid_as_fail": 0.666667,
            "invalid_pi_count": 0,
            "invalid_pi_rate": 0.0,
            "ef_sensitivity_gap": 0.0,
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": True,
        },
    ]
    payload = analyze_certified_controls(manifest_rows, phase_a_rows)
    assert payload["certified_correct_count"] == 2
    assert payload["undecidable_count"] == 1
    assert payload["false_unearned_count"] == 1
    assert payload["false_unearned_rate"] == pytest.approx(0.5)
    assert payload["false_unearned_mechanisms"]["visible_test_overfitting"] == 1


def test_generate_certified_controls_report(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [
            [
                "CC001",
                "inst-1",
                "org/repo",
                "upstream_merge",
                "abc123",
                "abc123",
                "https://example.org/1",
                "merged_upstream_pr;issue_linked;prod_only_extract",
                "yes",
                "no",
                "no",
                "no",
                "0.9",
                "0.95",
                "certified_correct",
                "",
                "",
            ],
        ],
    )
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    _write_phase_a_summary(
        phase_a / "summary.csv",
        [
            [
                "inst-1",
                "org/repo",
                "1",
                "1",
                "1",
                "1",
                "success",
                "success",
                "success",
                "3",
                "1.0",
                "1.0",
                "1.0",
                "0",
                "0.0",
                "0.0",
                EarnedFractionStatus.DEFINED.value,
                "0",
                "1",
                "",
                "run-1",
                "digest",
            ],
        ],
    )
    out = tmp_path / "report"
    result = generate_certified_controls_report(manifest, phase_a, out)
    assert result.report_md.is_file()
    summary = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert summary["schema_version"] == "earnbench.certified_controls.v1"
    assert summary["false_unearned_count"] == 0
    body = result.report_md.read_text(encoding="utf-8")
    assert "Certified Correct Control Study Report" in body


def test_cli_validate_manifest_success(capsys) -> None:
    manifest = Path(__file__).parent / "fixtures" / "certified_controls" / "valid_manifest.csv"
    exit_code = main(["controls", "validate-manifest", str(manifest)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"


def test_cli_report_certified_controls(capsys, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [
            [
                "CC001",
                "inst-1",
                "org/repo",
                "upstream_merge",
                "abc123",
                "abc123",
                "https://example.org/1",
                "merged_upstream_pr;issue_linked;prod_only_extract",
                "yes",
                "no",
                "no",
                "no",
                "0.9",
                "0.95",
                "certified_correct",
                "",
                "",
            ],
        ],
    )
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    _write_phase_a_summary(
        phase_a / "summary.csv",
        [
            [
                "inst-1",
                "org/repo",
                "1",
                "1",
                "1",
                "1",
                "success",
                "success",
                "success",
                "3",
                "1.0",
                "1.0",
                "1.0",
                "0",
                "0.0",
                "0.0",
                EarnedFractionStatus.DEFINED.value,
                "0",
                "1",
                "",
                "run-1",
                "digest",
            ],
        ],
    )
    out = tmp_path / "out"
    exit_code = main(
        [
            "report",
            "certified-controls",
            "--manifest",
            str(manifest),
            "--phase-a-run",
            str(phase_a),
            "--output",
            str(out),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["report_md"]).is_file()
