"""SWE-bench Docker image preflight before nominal harness execution."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from earnbench.adapters.swebench_metadata import (
    SWEBenchVerifiedRecord,
    load_verified_instance,
    load_verified_instance_row,
)
from earnbench.adapters.swebench_nominal import (
    HARNESS_INSTALL_HINT,
    HarnessNotInstalledError,
    require_swebench_harness,
)

PREFLIGHT_LOG_NAME = "preflight.log"
PREFLIGHT_JSON_NAME = "preflight.json"


class PreflightStatus(str, Enum):
    """Terminal status for a preflight run."""

    OK = "ok"
    MISSING_IMAGES = "missing_images"
    BUILD_FAILED = "build_failed"
    HARNESS_UNAVAILABLE = "harness_unavailable"


@dataclass(frozen=True, slots=True)
class RequiredImages:
    """Docker images required by the SWE-bench harness for one instance."""

    base: str
    environment: str
    instance: str | None


ImageInspector = Callable[[str], bool]
ImageDiscoverer = Callable[[dict[str, Any]], RequiredImages]
ImageBuilder = Callable[[dict[str, Any], Any], tuple[bool, str]]


class MissingDockerImagesError(RuntimeError):
    """Raised when nominal grading cannot run because Docker images are absent."""

    def __init__(
        self,
        *,
        instance_id: str,
        missing_images: tuple[str, ...],
        metadata_path: Path,
        output_dir: Path,
    ) -> None:
        self.instance_id = instance_id
        self.missing_images = missing_images
        self.metadata_path = metadata_path
        self.output_dir = output_dir
        hint = format_preflight_command(
            metadata_path=metadata_path,
            instance_id=instance_id,
            output_dir=output_dir,
            build_missing_images=True,
        )
        missing = ", ".join(missing_images)
        msg = (
            f"Missing Docker image(s) for {instance_id}: {missing}\n"
            f"Run Docker preflight before nominal grading:\n{hint}"
        )
        super().__init__(msg)


def format_preflight_command(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    build_missing_images: bool,
) -> str:
    """Return a copy-pasteable ``earnbench swebench preflight`` command."""
    build_flag = " \\\n  --build-missing-images" if build_missing_images else ""
    return (
        "earnbench swebench preflight \\\n"
        f"  --metadata-parquet {metadata_path} \\\n"
        f"  --instance-id {instance_id} \\\n"
        f"  --output {output_dir}{build_flag}"
    )


def required_images_to_mapping(required: RequiredImages) -> dict[str, str]:
    """Serialize required images with stable role keys."""
    mapping = {
        "base": required.base,
        "environment": required.environment,
    }
    if required.instance is not None:
        mapping["instance"] = required.instance
    return mapping


def default_discover_required_images(instance_row: dict[str, Any]) -> RequiredImages:
    """Resolve harness Docker image names from official SWE-bench metadata."""
    require_swebench_harness()
    from swebench.harness.test_spec.test_spec import make_test_spec

    from earnbench.adapters.swebench_nominal import _normalize_row_for_harness

    row = _normalize_row_for_harness(instance_row)
    test_spec = make_test_spec(row)
    instance_image = None if test_spec.is_remote_image else test_spec.instance_image_key
    return RequiredImages(
        base=test_spec.base_image_key,
        environment=test_spec.env_image_key,
        instance=instance_image,
    )


def default_image_inspector(client: Any) -> ImageInspector:
    """Return a checker backed by ``docker image inspect``."""

    def inspect_image(image_name: str) -> bool:
        try:
            client.api.inspect_image(image_name)
            return True
        except Exception:
            return False

    return inspect_image


def partition_images(
    required: RequiredImages,
    inspector: ImageInspector,
) -> tuple[list[str], list[str]]:
    """Split required image names into present and missing lists."""
    present: list[str] = []
    missing: list[str] = []
    for image_name in required_images_to_mapping(required).values():
        if inspector(image_name):
            present.append(image_name)
        else:
            missing.append(image_name)
    return present, missing


def build_actionable_commands(
    *,
    instance_id: str,
    metadata_path: Path,
    output_dir: Path,
    harness_available: bool,
) -> list[str]:
    """Return commands the operator can run when images are missing."""
    commands = ['pip install -e ".[swebench]"']
    if harness_available:
        commands.append(
            f"python -m swebench.harness.prepare_images --instance_ids {instance_id}"
        )
    commands.append(
        format_preflight_command(
            metadata_path=metadata_path,
            instance_id=instance_id,
            output_dir=output_dir,
            build_missing_images=True,
        ).replace("\n", " ")
    )
    return commands


def default_build_instance_images(
    instance_row: dict[str, Any],
    client: Any,
) -> tuple[bool, str]:
    """Build missing SWE-bench images through the official harness API."""
    from swebench.harness.constants import LATEST
    from swebench.harness.docker_build import build_instance_images

    from earnbench.adapters.swebench_nominal import _normalize_row_for_harness

    row = _normalize_row_for_harness(instance_row)
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            _successful, failed = build_instance_images(
                client=client,
                dataset=[row],
                force_rebuild=False,
                max_workers=1,
                tag=LATEST,
                env_image_tag=LATEST,
            )
    except Exception as exc:
        log_text = buffer.getvalue()
        if log_text and not log_text.endswith("\n"):
            log_text += "\n"
        log_text += f"Build error: {exc}\n"
        return False, log_text
    log_text = buffer.getvalue()
    return len(failed) == 0, log_text


def resolve_preflight_status(
    *,
    harness_available: bool,
    missing_images: list[str],
    build_attempted: bool,
    build_success: bool,
) -> PreflightStatus:
    """Map preflight outcomes to the public status enum."""
    if not harness_available:
        return PreflightStatus.HARNESS_UNAVAILABLE
    if build_attempted and (not build_success or missing_images):
        return PreflightStatus.BUILD_FAILED
    if missing_images:
        return PreflightStatus.MISSING_IMAGES
    return PreflightStatus.OK


def build_preflight_payload(
    record: SWEBenchVerifiedRecord,
    *,
    required_images: dict[str, str],
    present_images: list[str],
    missing_images: list[str],
    build_attempted: bool,
    build_success: bool,
    actionable_commands: list[str],
    status: PreflightStatus,
) -> dict[str, Any]:
    """Build the ``preflight.json`` document."""
    return {
        "instance_id": record.instance_id,
        "repo": record.repo,
        "base_commit": record.base_commit,
        "required_images": required_images,
        "present_images": present_images,
        "missing_images": missing_images,
        "build_attempted": build_attempted,
        "build_success": build_success,
        "actionable_commands": actionable_commands,
        "status": status.value,
    }


def check_nominal_docker_images(
    *,
    metadata_path: Path,
    instance_id: str,
    discover: ImageDiscoverer | None = None,
    inspector: ImageInspector | None = None,
    docker_client: Any | None = None,
) -> tuple[str, ...]:
    """Return missing Docker image names needed for nominal grading."""
    instance_row = load_verified_instance_row(metadata_path, instance_id)
    discover_fn = discover or default_discover_required_images
    required = discover_fn(instance_row)

    if inspector is not None:
        inspect = inspector
    else:
        require_swebench_harness()
        import docker

        client = docker_client or docker.from_env()
        close_client = docker_client is None
        try:
            inspect = default_image_inspector(client)
            _present, missing = partition_images(required, inspect)
            return tuple(missing)
        finally:
            if close_client:
                client.close()

    _present, missing = partition_images(required, inspect)
    return tuple(missing)


def run_swebench_preflight(
    *,
    metadata_path: Path,
    instance_id: str,
    output_dir: Path,
    build_missing_images: bool = False,
    discover: ImageDiscoverer | None = None,
    inspector: ImageInspector | None = None,
    builder: ImageBuilder | None = None,
    docker_client: Any | None = None,
) -> dict[str, Any]:
    """Check (and optionally build) SWE-bench Docker images for one instance."""
    record = load_verified_instance(metadata_path, instance_id)
    instance_row = load_verified_instance_row(metadata_path, instance_id)
    instance_dir = output_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    log_lines: list[str] = []

    harness_available = True
    required_mapping: dict[str, str] = {}
    present_images: list[str] = []
    missing_images: list[str] = []
    build_attempted = False
    build_success = False
    client: Any | None = docker_client
    close_client = False

    try:
        discover_fn = discover or default_discover_required_images
        required = discover_fn(instance_row)
        required_mapping = required_images_to_mapping(required)
        log_lines.append(
            "Required images: "
            + ", ".join(f"{role}={name}" for role, name in required_mapping.items())
        )

        if inspector is None:
            require_swebench_harness()
            if client is None:
                import docker

                client = docker.from_env()
                close_client = True
            inspect = default_image_inspector(client)
        else:
            inspect = inspector

        present_images, missing_images = partition_images(required, inspect)
        log_lines.append(f"Present: {present_images or 'none'}")
        log_lines.append(f"Missing: {missing_images or 'none'}")

        if build_missing_images and missing_images:
            build_attempted = True
            try:
                if builder is not None:
                    build_success, build_log = builder(instance_row, client)
                elif client is not None:
                    build_success, build_log = default_build_instance_images(
                        instance_row,
                        client,
                    )
                else:
                    build_log = "Cannot build images without a Docker client."
                    build_success = False
            except Exception as exc:
                build_success = False
                build_log = f"Build error: {exc}"
            if build_log.strip():
                log_lines.append(build_log.rstrip())
            present_images, missing_images = partition_images(required, inspect)
            log_lines.append(f"After build present: {present_images or 'none'}")
            log_lines.append(f"After build missing: {missing_images or 'none'}")
    except HarnessNotInstalledError as exc:
        harness_available = False
        log_lines.append(str(exc))
    finally:
        if close_client and client is not None:
            client.close()

    actionable = build_actionable_commands(
        instance_id=record.instance_id,
        metadata_path=metadata_path,
        output_dir=output_dir,
        harness_available=harness_available,
    )
    status = resolve_preflight_status(
        harness_available=harness_available,
        missing_images=missing_images,
        build_attempted=build_attempted,
        build_success=build_success,
    )
    payload = build_preflight_payload(
        record,
        required_images=required_mapping,
        present_images=present_images,
        missing_images=missing_images,
        build_attempted=build_attempted,
        build_success=build_success,
        actionable_commands=actionable,
        status=status,
    )

    log_path = instance_dir / PREFLIGHT_LOG_NAME
    log_path.write_text("\n".join(log_lines).rstrip() + "\n", encoding="utf-8")
    json_path = instance_dir / PREFLIGHT_JSON_NAME
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = [
    "HARNESS_INSTALL_HINT",
    "MissingDockerImagesError",
    "PreflightStatus",
    "RequiredImages",
    "build_actionable_commands",
    "build_preflight_payload",
    "check_nominal_docker_images",
    "default_discover_required_images",
    "default_image_inspector",
    "format_preflight_command",
    "partition_images",
    "required_images_to_mapping",
    "run_swebench_preflight",
]
