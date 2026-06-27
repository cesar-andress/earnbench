"""Tests for cross-oracle agreement analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.cross_oracle_agreement import (
    analyze_cross_oracle_agreement,
    generate_cross_oracle_agreement_report,
    validate_cross_oracle_table,
)

HEADER = (
    "instance_id,oracle_a,oracle_b,oracle_a_outcome,oracle_b_outcome\n"
)


def test_validate_cross_oracle_table_ok(tmp_path: Path) -> None:
    table = tmp_path / "cross_oracle.csv"
    table.write_text(
        HEADER
        + "inst-1,harness,maintainer,pass,pass\n"
        + "inst-2,harness,maintainer,fail,pass\n",
        encoding="utf-8",
    )
    result = validate_cross_oracle_table(table)
    assert result.ok
    assert result.row_count == 2


def test_analyze_cross_oracle_agreement_kappa() -> None:
    rows = [
        {
            "instance_id": "a",
            "oracle_a": "h",
            "oracle_b": "m",
            "oracle_a_outcome": "pass",
            "oracle_b_outcome": "pass",
        },
        {
            "instance_id": "b",
            "oracle_a": "h",
            "oracle_b": "m",
            "oracle_a_outcome": "fail",
            "oracle_b_outcome": "fail",
        },
    ]
    payload = analyze_cross_oracle_agreement(rows)
    assert payload["agreement_rate"] == pytest.approx(1.0)
    assert payload["cohen_kappa"] == pytest.approx(1.0)


def test_generate_cross_oracle_agreement_report(tmp_path: Path) -> None:
    table = tmp_path / "cross_oracle.csv"
    table.write_text(
        HEADER + "inst-1,harness,maintainer,1,0\n",
        encoding="utf-8",
    )
    out = tmp_path / "agreement"
    result = generate_cross_oracle_agreement_report(table, out)
    payload = json.loads(result.agreement_json.read_text(encoding="utf-8"))
    assert payload["disagreement_count"] == 1
    assert result.disagreements_csv.is_file()
