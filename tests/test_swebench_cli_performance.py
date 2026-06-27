"""CLI parsing tests for SWE-bench performance flags."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.adapters.swebench_config import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_WORKER_CAP,
    default_workers,
    resolve_swebench_run_config,
    resolve_swebench_run_config_from_args,
)
from earnbench.cli import build_parser

FIXTURES = Path(__file__).parent / "fixtures"
METADATA = FIXTURES / "swebench_smoke_metadata.json"


def _preflight_argv(*extra: str) -> list[str]:
    base = [
        "swebench",
        "preflight",
        "--metadata-parquet",
        str(METADATA),
        "--instance-id",
        "psf__requests-1724",
        "--output",
        "/tmp/out",
    ]
    return base + list(extra)


def _run_nominal_argv(*extra: str) -> list[str]:
    base = [
        "swebench",
        "run-nominal",
        "--metadata-parquet",
        str(METADATA),
        "--instance-id",
        "psf__requests-1724",
        "--patch",
        "/tmp/golden.patch",
        "--output",
        "/tmp/out",
    ]
    return base + list(extra)


def _run_pi_verif_argv(*extra: str) -> list[str]:
    base = [
        "swebench",
        "run-pi-verif",
        "--metadata-parquet",
        str(METADATA),
        "--instance-id",
        "psf__requests-1724",
        "--patch",
        "/tmp/prod_only.patch",
        "--output",
        "/tmp/out",
    ]
    return base + list(extra)


def test_preflight_parses_parallelism_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        _preflight_argv(
            "--workers",
            "8",
            "--max-parallel-containers",
            "6",
            "--max-parallel-builds",
            "4",
        )
    )

    assert args.workers == 8
    assert args.max_parallel_containers == 6
    assert args.max_parallel_builds == 4


def test_preflight_parses_performance_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        _preflight_argv(
            "--workers",
            "8",
            "--no-build",
            "--cache-dir",
            "/tmp/swebench-cache",
            "--timeout-seconds",
            "900",
            "--no-reuse-images",
        )
    )

    assert args.swebench_command == "preflight"
    assert args.workers == 8
    assert args.no_build is True
    assert args.cache_dir == Path("/tmp/swebench-cache")
    assert args.timeout_seconds == 900
    assert args.reuse_images is False


def test_run_nominal_parses_performance_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        _run_nominal_argv(
            "--workers",
            "12",
            "--reuse-images",
            "--cache-dir",
            "/data/swebench",
        )
    )

    assert args.swebench_command == "run-nominal"
    assert args.workers == 12
    assert args.reuse_images is True
    assert args.no_build is False
    assert args.cache_dir == Path("/data/swebench")
    assert args.timeout_seconds is None


def test_run_pi_verif_parses_performance_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        _run_pi_verif_argv(
            "--workers",
            "1",
            "--timeout-seconds",
            "1800",
            "--cache-dir",
            "/tmp/cache",
        )
    )

    assert args.swebench_command == "run-pi-verif"
    assert args.workers == 1
    assert args.timeout_seconds == 1800
    assert args.cache_dir == Path("/tmp/cache")


def test_resolve_config_uses_json_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "swebench.json"
    config_path.write_text(
        json.dumps(
            {
                "timeout_seconds": 2400,
                "workers": 4,
                "reuse_images": False,
                "cache_dir": "/tmp/from-file",
            }
        ),
        encoding="utf-8",
    )

    config = resolve_swebench_run_config(
        config_path=config_path,
        workers=None,
        reuse_images=None,
        no_build=False,
        cache_dir=None,
        timeout_seconds=None,
    )

    assert config.timeout_seconds == 2400
    assert config.workers == 4
    assert config.max_parallel_containers == 4
    assert config.max_parallel_builds == 4
    assert config.reuse_images is False
    assert config.cache_dir == Path("/tmp/from-file")
    assert config.force_rebuild is True


def test_cli_overrides_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "swebench.json"
    config_path.write_text(
        json.dumps({"timeout_seconds": 2400, "workers": 4}),
        encoding="utf-8",
    )
    parser = build_parser()
    args = parser.parse_args(
        _preflight_argv(
            "--config",
            str(config_path),
            "--workers",
            "1",
            "--timeout-seconds",
            "600",
        )
    )
    config = resolve_swebench_run_config_from_args(args)

    assert config.workers == 1
    assert config.timeout_seconds == 600


def test_resolve_config_defaults_without_file() -> None:
    config = resolve_swebench_run_config(
        config_path=None,
        workers=None,
        max_parallel_containers=None,
        max_parallel_builds=None,
        reuse_images=None,
        no_build=False,
        cache_dir=None,
        timeout_seconds=None,
        cpu_count=32,
    )

    assert config.workers == MAX_WORKER_CAP
    assert config.max_parallel_containers == MAX_WORKER_CAP
    assert config.max_parallel_builds == MAX_WORKER_CAP
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert config.reuse_images is True
    assert config.allow_build is True


def test_default_workers_caps_at_twelve() -> None:
    assert default_workers(cpu_count=64) == 12
    assert default_workers(cpu_count=4) == 4
    assert default_workers(cpu_count=0) == 1


def test_effective_parallelism_for_batch() -> None:
    from earnbench.adapters.swebench_config import SWEBenchRunConfig

    config = SWEBenchRunConfig(
        workers=12,
        max_parallel_containers=8,
        max_parallel_builds=6,
        reuse_images=True,
        allow_build=True,
        cache_dir=None,
        timeout_seconds=1800,
    )
    assert config.effective_instance_workers(20) == 8
    assert config.effective_harness_build_workers(build_jobs=10) == 6
    assert config.effective_image_inspect_workers(5) == 5


def test_resolve_config_rejects_invalid_workers() -> None:
    with pytest.raises(ValueError, match="workers"):
        resolve_swebench_run_config(
            config_path=None,
            workers=0,
            max_parallel_containers=None,
            max_parallel_builds=None,
            reuse_images=None,
            no_build=False,
            cache_dir=None,
            timeout_seconds=None,
        )
