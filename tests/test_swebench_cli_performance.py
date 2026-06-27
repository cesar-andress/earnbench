"""CLI parsing tests for SWE-bench performance flags."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earnbench.adapters.swebench_config import (
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_WORKERS,
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
        reuse_images=None,
        no_build=False,
        cache_dir=None,
        timeout_seconds=None,
    )

    assert config.workers == DEFAULT_WORKERS
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert config.reuse_images is True
    assert config.allow_build is True


def test_resolve_config_rejects_invalid_workers() -> None:
    with pytest.raises(ValueError, match="workers"):
        resolve_swebench_run_config(
            config_path=None,
            workers=0,
            reuse_images=None,
            no_build=False,
            cache_dir=None,
            timeout_seconds=None,
        )
