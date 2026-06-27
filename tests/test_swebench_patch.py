"""Tests for prod-only patch extraction and protected-path detection."""

from __future__ import annotations

import pytest

from earnbench.adapters.swebench_patch import (
    DEFAULT_PROTECTED_GLOBS,
    ProdPatchResult,
    extract_prod_patch,
    is_protected_path,
    normalize_diff_path,
    validate_protected_path_stripping,
)

PROD_AND_TEST_PATCH = """\
diff --git a/requests/models.py b/requests/models.py
--- a/requests/models.py
+++ b/requests/models.py
@@ -1 +1,2 @@
+prod fix
diff --git a/tests/test_models.py b/tests/test_models.py
--- a/tests/test_models.py
+++ b/tests/test_models.py
@@ -1 +1,2 @@
+test tamper
"""

PROD_ONLY_PATCH = """\
diff --git a/requests/models.py b/requests/models.py
--- a/requests/models.py
+++ b/requests/models.py
@@ -1 +1,2 @@
+prod fix
"""


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_models.py", True),
        ("requests/models.py", False),
        ("src/conftest.py", True),
        ("eval/run.py", True),
        ("pkg/test_foo.py", True),
        ("pkg/foo_test.py", True),
        ("a/tests/unit/test_x.py", True),
    ],
)
def test_is_protected_path(path: str, expected: bool) -> None:
    assert is_protected_path(path, DEFAULT_PROTECTED_GLOBS) is expected


def test_normalize_diff_path_strips_prefixes() -> None:
    assert normalize_diff_path("a/foo.py") == "foo.py"
    assert normalize_diff_path("b/tests/x.py") == "tests/x.py"


def test_extract_prod_patch_strips_test_hunks() -> None:
    result = extract_prod_patch(PROD_AND_TEST_PATCH)
    validate_protected_path_stripping(result)

    assert result.prod_paths == ("requests/models.py",)
    assert result.stripped_paths == ("tests/test_models.py",)
    assert result.stripped_hunks == 1
    assert result.tamper_detected is True
    assert "requests/models.py" in result.prod_patch
    assert "tests/test_models.py" not in result.prod_patch
    assert result.empty_after_strip is False
    assert len(result.raw_patch_sha256) == 64
    assert len(result.prod_patch_sha256) == 64


def test_extract_prod_patch_prod_only_input() -> None:
    result = extract_prod_patch(PROD_ONLY_PATCH)
    validate_protected_path_stripping(result)

    assert result.stripped_paths == ()
    assert result.tamper_detected is False
    assert result.prod_paths == ("requests/models.py",)


def test_extract_prod_patch_all_test_hunks_is_empty() -> None:
    test_only = """\
diff --git a/tests/test_models.py b/tests/test_models.py
--- a/tests/test_models.py
+++ b/tests/test_models.py
@@ -1 +1,2 @@
+only test
"""
    result = extract_prod_patch(test_only)
    validate_protected_path_stripping(result)

    assert result.empty_after_strip is True
    assert result.prod_paths == ()
    assert result.stripped_paths == ("tests/test_models.py",)


def test_validate_protected_path_stripping_rejects_leaks() -> None:
    result = extract_prod_patch(PROD_ONLY_PATCH)
    leaking = ProdPatchResult(
        prod_patch=result.prod_patch,
        prod_paths=("tests/leak.py",),
        stripped_paths=result.stripped_paths,
        stripped_hunks=result.stripped_hunks,
        empty_after_strip=result.empty_after_strip,
        raw_patch_sha256=result.raw_patch_sha256,
        prod_patch_sha256=result.prod_patch_sha256,
    )
    with pytest.raises(ValueError, match="protected paths"):
        validate_protected_path_stripping(leaking)
