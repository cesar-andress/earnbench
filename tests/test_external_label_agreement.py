"""Tests for external-label agreement analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.external_label_agreement import (
    analyze_external_label_agreement,
    generate_external_label_agreement_report,
    validate_external_labels_table,
)
from earnbench.injection_validity import FALSE_EARNED_THRESHOLD
from earnbench.reports import EarnedFractionStatus

SUMMARY_HEADER = (
    "instance_id,repo,y0,y_vtest,y_verif,y_env,"
    "pi_vtest_status,pi_verif_status,pi_env_status,"
    "valid_pi_count,ef_pi,ef_exclude_invalid,ef_invalid_as_fail,"
    "invalid_pi_count,invalid_pi_rate,ef_sensitivity_gap,ef_status,"
    "false_unearned,retained,exclude_reason,run_id,config_digest\n"
)

LABEL_HEADER = (
    "instance_id,source,label_name,label_value,label_confidence,notes\n"
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
            "inst-1,patchdiff,weak_test,true,0.9,dev tests fail\n",
            "inst-2,swe_abs,overfit,yes,,rejected by ABS\n",
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
        },
        {
            "instance_id": "inst-low-concern",
            "y0": "true",
            "ef_pi": "0.5",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "true",
        },
        {
            "instance_id": "inst-flag-high",
            "y0": "true",
            "ef_pi": "1.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "false",
        },
        {
            "instance_id": "inst-low-clean",
            "y0": "true",
            "ef_pi": "0.8",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "true",
        },
        {
            "instance_id": "inst-unlabeled",
            "y0": "true",
            "ef_pi": "1.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "false",
        },
    ]
    label_rows = [
        {
            "instance_id": "inst-high-clean",
            "source": "patchdiff",
            "label_name": "clean",
            "label_value": "true",
            "label_confidence": "",
            "notes": "",
        },
        {
            "instance_id": "inst-low-concern",
            "source": "patchdiff",
            "label_name": "weak_test",
            "label_value": "true",
            "label_confidence": "0.95",
            "notes": "fails dev tests",
        },
        {
            "instance_id": "inst-flag-high",
            "source": "swe_abs",
            "label_name": "overfit",
            "label_value": "yes",
            "label_confidence": "",
            "notes": "",
        },
        {
            "instance_id": "inst-low-clean",
            "source": "swe_shield",
            "label_name": "design_violation",
            "label_value": "false",
            "label_confidence": "",
            "notes": "constraint ok",
        },
        {
            "instance_id": "inst-missing-summary",
            "source": "swe_bench_plus",
            "label_name": "solution_leakage",
            "label_value": "true",
            "label_confidence": "",
            "notes": "",
        },
    ]

    payload = analyze_external_label_agreement(summary_rows, label_rows)

    assert payload["summary_instance_count"] == 5
    assert payload["overlap_instance_count"] == 4
    assert payload["low_ef_threshold"] == FALSE_EARNED_THRESHOLD
    assert payload["decidable_case_count"] == 4
    assert payload["concordant_case_count"] == 2
    assert payload["concordance_rate"] == pytest.approx(0.5)
    assert payload["disagreement_count"] == 2

    disagreement_types = {
        row["disagreement_type"] for row in payload["disagreement_rows"]
    }
    assert disagreement_types == {"external_flag_ef_high", "ef_low_external_clean"}

    weak_test_row = next(
        row for row in payload["by_label_rows"] if row["label_name"] == "weak_test"
    )
    assert weak_test_row["ef_mean"] == pytest.approx(0.5)
    assert weak_test_row["low_ef_rate"] == pytest.approx(1.0)


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
            "inst-1,patchdiff,clean,true,,\n",
            "inst-2,patchdiff,weak_test,true,0.8,fails dev subset\n",
            "inst-3,swe_abs,overfit,yes,,no summary row\n",
        ],
    )
    out = tmp_path / "agreement"
    result = generate_external_label_agreement_report(summary, labels, out)

    payload = json.loads(result.agreement_json.read_text(encoding="utf-8"))
    assert payload["overlap_instance_count"] == 2
    assert result.agreement_csv.is_file()
    assert result.by_label_csv.is_file()
    assert result.agreement_table_csv.is_file()
    assert result.disagreements_csv.is_file()
    assert result.agreement_md.is_file()


def test_analyze_handles_empty_label_overlap() -> None:
    summary_rows = [
        {
            "instance_id": "inst-only",
            "y0": "true",
            "ef_pi": "1.0",
            "ef_status": EarnedFractionStatus.DEFINED.value,
            "false_unearned": "false",
        },
    ]
    payload = analyze_external_label_agreement(summary_rows, [])
    assert payload["overlap_instance_count"] == 0
    assert payload["concordance_rate"] is None
    assert payload["by_label_rows"] == []
