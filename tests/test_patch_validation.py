"""Tests for Phase C unified diff validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from earnbench.agents.base import AgentRunContext
from earnbench.agents.ollama import OllamaAdapter
from earnbench.agents.schemas import AgentArmSpec
from earnbench.agents.patch_validation import (
    REASON_DUPLICATED_HEADERS,
    REASON_EMPTY_PATCH,
    REASON_INVALID_FILENAME,
    REASON_MALFORMED_PATCH,
    PatchValidationResult,
    validate_unified_diff,
)

VALID_PATCH = (
    "diff --git a/requests/models.py b/requests/models.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/requests/models.py\n"
    "+++ b/requests/models.py\n"
    "@@ -1,3 +1,4 @@\n"
    " # header\n"
    "+fixed prod change\n"
)

VALID_PATCH_WITH_TESTS = (
    "diff --git a/requests/models.py b/requests/models.py\n"
    "--- a/requests/models.py\n"
    "+++ b/requests/models.py\n"
    "@@ -1,2 +1,3 @@\n"
    "+prod\n"
    "diff --git a/tests/test_models.py b/tests/test_models.py\n"
    "--- a/tests/test_models.py\n"
    "+++ b/tests/test_models.py\n"
    "@@ -1,2 +1,3 @@\n"
    "+test\n"
)


def test_validate_accepts_well_formed_patch() -> None:
    result = validate_unified_diff(VALID_PATCH)
    assert result == PatchValidationResult.ok()


def test_validate_accepts_patch_with_test_paths() -> None:
    result = validate_unified_diff(VALID_PATCH_WITH_TESTS)
    assert result.valid


def test_validate_rejects_empty_patch() -> None:
    result = validate_unified_diff("")
    assert not result.valid
    assert result.reason == REASON_EMPTY_PATCH


def test_validate_rejects_context_only_patch() -> None:
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        " unchanged\n"
        " still unchanged\n"
    )
    result = validate_unified_diff(patch)
    assert not result.valid
    assert result.reason == REASON_EMPTY_PATCH


def test_validate_rejects_malformed_missing_hunk() -> None:
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
    )
    result = validate_unified_diff(patch)
    assert not result.valid
    assert result.reason == REASON_MALFORMED_PATCH


def test_validate_rejects_malformed_bad_hunk_header() -> None:
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ not-a-hunk @@\n"
        "+change\n"
    )
    result = validate_unified_diff(patch)
    assert not result.valid
    assert result.reason == REASON_MALFORMED_PATCH


def test_validate_rejects_duplicated_file_headers() -> None:
    patch = VALID_PATCH + VALID_PATCH
    result = validate_unified_diff(patch)
    assert not result.valid
    assert result.reason == REASON_DUPLICATED_HEADERS


def test_validate_rejects_duplicate_minus_header_in_block() -> None:
    patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,2 @@\n"
        "+x\n"
    )
    result = validate_unified_diff(patch)
    assert not result.valid
    assert result.reason == REASON_DUPLICATED_HEADERS


def test_validate_rejects_invalid_filename_path_traversal() -> None:
    patch = (
        "diff --git a/../evil.py b/../evil.py\n"
        "--- a/../evil.py\n"
        "+++ b/../evil.py\n"
        "@@ -1,1 +1,2 @@\n"
        "+x\n"
    )
    result = validate_unified_diff(patch)
    assert not result.valid
    assert result.reason == REASON_INVALID_FILENAME


def test_validate_rejects_invalid_absolute_filename() -> None:
    patch = (
        "--- /etc/passwd\n"
        "+++ /etc/passwd\n"
        "@@ -1,1 +1,2 @@\n"
        "+x\n"
    )
    result = validate_unified_diff(patch)
    assert not result.valid
    assert result.reason == REASON_INVALID_FILENAME


def test_collect_attempt_marks_invalid_patch(tmp_path: Path) -> None:
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
        trajectory_path=output_dir / "trajectories/x.json",
    )
    adapter = OllamaAdapter(base_url="http://127.0.0.1:11434")

    bad_patch = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
    )

    from unittest.mock import patch

    from earnbench.agents.base import AgentRunResult

    with patch.object(
        adapter,
        "run",
        return_value=AgentRunResult(
            trajectory_text="{}",
            patch_text=bad_patch,
            status="ok",
        ),
    ):
        record = adapter.collect_attempt(context)

    assert record.status == "invalid_patch"
    assert record.patch_path
    assert REASON_MALFORMED_PATCH in record.error
