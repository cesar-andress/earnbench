"""Tests for Phase C optional unified diff repair."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from earnbench.agents.base import AgentRunContext, AgentRunResult
from earnbench.agents.ollama import OllamaAdapter
from earnbench.agents.patch_repair import (
    REPAIR_DUPLICATED_HEADERS,
    REPAIR_HUNK_OFFSETS,
    REPAIR_MALFORMED_HEADERS,
    REPAIR_TRAILING_NEWLINE,
    PatchRepairResult,
    maybe_repair_unified_diff,
    repair_unified_diff,
)
from earnbench.agents.patch_validation import validate_unified_diff
from earnbench.agents.schemas import AgentArmSpec

VALID_PATCH = (
    "diff --git a/requests/models.py b/requests/models.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/requests/models.py\n"
    "+++ b/requests/models.py\n"
    "@@ -1,3 +1,4 @@\n"
    " # header\n"
    "+fixed prod change\n"
)


def test_repair_trailing_newline() -> None:
    patch = VALID_PATCH.rstrip("\n")
    result = repair_unified_diff(patch)
    assert result.applied
    assert REPAIR_TRAILING_NEWLINE in result.repairs
    assert result.patch.endswith("\n")
    assert validate_unified_diff(result.patch).valid


def test_repair_malformed_headers_adds_prefix_and_git_header() -> None:
    patch = (
        "--- requests/models.py\n"
        "+++ requests/models.py\n"
        "@@ -1,1 +1,2 @@\n"
        "+change\n"
    )
    result = repair_unified_diff(patch)
    assert result.applied
    assert REPAIR_MALFORMED_HEADERS in result.repairs
    assert result.patch.startswith("diff --git a/requests/models.py b/requests/models.py\n")
    assert "--- a/requests/models.py\n" in result.patch
    assert "+++ b/requests/models.py\n" in result.patch
    assert validate_unified_diff(result.patch).valid


def test_repair_deduplicates_minus_plus_headers() -> None:
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,2 @@\n"
        "+x\n"
    )
    result = repair_unified_diff(patch)
    assert result.applied
    assert REPAIR_DUPLICATED_HEADERS in result.repairs
    assert validate_unified_diff(result.patch).valid


def test_repair_hunk_offsets_from_body() -> None:
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,99 +1,99 @@\n"
        " line1\n"
        " line2\n"
        "+added\n"
    )
    result = repair_unified_diff(patch)
    assert result.applied
    assert REPAIR_HUNK_OFFSETS in result.repairs
    assert "@@ -1,2 +1,3 @@" in result.patch
    assert validate_unified_diff(result.patch).valid
    assert "+added" in result.patch
    assert " line1" in result.patch
    assert " line2" in result.patch


def test_repair_preserves_hunk_semantics() -> None:
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,2 @@\n"
        "+x\n"
    )
    result = repair_unified_diff(patch.rstrip("\n"))
    assert "+x" in result.patch
    assert result.patch.count("+x") == 1


def test_repair_does_not_fix_invalid_filenames() -> None:
    patch = (
        "diff --git a/../evil.py b/../evil.py\n"
        "--- a/../evil.py\n"
        "+++ b/../evil.py\n"
        "@@ -1,1 +1,2 @@\n"
        "+x\n"
    )
    result = repair_unified_diff(patch)
    assert not result.applied


def test_maybe_repair_skips_when_disabled() -> None:
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
    )
    result = maybe_repair_unified_diff(patch, enabled=False)
    assert not result.applied
    assert result.patch == patch


def test_maybe_repair_leaves_valid_patch_untouched() -> None:
    result = maybe_repair_unified_diff(VALID_PATCH, enabled=True)
    assert not result.applied
    assert result.patch == VALID_PATCH


def test_collect_attempt_applies_repair_when_enabled(tmp_path: Path) -> None:
    arm = AgentArmSpec(id="ollama_devstral", provider="ollama", model="devstral")
    output_dir = tmp_path / "phase_c"
    context = AgentRunContext(
        output_dir=output_dir,
        arm=arm,
        instance_id="psf__requests-1724",
        replicate=0,
        seed=1,
        prompt="prompt",
        prompt_path=output_dir / "prompts/x.txt",
        patch_path=output_dir / "patches/x.patch",
        trajectory_path=output_dir / "trajectories/x.log",
        repair_patch=True,
    )
    adapter = OllamaAdapter(base_url="http://127.0.0.1:11434")

    broken_patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,9 +1,9 @@\n"
        "+x\n"
    )

    with patch.object(
        adapter,
        "run",
        return_value=AgentRunResult(
            trajectory_text="{}",
            patch_text=broken_patch,
            status="ok",
        ),
    ):
        record = adapter.collect_attempt(context)

    assert record.status == "ok"
    assert record.repair_applied is True
    assert record.original_patch.endswith(".original.patch")
    assert record.repaired_patch == record.patch_path
    assert validate_unified_diff(context.patch_path.read_text(encoding="utf-8")).valid
    original_path = output_dir / record.original_patch
    assert original_path.is_file()
    assert broken_patch in original_path.read_text(encoding="utf-8")


def test_collect_attempt_stays_invalid_without_repair(tmp_path: Path) -> None:
    arm = AgentArmSpec(id="ollama_devstral", provider="ollama", model="devstral")
    output_dir = tmp_path / "phase_c"
    context = AgentRunContext(
        output_dir=output_dir,
        arm=arm,
        instance_id="psf__requests-1724",
        replicate=0,
        seed=1,
        prompt="prompt",
        prompt_path=output_dir / "prompts/x.txt",
        patch_path=output_dir / "patches/x.patch",
        trajectory_path=output_dir / "trajectories/x.log",
        repair_patch=False,
    )
    adapter = OllamaAdapter(base_url="http://127.0.0.1:11434")

    broken_patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,9 +1,9 @@\n"
        "+x\n"
    )

    with patch.object(
        adapter,
        "run",
        return_value=AgentRunResult(
            trajectory_text="{}",
            patch_text=broken_patch,
            status="ok",
        ),
    ):
        record = adapter.collect_attempt(context)

    assert record.status == "invalid_patch"
    assert record.repair_applied is False
    assert record.original_patch == ""
    assert record.repaired_patch == ""
