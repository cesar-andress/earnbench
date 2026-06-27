"""Load SWE-bench Verified instance metadata for adapter preparation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.adapters.base import BenchmarkInstance

DATASET_NAME = "SWE-bench_Verified"


class MetadataLoadError(LookupError):
    """Raised when metadata cannot be loaded or an instance is missing."""


@dataclass(frozen=True, slots=True)
class SWEBenchVerifiedRecord:
    """One SWE-bench Verified instance row with golden patch."""

    instance_id: str
    repo: str
    base_commit: str
    golden_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...] = ()
    test_patch: str = ""
    dataset_name: str = DATASET_NAME

    def __post_init__(self) -> None:
        if not self.instance_id:
            msg = "instance_id must be non-empty"
            raise ValueError(msg)
        if not self.repo:
            msg = "repo must be non-empty"
            raise ValueError(msg)
        if not self.base_commit:
            msg = "base_commit must be non-empty"
            raise ValueError(msg)
        if not self.golden_patch.strip():
            msg = "golden_patch must be non-empty"
            raise ValueError(msg)
        if not self.fail_to_pass:
            msg = "fail_to_pass must contain at least one test id"
            raise ValueError(msg)

    def to_benchmark_instance(self) -> BenchmarkInstance:
        """Convert to the shared adapter ``BenchmarkInstance`` type."""
        metadata = {
            "test_patch_present": str(bool(self.test_patch.strip())).lower(),
        }
        return BenchmarkInstance(
            instance_id=self.instance_id,
            repo=self.repo,
            base_commit=self.base_commit,
            fail_to_pass=self.fail_to_pass,
            pass_to_pass=self.pass_to_pass,
            dataset_name=self.dataset_name,
            metadata=metadata,
        )


def _parse_test_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        items = json.loads(stripped)
    else:
        msg = f"unsupported test list value type: {type(value)!r}"
        raise MetadataLoadError(msg)
    return tuple(str(item) for item in items)


def _row_to_record(row: dict[str, Any]) -> SWEBenchVerifiedRecord:
    try:
        return SWEBenchVerifiedRecord(
            instance_id=str(row["instance_id"]),
            repo=str(row["repo"]),
            base_commit=str(row["base_commit"]),
            golden_patch=str(row["patch"]),
            fail_to_pass=_parse_test_list(row["FAIL_TO_PASS"]),
            pass_to_pass=_parse_test_list(row.get("PASS_TO_PASS")),
            test_patch=str(row.get("test_patch") or ""),
        )
    except KeyError as exc:
        msg = f"metadata row missing required field: {exc.args[0]}"
        raise MetadataLoadError(msg) from exc
    except ValueError as exc:
        raise MetadataLoadError(str(exc)) from exc


def _load_rows_from_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and "instances" in payload:
        rows = payload["instances"]
    else:
        msg = f"unsupported JSON metadata shape in {path}"
        raise MetadataLoadError(msg)
    if not isinstance(rows, list):
        msg = f"metadata instances must be a list in {path}"
        raise MetadataLoadError(msg)
    return [row for row in rows if isinstance(row, dict)]


def _load_rows_from_parquet(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        msg = (
            "reading parquet metadata requires pyarrow; "
            "install with: pip install pyarrow"
        )
        raise MetadataLoadError(msg) from exc

    table = pq.read_table(path)
    columns = table.to_pydict()
    instance_ids = columns.get("instance_id")
    if not instance_ids:
        msg = f"parquet file missing instance_id column: {path}"
        raise MetadataLoadError(msg)

    row_count = len(instance_ids)
    rows: list[dict[str, Any]] = []
    for index in range(row_count):
        row = {key: values[index] for key, values in columns.items()}
        rows.append(row)
    return rows


def load_metadata_rows(path: Path) -> list[dict[str, Any]]:
    """Load metadata rows from parquet or JSON (JSON for tests/fixtures)."""
    if not path.is_file():
        msg = f"metadata file not found: {path}"
        raise MetadataLoadError(msg)

    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return _load_rows_from_parquet(path)
    if suffix == ".json":
        return _load_rows_from_json(path)
    msg = f"unsupported metadata format (expected .parquet or .json): {path}"
    raise MetadataLoadError(msg)


def load_verified_instance(path: Path, instance_id: str) -> SWEBenchVerifiedRecord:
    """Load one Verified instance by id from a metadata export file."""
    if not instance_id.strip():
        msg = "instance_id must be non-empty"
        raise MetadataLoadError(msg)

    for row in load_metadata_rows(path):
        if str(row.get("instance_id")) == instance_id:
            return _row_to_record(row)

    msg = f"instance_id not found in metadata: {instance_id!r}"
    raise MetadataLoadError(msg)
