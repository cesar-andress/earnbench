"""Tests for external-label agreement analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.external_label_agreement import (
    DEFAULT_EF_THRESHOLD,
    analyze_external_label_agreement,
    generate_external_label_agreement_report,
    validate_external_labels_table,
)
from earnbench.reports import EarnedFractionStatus

SUMMARY_HEADER = (
    "instance_id,repo,y0,y_vtest,y_verif,y_env,"
    "pi_vtest_status,pi_verif_status,pi_env_status,"
    "valid_pi_count,ef_pi,ef_exclude_invalid,ef_invalid_as_fail,"
    "invalid_pi_count,invalid_pi_rate,ef_sensitivity_gap,ef_status,"
    "false_unearned,retained,exclude_reason,run_id,config_digest\n"
)

LABEL_HEADER = (
    "instance_id,source,label_name,label_value,label_confidence,notes,"
    "citation_key,url\n"
)


def _write_summary(path: Path, rows: list[str]) -> None:
    path.write_text(SUMMARY_HEADER + "".join(rows), encoding="utf-8")


def _write_labels(path: Path, rows: list[str]) -> None:
    path.write_text(LABEL_HEADER + "".join(rows), encoding="utf-8")


def test_validate_external_labels_table_ok(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    _write_labels(
        labels,
        [
            "inst-1,patchdiff,weak_test,true,0.9,dev tests fail,wang2026patchdiff,\n",
            "inst-2,swe_abs,overfit,yes,,rejected by ABS,yu2026sweabs,\n",
        ],
    )
    result = validate_external_labels_table(labels)
    assert result.ok
    assert result.row_count == 2


def test_validate_external_labels_table_missing_column(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text("instance_id,source\ninst-1,patchdiff\n", encoding="utf-8")
    result = validate_external_labels_table(labels)
    assert not result.ok
    assert any("label_name" in error for error in result.errors)


def test_analyze_external_label_agreement_synthetic() -> None:
    summary_rows = [
        {
            "instance_id": "inst-high-clean",
            "y0": "true",
            "ef_pi": "1.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "false",
            "retained": "true",
            "invalid_pi_rate": "0.0",
        },
        {
            "instance_id": "inst-low-concern",
            "y0": "true",
            "ef_pi": "0.5",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "true",
            "retained": "false",
            "invalid_pi_rate": "0.0",
            "failed_mechanisms": "pi_verif.v1",
        },
        {
            "instance_id": "inst-flag-high",
            "y0": "true",
            "ef_pi": "1.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "false",
            "retained": "true",
            "invalid_pi_rate": "0.0",
        },
        {
            "instance_id": "inst-low-clean",
            "y0": "true",
            "ef_pi": "0.8",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "true",
            "retained": "false",
            "invalid_pi_rate": "0.1",
        },
        {
            "instance_id": "inst-undefined",
            "y0": "true",
            "ef_pi": "",
            "ef_status": "invalid_env",
            "false_unearned": "false",
            "retained": "false",
            "invalid_pi_rate": "0.33",
        },
        {
            "instance_id": "inst-unlabeled",
            "y0": "true",
            "ef_pi": "1.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "false",
            "retained": "true",
            "invalid_pi_rate": "0.0",
        },
    ]
    label_rows = [
        {
            "instance_id": "inst-high-clean",
            "source": "patchdiff",
            "label_name": "clean",
            "label_value": "true",
        },
        {
            "instance_id": "inst-low-concern",
            "source": "patchdiff",
            "label_name": "weak_test",
            "label_value": "true",
        },
        {
            "instance_id": "inst-flag-high",
            "source": "swe_abs",
            "label_name": "overfit",
            "label_value": "yes",
        },
        {
            "instance_id": "inst-low-clean",
            "source": "swe_shield",
            "label_name": "design_violation",
            "label_value": "false",
        },
        {
            "instance_id": "inst-undefined",
            "source": "patchdiff",
            "label_name": "weak_test",
            "label_value": "true",
        },
        {
            "instance_id": "inst-missing-summary",
            "source": "swe_bench_plus",
            "label_name": "solution_leakage",
            "label_value": "true",
        },
        {
            "instance_id": "inst-low-concern",
            "source": "patchdiff",
            "label_name": "behavioral_divergence",
            "label_value": "flagged",
        },
    ]

    payload = analyze_external_label_agreement(summary_rows, label_rows)

    assert payload["schema_version"] == "earnbench.external_label_agreement.v2"
    assert payload["summary_instance_count"] == 6
    assert payload["overlap_instance_count"] == 5
    assert payload["ef_threshold"] == DEFAULT_EF_THRESHOLD
    assert payload["undefined_ef_case_count"] == 1
    assert payload["decidable_case_count"] == 5
    assert payload["concordant_case_count"] == 3
    assert payload["concordance_rate"] == pytest.approx(0.6)
    assert payload["disagreement_count"] == 2

    patchdiff = next(row for row in payload["by_source_rows"] if row["source"] == "patchdiff")
    assert patchdiff["overlap_instance_count"] == 3
    assert patchdiff["label_row_count"] == 4
    assert patchdiff["summary_coverage_rate"] == pytest.approx(3 / 6)

    weak_test_row = next(
        row for row in payload["by_label_rows"] if row["label_name"] == "weak_test"
    )
    assert weak_test_row["label_row_count"] == 2
    assert weak_test_row["ef_mean"] == pytest.approx(0.5)
    assert weak_test_row["low_ef_rate"] == pytest.approx(0.5)
    assert weak_test_row["failed_mechanism_row_count"] == 1

    confusion = {
        (row["ef_band"], row["external_polarity"], row["agreement_cell"]): row["count"]
        for row in payload["confusion_rows"]
    }
    assert confusion[("high", "flagged", "ef_high_vs_external_flagged")] == 1
    assert confusion[("low", "clean", "ef_low_vs_external_clean")] == 1


def test_analyze_respects_custom_ef_threshold() -> None:
    summary_rows = [
        {
            "instance_id": "inst-mid",
            "y0": "true",
            "ef_pi": "0.96",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "false",
            "retained": "true",
            "invalid_pi_rate": "0.0",
        },
    ]
    label_rows = [
        {
            "instance_id": "inst-mid",
            "source": "patchdiff",
            "label_name": "weak_test",
            "label_value": "true",
        },
    ]

    default_payload = analyze_external_label_agreement(summary_rows, label_rows)
    strict_payload = analyze_external_label_agreement(
        summary_rows,
        label_rows,
        ef_threshold=0.99,
    )

    assert default_payload["matched_label_row_count"] == 1
    default_active = [row for row in default_payload["confusion_rows"] if row["count"] > 0]
    strict_active = [row for row in strict_payload["confusion_rows"] if row["count"] > 0]
    assert default_active[0]["ef_band"] == "high"
    assert strict_active[0]["ef_band"] == "low"


def test_generate_external_label_agreement_report(tmp_path: Path) -> None:
    summary = tmp_path / "summary.csv"
    _write_summary(
        summary,
        [
            "inst-1,repo,true,1,1,1,ok,ok,ok,3,1.0,1.0,1.0,0,0.0,0.0,defined,false,true,,run,digest\n",
            "inst-2,repo,true,1,0,1,ok,fail,ok,2,0.6,0.6,0.6,0,0.0,0.0,defined,true,false,false_unearned,run,digest\n",
        ],
    )
    labels = tmp_path / "labels.csv"
    _write_labels(
        labels,
        [
            "inst-1,patchdiff,clean,true,,,,,\n",
            "inst-2,patchdiff,weak_test,true,0.8,fails dev subset,wang2026patchdiff,\n",
            "inst-3,swe_abs,overfit,yes,,no summary row,yu2026sweabs,\n",
        ],
    )
    out = tmp_path / "agreement"
    result = generate_external_label_agreement_report(summary, labels, out, ef_threshold=0.95)

    payload = json.loads(result.summary_json.read_text(encoding="utf-8"))
    assert payload["overlap_instance_count"] == 2
    assert result.by_source_csv.is_file()
    assert result.by_label_csv.is_file()
    assert result.confusion_csv.is_file()
    assert result.disagreements_csv.is_file()
    assert result.report_md.is_file()
    assert "inst-3" not in payload["overlap_instance_ids"]


def test_analyze_handles_empty_label_overlap() -> None:
    summary_rows = [
        {
            "instance_id": "inst-only",
            "y0": "true",
            "ef_pi": "1.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "false",
            "retained": "true",
            "invalid_pi_rate": "0.0",
        },
    ]
    payload = analyze_external_label_agreement(summary_rows, [])
    assert payload["overlap_instance_count"] == 0
    assert payload["concordance_rate"] is None
    assert payload["by_label_rows"] == []
    assert payload["by_source_rows"] == []
