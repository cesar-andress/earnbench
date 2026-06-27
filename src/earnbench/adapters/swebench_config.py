"""Performance and cache configuration for SWE-bench CLI commands."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_WORKERS = 1


@dataclass(frozen=True, slots=True)
class SWEBenchRunConfig:
    """Resolved execution settings for SWE-bench preflight and grading."""

    workers: int
    reuse_images: bool
    allow_build: bool
    cache_dir: Path | None
    timeout_seconds: int

    @property
    def effective_instance_workers(self) -> int:
        """Instance-level parallelism reserved for future batch mode."""
        return max(1, self.workers)

    @property
    def effective_harness_build_workers(self) -> int:
        """Harness Docker build pool size for one instance (always serial)."""
        return 1

    @property
    def force_rebuild(self) -> bool:
        return not self.reuse_images


def load_swebench_config_file(path: Path | None) -> dict[str, Any]:
    """Load optional JSON config overrides."""
    if path is None:
        return {}
    if not path.is_file():
        msg = f"config file not found: {path}"
        raise FileNotFoundError(msg)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"config file must contain a JSON object: {path}"
        raise ValueError(msg)
    return data


def resolve_swebench_run_config(
    *,
    config_path: Path | None = None,
    workers: int | None = None,
    reuse_images: bool | None = None,
    no_build: bool = False,
    cache_dir: Path | None = None,
    timeout_seconds: int | None = None,
) -> SWEBenchRunConfig:
    """Merge file defaults with CLI flags into one run configuration."""
    file_data = load_swebench_config_file(config_path)
    merged: dict[str, Any] = {
        "workers": DEFAULT_WORKERS,
        "reuse_images": True,
        "allow_build": True,
        "cache_dir": None,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
    }
    for key in merged:
        if key in file_data and file_data[key] is not None:
            merged[key] = file_data[key]

    if workers is not None:
        merged["workers"] = workers
    if reuse_images is not None:
        merged["reuse_images"] = reuse_images
    if cache_dir is not None:
        merged["cache_dir"] = str(cache_dir)
    if timeout_seconds is not None:
        merged["timeout_seconds"] = timeout_seconds
    if no_build:
        merged["allow_build"] = False
    elif "allow_build" in file_data and file_data["allow_build"] is False:
        merged["allow_build"] = False

    resolved_workers = int(merged["workers"])
    if resolved_workers < 1:
        msg = f"--workers must be >= 1, got {resolved_workers}"
        raise ValueError(msg)

    resolved_timeout = int(merged["timeout_seconds"])
    if resolved_timeout < 1:
        msg = f"--timeout-seconds must be >= 1, got {resolved_timeout}"
        raise ValueError(msg)

    cache_path: Path | None
    raw_cache = merged.get("cache_dir")
    if raw_cache in (None, ""):
        cache_path = None
    else:
        cache_path = Path(str(raw_cache))

    return SWEBenchRunConfig(
        workers=resolved_workers,
        reuse_images=bool(merged["reuse_images"]),
        allow_build=bool(merged["allow_build"]),
        cache_dir=cache_path,
        timeout_seconds=resolved_timeout,
    )


def resolve_swebench_run_config_from_args(args: Any) -> SWEBenchRunConfig:
    """Build ``SWEBenchRunConfig`` from an argparse namespace."""
    return resolve_swebench_run_config(
        config_path=getattr(args, "config", None),
        workers=getattr(args, "workers", None),
        reuse_images=getattr(args, "reuse_images", None),
        no_build=bool(getattr(args, "no_build", False)),
        cache_dir=getattr(args, "cache_dir", None),
        timeout_seconds=getattr(args, "timeout_seconds", None),
    )


def effective_cache_dir(config: SWEBenchRunConfig, output_dir: Path) -> Path:
    """Return the directory used for persistent harness build artifacts."""
    if config.cache_dir is not None:
        return config.cache_dir
    return output_dir / ".swebench_cache"


def prepare_swebench_workdir(output_dir: Path, config: SWEBenchRunConfig) -> Path:
    """Create work cwd and link harness ``logs/`` into the cache directory."""
    work_cwd = output_dir / ".swebench_work"
    work_cwd.mkdir(parents=True, exist_ok=True)

    cache_root = effective_cache_dir(config, output_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_logs = cache_root / "logs"
    cache_logs.mkdir(parents=True, exist_ok=True)

    work_logs = work_cwd / "logs"
    if work_logs.is_symlink():
        if work_logs.resolve() != cache_logs.resolve():
            work_logs.unlink()
            work_logs.symlink_to(cache_logs, target_is_directory=True)
    elif work_logs.exists():
        if work_logs.is_dir() and not any(work_logs.iterdir()):
            work_logs.rmdir()
            work_logs.symlink_to(cache_logs, target_is_directory=True)
    else:
        work_logs.symlink_to(cache_logs, target_is_directory=True)

    return work_cwd


def describe_image_cache_status(
    config: SWEBenchRunConfig,
    output_dir: Path,
) -> dict[str, Any]:
    """Summarize cache directory presence for operator logs."""
    cache_path = effective_cache_dir(config, output_dir)
    logs_path = cache_path / "logs"
    build_images_path = logs_path / "build_images"
    return {
        "cache_dir": str(cache_path),
        "cache_dir_exists": cache_path.is_dir(),
        "logs_dir_exists": logs_path.is_dir(),
        "build_images_dir_exists": build_images_path.is_dir(),
        "reuse_images": config.reuse_images,
        "allow_build": config.allow_build,
        "force_rebuild": config.force_rebuild,
    }


def print_swebench_execution_summary(
    *,
    command: str,
    config: SWEBenchRunConfig,
    output_dir: Path,
    instance_count: int = 1,
) -> None:
    """Print effective parallelism and cache settings before execution."""
    cache_status = describe_image_cache_status(config, output_dir)
    if instance_count <= 1:
        harness_workers = config.effective_harness_build_workers
        instance_workers = 1
        parallelism_note = (
            f"requested_workers={config.workers}, "
            f"effective_instance_workers={instance_workers} "
            f"(single-instance; batch reserve={config.effective_instance_workers}), "
            f"harness_build_workers={harness_workers}"
        )
    else:
        instance_workers = min(config.effective_instance_workers, instance_count)
        harness_workers = config.effective_harness_build_workers
        parallelism_note = (
            f"requested_workers={config.workers}, "
            f"effective_instance_workers={instance_workers}, "
            f"harness_build_workers={harness_workers}"
        )

    print(f"earnbench swebench {command}: {parallelism_note}", file=sys.stderr)
    print(
        "image cache: "
        f"dir={cache_status['cache_dir']} "
        f"exists={cache_status['cache_dir_exists']} "
        f"build_images={cache_status['build_images_dir_exists']} "
        f"reuse_images={cache_status['reuse_images']} "
        f"allow_build={cache_status['allow_build']} "
        f"force_rebuild={cache_status['force_rebuild']}",
        file=sys.stderr,
    )
    print(f"timeout_seconds={config.timeout_seconds}", file=sys.stderr)


def add_swebench_performance_arguments(parser: argparse.ArgumentParser) -> None:
    """Register shared performance flags on a subcommand parser."""
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional JSON file with SWE-bench defaults (timeout, workers, cache)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Requested worker count for batch instance parallelism "
            f"(default from config: {DEFAULT_WORKERS})"
        ),
    )
    parser.add_argument(
        "--reuse-images",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Reuse existing Docker images instead of forcing rebuild (default: true)",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Never build Docker images; fail or skip when images are missing",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Persistent directory for harness build logs and cached artifacts",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help=(
            "Harness per-instance test timeout "
            f"(default from config: {DEFAULT_TIMEOUT_SECONDS})"
        ),
    )


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_WORKERS",
    "SWEBenchRunConfig",
    "add_swebench_performance_arguments",
    "describe_image_cache_status",
    "effective_cache_dir",
    "load_swebench_config_file",
    "prepare_swebench_workdir",
    "print_swebench_execution_summary",
    "resolve_swebench_run_config",
    "resolve_swebench_run_config_from_args",
]
