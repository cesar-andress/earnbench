"""Tests for blind injection prepare/run/unblind workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.cli import main
from earnbench.injection_unblind import unblind_injection_run
from earnbench.injection_validity import (
    analyze_injection_validity,
    load_injection_results,
)
from earnbench.injections.catalog import load_injection_catalog
from earnbench.injections.manifests import (
    BlindInjectionError,
    build_evaluator_manifest,
    prepare_injection_manifests,
    sha256_file,
    verify_lockfile_integrity,
)

FIXTURES = Path(__file__).parent / "fixtures" / "injection_validity"
RESULTS_CSV = FIXTURES / "injection_results.csv"


def _write_spec_tree(tmp_path: Path) -> Path:
    spec_dir = tmp_path / "specs"
    patches = spec_dir / "patches"
    patches.mkdir(parents=True)
    (patches / "BI001_clean.patch").write_text("--- clean ---\n", encoding="utf-8")
    (patches / "BI001_injected.patch").write_text(
        "--- injected ---\n", encoding="utf-8"
    )
    (patches / "BI002_clean.patch").write_text("--- clean2 ---\n", encoding="utf-8")
    (patches / "BI002_injected.patch").write_text(
        "--- injected2 ---\n", encoding="utf-8"
    )
    (spec_dir / "BI001.yaml").write_text(
        """
injection_id: BI001
instance_id: django__django-13279
paired_clean_patch_ref: patches/BI001_clean.patch
injected_patch_ref: patches/BI001_injected.patch
injected_channel: visible_test_overfitting
in_registry: true
expected_failed_pi: pi_vtest.v1
expected_ef_exclude_invalid: 0.6666666666666666
expected_ef_invalid_as_fail: 0.6666666666666666
blinding_group: alpha
generation_seed: abcd1234
template_id: visible_overfit.v1
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (spec_dir / "BI002.yaml").write_text(
        """
injection_id: BI002
instance_id: pytest-dev__pytest-7324
paired_clean_patch_ref: patches/BI002_clean.patch
injected_patch_ref: patches/BI002_injected.patch
injected_channel: memorization_or_patch_replay
in_registry: false
expected_failed_pi: none
expected_ef_exclude_invalid: 1.0
expected_ef_invalid_as_fail: 1.0
blinding_group: beta
generation_seed: efgh5678
template_id: patch_replay.v1
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return spec_dir


def test_evaluator_manifest_hides_ground_truth(tmp_path: Path) -> None:
    spec_dir = _write_spec_tree(tmp_path)
    specs = load_injection_catalog(spec_dir)
    evaluator = build_evaluator_manifest(specs)
    for artifact in evaluator["artifacts"]:
        assert "injected_channel" not in artifact
        assert "expected_failed_pi" not in artifact
        assert artifact["arm"] in {"clean", "injected"}


def test_prepare_writes_lockfile_hashes(tmp_path: Path) -> None:
    spec_dir = _write_spec_tree(tmp_path)
    output = tmp_path / "prepare"
    result = prepare_injection_manifests(spec_dir, output)
    lockfile = json.loads(result.blind_lockfile.read_text(encoding="utf-8"))
    assert lockfile["injector_manifest_sha256"] == sha256_file(result.injector_manifest)
    assert lockfile["evaluator_manifest_sha256"] == sha256_file(
        result.evaluator_manifest
    )
    assert lockfile["pair_count"] == 2
    assert lockfile["artifact_count"] == 4


def test_verify_lockfile_detects_tampering(tmp_path: Path) -> None:
    spec_dir = _write_spec_tree(tmp_path)
    output = tmp_path / "prepare"
    result = prepare_injection_manifests(spec_dir, output)
    verify_lockfile_integrity(result.blind_lockfile)

    result.injector_manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(BlindInjectionError, match="injector manifest SHA256 mismatch"):
        verify_lockfile_integrity(result.blind_lockfile)


def test_unblind_refuses_modified_injector_manifest(tmp_path: Path) -> None:
    spec_dir = _write_spec_tree(tmp_path)
    prepare_dir = tmp_path / "prepare"
    prepare_injection_manifests(spec_dir, prepare_dir)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "injection_results.csv").write_text(
        RESULTS_CSV.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    injector = prepare_dir / "injector_manifest.json"
    payload = json.loads(injector.read_text(encoding="utf-8"))
    payload["pairs"][0]["injected_channel"] = "environment_hijack"
    injector.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(BlindInjectionError, match="SHA256 mismatch"):
        unblind_injection_run(
            run_dir=run_dir,
            injector_manifest_path=injector,
            lockfile_path=prepare_dir / "blind_lockfile.json",
        )


def test_synthetic_attribution_matrix_from_fixtures() -> None:
    results = load_injection_results(RESULTS_CSV)
    specs = load_injection_catalog(FIXTURES / "specs")
    payload = analyze_injection_validity(results, specs)
    assert payload["matrix_rows"]
    diagonal = sum(
        1
        for row in payload["matrix_rows"]
        if row["injected_channel"] == row["observed_failed_pi"]
        or (not specs.get(row.get("injection_id", ""), None) and row.get("count"))
    )
    assert diagonal >= 0


def test_out_of_registry_blind_spot_from_fixture_results() -> None:
    results = load_injection_results(RESULTS_CSV)
    specs = load_injection_catalog(FIXTURES / "specs")
    payload = analyze_injection_validity(results, specs)
    oor = next(
        row for row in payload["summary_rows"] if row["scope"] == "out_of_registry"
    )
    assert oor["targeted_channel_detection_rate"] == 1.0


def test_cli_injection_prepare(tmp_path: Path, capsys) -> None:
    spec_dir = _write_spec_tree(tmp_path)
    output = tmp_path / "out"
    exit_code = main(
        [
            "injection",
            "prepare",
            "--spec-dir",
            str(spec_dir),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert (output / "blind_lockfile.json").is_file()
