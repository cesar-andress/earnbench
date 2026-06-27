"""Tests for maintainer-certified correctness anchor."""

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
    "upstream_commit",
    "upstream_pr",
    "upstream_issue",
    "patch_source",
    "patch_sha256",
    "merged_by_maintainer",
    "issue_closed",
    "production_only",
    "touches_tests",
    "touches_verifier",
    "touches_ci",
    "touches_environment",
    "nominal_success",
    "certification_status",
    "exclusion_reason",
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

VALID_SHA256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _certified_row(
    control_id: str,
    instance_id: str,
    *,
    touches_tests: str = "no",
    nominal_success: str = "yes",
    status: str = "certified_correct",
    exclusion_reason: str = "",
) -> list[str]:
    return [
        control_id,
        instance_id,
        "org/repo",
        "abc123def4567890",
        "https://example.org/pull/1",
        "https://example.org/issues/1",
        "upstream_merge",
        VALID_SHA256,
        "yes",
        "yes",
        "yes",
        touches_tests,
        "no",
        "no",
        "no",
        nominal_success,
        status,
        exclusion_reason,
        "",
    ]


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
    _write_manifest(manifest, [_certified_row("MC001", "inst-1")])
    result = validate_certified_controls_manifest(manifest)
    assert result.ok


def test_validate_certified_correct_rejects_touches_tests(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [_certified_row("MC001", "inst-1", touches_tests="yes")],
    )
    result = validate_certified_controls_manifest(manifest)
    assert not result.ok
    assert any("touches_tests=False" in error for error in result.errors)


def test_validate_certified_correct_requires_nominal_success(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [_certified_row("MC001", "inst-1", nominal_success="no")],
    )
    result = validate_certified_controls_manifest(manifest)
    assert not result.ok
    assert any("nominal_success=True" in error for error in result.errors)


def test_validate_undecidable_requires_exclusion_reason(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(
        manifest,
        [
            _certified_row(
                "MC-U1",
                "inst-1",
                status="undecidable",
            ),
        ],
    )
    result = validate_certified_controls_manifest(manifest)
    assert not result.ok
    assert any("exclusion_reason" in error for error in result.errors)


def test_analyze_false_unearned_rate() -> None:
    manifest_rows = [
        {
            "control_id": "MC001",
            "instance_id": "inst-1",
            "certification_status": "certified_correct",
            "nominal_success": "yes",
            "notes": "",
        },
        {
            "control_id": "MC002",
            "instance_id": "inst-2",
            "certification_status": "certified_correct",
            "nominal_success": "yes",
            "notes": "",
        },
        {
            "control_id": "MC-U1",
            "instance_id": "inst-3",
            "certification_status": "undecidable",
            "nominal_success": "no",
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
    assert payload["schema_version"] == "earnbench.maintainer_certified_correctness.v1"


def test_generate_report(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest, [_certified_row("MC001", "inst-1")])
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
    summary = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert summary["false_unearned_count"] == 0
    assert "Maintainer-Certified Correctness Anchor Report" in result.report_md.read_text(
        encoding="utf-8"
    )


def test_cli_validate_manifest_success(capsys) -> None:
    manifest = Path(__file__).parent / "fixtures" / "certified_controls" / "valid_manifest.csv"
    exit_code = main(["controls", "validate-manifest", str(manifest)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"


def test_cli_report_controls(capsys, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    _write_manifest(manifest, [_certified_row("MC001", "inst-1")])
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
            "controls",
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
