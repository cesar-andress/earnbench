"""Tests for SWE-bench Docker preflight (mocked docker and image discovery)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.adapters.swebench_nominal import run_nominal_grading
from earnbench.adapters.swebench_preflight import (
    MissingDockerImagesError,
    PreflightStatus,
    RequiredImages,
    run_swebench_preflight,
)

FIXTURES = Path(__file__).parent / "fixtures"
METADATA_FIXTURE = FIXTURES / "swebench_smoke_metadata.json"
INSTANCE_ID = "psf__requests-1724"

MOCK_REQUIRED = RequiredImages(
    base="sweb.base.py.x86_64:latest",
    environment="sweb.env.py.x86_64.abc123:latest",
    instance="sweb.eval.x86_64.psf__requests-1724:latest",
)

PREFLIGHT_FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "required_images",
    "present_images",
    "missing_images",
    "build_attempted",
    "build_success",
    "actionable_commands",
    "status",
)


def _mock_discover(_row: dict) -> RequiredImages:
    return MOCK_REQUIRED


def test_run_preflight_all_images_present(tmp_path: Path) -> None:
    def inspector(image_name: str) -> bool:
        return image_name in {
            MOCK_REQUIRED.base,
            MOCK_REQUIRED.environment,
            MOCK_REQUIRED.instance or "",
        }

    payload = run_swebench_preflight(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        output_dir=tmp_path,
        discover=_mock_discover,
        inspector=inspector,
    )

    assert set(PREFLIGHT_FIELDS) <= set(payload.keys())
    assert payload["status"] == PreflightStatus.OK.value
    assert payload["missing_images"] == []
    assert payload["build_attempted"] is False
    assert payload["build_success"] is False
    assert payload["required_images"]["environment"] == MOCK_REQUIRED.environment

    instance_dir = tmp_path / INSTANCE_ID
    assert (instance_dir / "preflight.json").is_file()
    assert (instance_dir / "preflight.log").is_file()
    saved = json.loads((instance_dir / "preflight.json").read_text(encoding="utf-8"))
    assert saved == payload


def test_run_preflight_missing_images_without_build(tmp_path: Path) -> None:
    payload = run_swebench_preflight(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        output_dir=tmp_path,
        discover=_mock_discover,
        inspector=lambda _name: False,
    )

    assert payload["status"] == PreflightStatus.MISSING_IMAGES.value
    assert set(payload["missing_images"]) == {
        MOCK_REQUIRED.base,
        MOCK_REQUIRED.environment,
        MOCK_REQUIRED.instance,
    }
    assert payload["build_attempted"] is False
    assert any("preflight" in cmd for cmd in payload["actionable_commands"])


def test_run_preflight_build_failed(tmp_path: Path) -> None:
    def builder(_row: dict, _client) -> tuple[bool, str]:
        return False, "env image build failed\n"

    payload = run_swebench_preflight(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        output_dir=tmp_path,
        build_missing_images=True,
        discover=_mock_discover,
        inspector=lambda _name: False,
        builder=builder,
    )

    assert payload["build_attempted"] is True
    assert payload["build_success"] is False
    assert payload["status"] == PreflightStatus.BUILD_FAILED.value


def test_run_preflight_build_missing_images_success(tmp_path: Path) -> None:
    present: set[str] = set()

    def inspector(image_name: str) -> bool:
        return image_name in present

    def builder(_row: dict, _client) -> tuple[bool, str]:
        present.update(
            {
                MOCK_REQUIRED.base,
                MOCK_REQUIRED.environment,
                MOCK_REQUIRED.instance or "",
            }
        )
        return True, "built images via harness\n"

    payload = run_swebench_preflight(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        output_dir=tmp_path,
        build_missing_images=True,
        discover=_mock_discover,
        inspector=inspector,
        builder=builder,
    )

    assert payload["build_attempted"] is True
    assert payload["build_success"] is True
    assert payload["status"] == PreflightStatus.OK.value
    assert payload["missing_images"] == []


def test_default_build_instance_images_passes_latest_tags(monkeypatch) -> None:
    from earnbench.adapters.swebench_preflight import default_build_instance_images

    captured: dict[str, object] = {}

    def fake_build_instance_images(**kwargs):
        captured.update(kwargs)
        return ([], [])

    monkeypatch.setattr(
        "swebench.harness.docker_build.build_instance_images",
        fake_build_instance_images,
    )

    ok, log = default_build_instance_images({"instance_id": "x"}, object())

    assert ok is True
    assert captured["tag"] == "latest"
    assert captured["env_image_tag"] == "latest"
    assert log == ""


def test_run_preflight_harness_unavailable(tmp_path: Path, monkeypatch) -> None:
    def fail_discover(_row: dict) -> RequiredImages:
        from earnbench.adapters.swebench_nominal import HarnessNotInstalledError

        raise HarnessNotInstalledError("harness missing")

    payload = run_swebench_preflight(
        metadata_path=METADATA_FIXTURE,
        instance_id=INSTANCE_ID,
        output_dir=tmp_path,
        discover=fail_discover,
    )

    assert payload["status"] == PreflightStatus.HARNESS_UNAVAILABLE.value
    assert payload["required_images"] == {}
    assert any("pip install" in cmd for cmd in payload["actionable_commands"])


def test_run_nominal_suggests_preflight_when_images_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    patch_path = tmp_path / "golden.patch"
    patch_path.write_text("diff --git a/foo b/foo\n", encoding="utf-8")

    monkeypatch.setattr(
        "earnbench.adapters.swebench_preflight.check_nominal_docker_images",
        lambda **_kwargs: (MOCK_REQUIRED.environment,),
    )

    with pytest.raises(MissingDockerImagesError, match="preflight") as exc_info:
        run_nominal_grading(
            metadata_path=METADATA_FIXTURE,
            instance_id=INSTANCE_ID,
            patch_path=patch_path,
            output_dir=tmp_path,
        )

    assert MOCK_REQUIRED.environment in str(exc_info.value)
    assert "--build-missing-images" in str(exc_info.value)
