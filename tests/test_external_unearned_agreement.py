"""Tests for external unearned anchor EF agreement analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from earnbench.cli import main
from earnbench.external_unearned import (
    analyze_external_unearned_agreement,
    classify_external_unearned_agreement,
    generate_external_unearned_agreement_report,
    load_external_unearned_catalog,
    load_external_unearned_results,
    validate_external_unearned_catalog,
)
from earnbench.reports import EarnedFractionStatus

FIXTURES = Path(__file__).parent / "fixtures" / "external_unearned"

CATALOG_HEADER = (
    "external_id,source,paper_or_url,original_benchmark,original_task_id,"
    "external_label,external_label_quote,external_label_type,artifact_available,"
    "patch_available,trace_available,reproducible,mapped_channel,registry_label,"
    "expected_failed_pi,expected_ef_behavior,expected_detection,inclusion_decision,"
    "exclusion_reason,notes\n"
)


def _catalog_row(
    external_id: str,
    *,
    registry_label: str = "IN_REGISTRY",
    expected_detection: str = "detect",
    inclusion_decision: str = "include",
    mapped_channel: str = "visible_test_overfitting",
    expected_failed_pi: str = "pi_vtest.v1",
) -> str:
    return (
        f"{external_id},SpecBench,https://example.org/x,SpecBench,t1,label,"
        f"\"quote\",visible_test_overfit,yes,yes,no,yes,{mapped_channel},"
        f"{registry_label},{expected_failed_pi},low_on_y0_eq_1,{expected_detection},"
        f"{inclusion_decision},,\n"
    )


def _results_row(
    external_id: str,
    *,
    y0: str = "1",
    ef_pi: str = "0.333333",
    ef_status: str = "defined",
    failed_mechanisms: str = "visible_test_overfitting",
) -> str:
    return f"{external_id},{y0},{ef_pi},{ef_status},{failed_mechanisms}\n"


def _write_pair(tmp_path: Path, catalog_body: str, results_body: str) -> tuple[Path, Path]:
    catalog_path = tmp_path / "catalog.csv"
    results_path = tmp_path / "results.csv"
    catalog_path.write_text(CATALOG_HEADER + catalog_body, encoding="utf-8")
    results_path.write_text(
        "external_id,y0,ef_pi,ef_status,failed_mechanisms\n" + results_body,
        encoding="utf-8",
    )
    return catalog_path, results_path


def test_classify_in_registry_detected(tmp_path: Path) -> None:
    catalog_path, results_path = _write_pair(
        tmp_path,
        _catalog_row("EU001"),
        _results_row("EU001"),
    )
    catalog = load_external_unearned_catalog(catalog_path)[0]
    results = load_external_unearned_results(results_path)[0]
    agreement_class, explanation, detected, matched = classify_external_unearned_agreement(
        catalog,
        results,
    )
    assert agreement_class == "ef_detects"
    assert detected is True
    assert matched is True
    assert "IN_REGISTRY detection expected" in explanation


def test_classify_in_registry_missed_unexpectedly(tmp_path: Path) -> None:
    catalog_path, results_path = _write_pair(
        tmp_path,
        _catalog_row("EU002"),
        _results_row("EU002", ef_pi="1.0", failed_mechanisms=""),
    )
    catalog = load_external_unearned_catalog(catalog_path)[0]
    results = load_external_unearned_results(results_path)[0]
    agreement_class, explanation, detected, _ = classify_external_unearned_agreement(
        catalog,
        results,
    )
    assert agreement_class == "ef_misses_unexpected"
    assert detected is False
    assert "did not flag unearned" in explanation


def test_classify_out_of_registry_miss_expected(tmp_path: Path) -> None:
    catalog_path, results_path = _write_pair(
        tmp_path,
        _catalog_row(
            "EU003",
            registry_label="OUT_OF_REGISTRY",
            expected_detection="miss_expected",
            mapped_channel="repo_state_leakage",
            expected_failed_pi="none",
        ),
        _results_row("EU003", ef_pi="1.0", failed_mechanisms=""),
    )
    catalog = load_external_unearned_catalog(catalog_path)[0]
    results = load_external_unearned_results(results_path)[0]
    agreement_class, explanation, detected, _ = classify_external_unearned_agreement(
        catalog,
        results,
    )
    assert agreement_class == "ef_misses_expected"
    assert detected is False
    assert "false-negative floor" in explanation


def test_classify_invalid_undefined(tmp_path: Path) -> None:
    catalog_path, results_path = _write_pair(
        tmp_path,
        _catalog_row("EU004"),
        _results_row(
            "EU004",
            ef_pi="",
            ef_status=EarnedFractionStatus.UNDEFINED.value,
            failed_mechanisms="",
        ),
    )
    catalog = load_external_unearned_catalog(catalog_path)[0]
    results = load_external_unearned_results(results_path)[0]
    agreement_class, explanation, _, _ = classify_external_unearned_agreement(
        catalog,
        results,
    )
    assert agreement_class == "ef_undefined"
    assert "undefined" in explanation.lower()


def test_validate_duplicate_external_ids_in_catalog() -> None:
    result = validate_external_unearned_catalog(FIXTURES / "duplicate_id.csv")
    assert not result.ok
    assert any("duplicate external_id" in error for error in result.errors)


def test_analyze_agreement_summary() -> None:
    catalog = load_external_unearned_catalog(FIXTURES / "agreement_catalog.csv")
    results = load_external_unearned_results(FIXTURES / "agreement_results.csv")
    payload = analyze_external_unearned_agreement(catalog, results)

    assert payload["included_anchor_count"] == 4
    assert payload["agreement_class_counts"]["ef_detects"] == 1
    assert payload["agreement_class_counts"]["ef_misses_unexpected"] == 1
    assert payload["agreement_class_counts"]["ef_misses_expected"] == 1
    assert payload["agreement_class_counts"]["ef_undefined"] == 1
    assert payload["agreement_class_counts"]["excluded"] == 1
    assert payload["ef_agreement_rate"] == pytest.approx(2 / 3)
    assert payload["expected_miss_out_of_registry"]["count"] == 1
    assert len(payload["invalid_undefined_cases"]) == 1
    assert payload["agreement_by_source"]["SpecBench"]["agreement_rate"] == pytest.approx(0.5)


def test_generate_agreement_report(tmp_path: Path) -> None:
    result = generate_external_unearned_agreement_report(
        FIXTURES / "agreement_catalog.csv",
        FIXTURES / "agreement_results.csv",
        tmp_path / "out",
    )
    summary = json.loads(result.agreement_json.read_text(encoding="utf-8"))
    assert summary["schema_version"] == "earnbench.external_unearned_agreement.v1"
    assert result.agreement_csv.is_file()
    assert "EF Agreement Analysis" in result.agreement_md.read_text(encoding="utf-8")

    with result.agreement_csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["external_id"] for row in rows} == {
        "EU-IN-DETECT",
        "EU-IN-MISS",
        "EU-OOR-EXPECTED",
        "EU-UNDEF",
        "EU-DEFER",
    }
    by_id = {row["external_id"]: row for row in rows}
    assert by_id["EU-IN-DETECT"]["agreement_class"] == "ef_detects"
    assert by_id["EU-IN-MISS"]["agreement_class"] == "ef_misses_unexpected"
    assert by_id["EU-OOR-EXPECTED"]["agreement_class"] == "ef_misses_expected"
    assert by_id["EU-UNDEF"]["agreement_class"] == "ef_undefined"
    assert by_id["EU-DEFER"]["agreement_class"] == "excluded"


def test_classify_disagrees_when_y0_conflicts_with_label(tmp_path: Path) -> None:
    catalog_path, results_path = _write_pair(
        tmp_path,
        _catalog_row("EU005"),
        _results_row("EU005", y0="0", ef_pi="0.0", failed_mechanisms=""),
    )
    catalog = load_external_unearned_catalog(catalog_path)[0]
    results = load_external_unearned_results(results_path)[0]
    agreement_class, explanation, _, _ = classify_external_unearned_agreement(
        catalog,
        results,
    )
    assert agreement_class == "ef_disagrees_with_label"
    assert "Y₀=0" in explanation


def test_cli_report_external_unearned_agreement(capsys, tmp_path: Path) -> None:
    out = tmp_path / "agreement"
    exit_code = main(
        [
            "report",
            "external-unearned-agreement",
            "--catalog",
            str(FIXTURES / "agreement_catalog.csv"),
            "--results",
            str(FIXTURES / "agreement_results.csv"),
            "--output",
            str(out),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["agreement_md"]).is_file()
