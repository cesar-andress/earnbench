"""Tests for external unearned anchor catalog and report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.cli import main
from earnbench.external_unearned import (
    analyze_external_unearned_anchors,
    generate_external_unearned_report,
    load_external_unearned_catalog,
    load_external_unearned_results,
    validate_external_unearned_catalog,
)
from earnbench.reports import EarnedFractionStatus

FIXTURES = Path(__file__).parent / "fixtures" / "external_unearned"


def test_validate_valid_catalog() -> None:
    result = validate_external_unearned_catalog(FIXTURES / "valid_catalog.csv")
    assert result.ok
    assert result.row_count == 1


def test_validate_duplicate_external_id() -> None:
    result = validate_external_unearned_catalog(FIXTURES / "duplicate_id.csv")
    assert not result.ok
    assert any("duplicate external_id" in error for error in result.errors)


def test_validate_invalid_label_type() -> None:
    result = validate_external_unearned_catalog(FIXTURES / "invalid_label_type.csv")
    assert not result.ok
    assert any("external_label_type" in error for error in result.errors)


def test_analyze_detection_and_miss_rates() -> None:
    catalog = load_external_unearned_catalog(FIXTURES / "synthetic_catalog.csv")
    results = load_external_unearned_results(FIXTURES / "synthetic_results.csv")
    payload = analyze_external_unearned_anchors(catalog, results)

    assert payload["included_anchor_count"] == 2
    assert payload["in_registry_detection"]["eligible_count"] == 1
    assert payload["in_registry_detection"]["detected_count"] == 1
    assert payload["in_registry_detection"]["detection_rate"] == pytest.approx(1.0)
    assert payload["out_of_registry_miss"]["eligible_count"] == 1
    assert payload["out_of_registry_miss"]["expected_miss_count"] == 1
    assert payload["false_negative_floor"] == pytest.approx(1.0)


def test_generate_report(tmp_path: Path) -> None:
    result = generate_external_unearned_report(
        FIXTURES / "synthetic_catalog.csv",
        FIXTURES / "synthetic_results.csv",
        tmp_path / "out",
    )
    summary = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert summary["schema_version"] == "earnbench.external_unearned_anchor.v1"
    assert result.join_csv.is_file()
    assert "External Unearned Anchor Report" in result.report_md.read_text(encoding="utf-8")


def test_cli_validate_catalog_success(capsys) -> None:
    exit_code = main(
        ["external-unearned", "validate-catalog", str(FIXTURES / "valid_catalog.csv")]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"


def test_cli_report_external_unearned(capsys, tmp_path: Path) -> None:
    out = tmp_path / "report"
    exit_code = main(
        [
            "report",
            "external-unearned",
            "--catalog",
            str(FIXTURES / "synthetic_catalog.csv"),
            "--results",
            str(FIXTURES / "synthetic_results.csv"),
            "--output",
            str(out),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["report_md"]).is_file()
