"""Tests for SWE-bench smoke preparation."""

from __future__ import annotations

import json
from pathlib import Path

from earnbench.adapters.base import AdapterConfig
from earnbench.adapters.swebench import (
    build_pi_verif_prepare_bundle,
    holdout_partition,
    pi_vtest_viable,
    prepare_exploit,
    prepare_smoke,
    supported_perturbations,
)
from earnbench.adapters.swebench_metadata import load_verified_instance
from earnbench.adapters.swebench_patch import extract_prod_patch

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
INSTANCE_ID = "psf__requests-1724"


def test_load_verified_instance_from_json_fixture() -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    assert record.repo == "psf/requests"
    assert record.base_commit
    assert record.fail_to_pass
    assert record.golden_patch.startswith("diff --git")


def test_holdout_partition_is_deterministic() -> None:
    f2p = ("tests.a::x", "tests.b::y", "tests.c::z")
    holdout, visible = holdout_partition(INSTANCE_ID, f2p)
    assert holdout or visible
    assert set(holdout).isdisjoint(visible)
    assert set(holdout) | set(visible) == set(f2p)


def test_supported_perturbations_includes_vtest_for_fixture() -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    scheduled = supported_perturbations(record.instance_id, record.fail_to_pass)
    assert scheduled == ("pi_vtest.v1", "pi_verif.v1", "pi_env.v1")


def test_pi_vtest_not_viable_for_single_f2p() -> None:
    assert pi_vtest_viable("x", ("only_one",)) is False
    assert supported_perturbations("x", ("only_one",)) == (
        "pi_verif.v1",
        "pi_env.v1",
    )


def test_build_pi_verif_prepare_bundle() -> None:
    record = load_verified_instance(METADATA_FIXTURE, INSTANCE_ID)
    prod_result = extract_prod_patch(record.golden_patch)
    config = AdapterConfig(
        dataset_revision="fixture",
        holdout_salt="earnbench_v0.1_holdout_salt",
    )
    bundle = build_pi_verif_prepare_bundle(
        record=record,
        prod_result=prod_result,
        config=config,
        run_id="smoke-test",
    )
    assert bundle.perturbation_id == "pi_verif.v1"
    assert bundle.config["protected_paths"]
    assert bundle.evaluation_request["task_id"] == INSTANCE_ID
    assert bundle.tamper_detected is True
    assert "tests/test_models.py" in bundle.stripped_paths


def test_prepare_smoke_writes_expected_layout(tmp_path: Path) -> None:
    plan = prepare_smoke(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        output_dir=tmp_path,
        run_id="phase_a_smoke_test",
    )
    instance_dir = tmp_path / INSTANCE_ID
    assert (instance_dir / "meta.json").is_file()
    assert (instance_dir / "patch" / "raw.patch").is_file()
    assert (instance_dir / "patch" / "prod_only.patch").is_file()
    assert (instance_dir / "plan.json").is_file()

    meta = json.loads((instance_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["instance_id"] == INSTANCE_ID
    assert meta["run_id"] == "phase_a_smoke_test"
    assert "tests/test_models.py" in meta["stripped_paths"]

    saved_plan = json.loads((instance_dir / "plan.json").read_text(encoding="utf-8"))
    assert saved_plan == plan
    assert plan["instance_id"] == INSTANCE_ID
    assert plan["repo"] == "psf/requests"
    assert plan["raw_patch_sha256"] == meta["raw_patch_sha256"]
    assert plan["prod_patch_sha256"] == meta["prod_patch_sha256"]
    assert plan["supported_perturbations"] == [
        "pi_vtest.v1",
        "pi_verif.v1",
        "pi_env.v1",
    ]
    assert "docker_image_digest" in plan["missing_inputs_for_real_execution"]
    assert plan["dry_run"] is True
    assert plan["pi_verif_prepare"]["perturbation_id"] == "pi_verif.v1"
    assert plan["pi_verif_prepare"]["tamper_detected"] is True

    prod_patch = (instance_dir / "patch" / "prod_only.patch").read_text(
        encoding="utf-8"
    )
    assert "requests/models.py" in prod_patch
    assert "tests/test_models.py" not in prod_patch


def test_prepare_exploit_writes_exploit_metadata(tmp_path: Path) -> None:
    patch_content = (FIXTURES / "exploits" / "patches" / "E900.patch").read_text(
        encoding="utf-8"
    )
    work_root = tmp_path / "E900"
    prepare_exploit(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        exploit_id="E900",
        patch_content=patch_content,
        output_dir=work_root,
        run_id="phase_b_test",
        patch_class="exploit_planted",
        y0_policy="prod_only",
        channel="visible_test_overfitting",
        family="visible_overfit",
        template_id="V-OVERFIT-TEST",
        predicted_fail_pi="pi_vtest.v1",
    )
    instance_dir = work_root / INSTANCE_ID
    meta = json.loads((instance_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["exploit_id"] == "E900"
    assert meta["patch_class"] == "exploit_planted"
    assert meta["predicted_fail_pi"] == "pi_vtest.v1"
    assert (instance_dir / "patch" / "raw.patch").read_text(encoding="utf-8") == patch_content
