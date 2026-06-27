"""Performance and cache configuration for SWE-bench CLI commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 1800
MAX_WORKER_CAP = 12


def default_workers(*, cpu_count: int | None = None) -> int:
    """Return the default worker count: ``min(cpu_count(), 12)``."""
    cores = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    return max(1, min(cores, MAX_WORKER_CAP))


DEFAULT_WORKERS = default_workers()


@dataclass(frozen=True, slots=True)
class SWEBenchRunConfig:
    """Resolved execution settings for SWE-bench preflight and grading."""

    workers: int
    max_parallel_containers: int
    max_parallel_builds: int
    reuse_images: bool
    allow_build: bool
    cache_dir: Path | None
    timeout_seconds: int

    @property
    def force_rebuild(self) -> bool:
        return not self.reuse_images

    def effective_instance_workers(self, instance_count: int = 1) -> int:
        """Parallel SWE-bench instance grading (batch mode)."""
        if instance_count <= 1:
            return 1
        return max(
            1,
            min(self.workers, self.max_parallel_containers, instance_count),
        )

    def effective_harness_build_workers(self, build_jobs: int = 1) -> int:
        """Parallel Docker image builds via the SWE-bench harness."""
        if build_jobs <= 1:
            return 1
        return max(1, min(self.max_parallel_builds, build_jobs))

    def effective_image_inspect_workers(self, image_count: int) -> int:
        """Parallel ``docker image inspect`` calls (I/O-bound)."""
        if image_count <= 1:
            return 1
        return max(
            1,
            min(self.workers, self.max_parallel_containers, image_count),
        )

    def parallelism_summary(self, *, instance_count: int = 1) -> dict[str, int]:
        """Return effective parallelism for operator logs."""
        image_jobs = 3 if instance_count >= 1 else 1
        return {
            "workers": self.workers,
            "max_parallel_containers": self.max_parallel_containers,
            "max_parallel_builds": self.max_parallel_builds,
            "effective_instance_workers": self.effective_instance_workers(
                instance_count
            ),
            "effective_container_workers": self.effective_instance_workers(
                instance_count
            ),
            "effective_build_workers": self.effective_harness_build_workers(
                build_jobs=self.max_parallel_builds,
            ),
            "effective_image_inspect_workers": self.effective_image_inspect_workers(
                image_jobs,
            ),
        }


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


def _positive_int(name: str, value: int) -> int:
    if value < 1:
        msg = f"{name} must be >= 1, got {value}"
        raise ValueError(msg)
    return value


def resolve_swebench_run_config(
    *,
    config_path: Path | None = None,
    workers: int | None = None,
    max_parallel_containers: int | None = None,
    max_parallel_builds: int | None = None,
    reuse_images: bool | None = None,
    no_build: bool = False,
    cache_dir: Path | None = None,
    timeout_seconds: int | None = None,
    cpu_count: int | None = None,
) -> SWEBenchRunConfig:
    """Merge file defaults with CLI flags into one run configuration."""
    file_data = load_swebench_config_file(config_path)
    baseline_workers = default_workers(cpu_count=cpu_count)
    merged: dict[str, Any] = {
        "workers": baseline_workers,
        "max_parallel_containers": baseline_workers,
        "max_parallel_builds": baseline_workers,
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
    if max_parallel_containers is not None:
        merged["max_parallel_containers"] = max_parallel_containers
    if max_parallel_builds is not None:
        merged["max_parallel_builds"] = max_parallel_builds
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

    if max_parallel_containers is None and "max_parallel_containers" not in file_data:
        merged["max_parallel_containers"] = merged["workers"]
    if max_parallel_builds is None and "max_parallel_builds" not in file_data:
        merged["max_parallel_builds"] = merged["workers"]

    resolved_workers = _positive_int("--workers", int(merged["workers"]))
    resolved_containers = _positive_int(
        "--max-parallel-containers",
        int(merged["max_parallel_containers"]),
    )
    resolved_builds = _positive_int(
        "--max-parallel-builds",
        int(merged["max_parallel_builds"]),
    )
    resolved_timeout = _positive_int(
        "--timeout-seconds",
        int(merged["timeout_seconds"]),
    )

    cache_path: Path | None
    raw_cache = merged.get("cache_dir")
    if raw_cache in (None, ""):
        cache_path = None
    else:
        cache_path = Path(str(raw_cache))

    return SWEBenchRunConfig(
        workers=resolved_workers,
        max_parallel_containers=resolved_containers,
        max_parallel_builds=resolved_builds,
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
        max_parallel_containers=getattr(args, "max_parallel_containers", None),
        max_parallel_builds=getattr(args, "max_parallel_builds", None),
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
    parallel = config.parallelism_summary(instance_count=instance_count)
    print(
        f"earnbench swebench {command}: "
        f"workers={parallel['workers']} "
        f"max_parallel_containers={parallel['max_parallel_containers']} "
        f"max_parallel_builds={parallel['max_parallel_builds']} "
        f"effective_instance_workers={parallel['effective_instance_workers']} "
        f"effective_build_workers={parallel['effective_build_workers']} "
        f"effective_image_inspect_workers={parallel['effective_image_inspect_workers']}",
        file=sys.stderr,
    )
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
    default_workers_help = (
        f"default: min(cpu_count(), {MAX_WORKER_CAP}) = {DEFAULT_WORKERS}"
    )
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
            "Top-level worker budget for instance/batch orchestration; "
            + default_workers_help
        ),
    )
    parser.add_argument(
        "--max-parallel-containers",
        type=int,
        default=None,
        help=(
            "Cap on concurrent SWE-bench Docker grading containers; "
            + default_workers_help
        ),
    )
    parser.add_argument(
        "--max-parallel-builds",
        type=int,
        default=None,
        help=(
            "Cap on concurrent SWE-bench harness image builds; " + default_workers_help
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
    "MAX_WORKER_CAP",
    "SWEBenchRunConfig",
    "add_swebench_performance_arguments",
    "default_workers",
    "describe_image_cache_status",
    "effective_cache_dir",
    "load_swebench_config_file",
    "prepare_swebench_workdir",
    "print_swebench_execution_summary",
    "resolve_swebench_run_config",
    "resolve_swebench_run_config_from_args",
]
