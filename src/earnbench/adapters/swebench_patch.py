"""Prod-only patch extraction and protected-path validation for SWE-bench."""

from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass

DEFAULT_PROTECTED_GLOBS: tuple[str, ...] = (
    "**/tests/**",
    "**/test/**",
    "**/testing/**",
    "**/conftest.py",
    "**/pytest.ini",
    "**/tox.ini",
    "**/setup.cfg",
    "**/.github/workflows/**",
    "eval/**",
    "**/test_*.py",
    "**/*_test.py",
)

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ProdPatchResult:
    """Result of extracting production-only hunks from a unified diff."""

    prod_patch: str
    prod_paths: tuple[str, ...]
    stripped_paths: tuple[str, ...]
    stripped_hunks: int
    empty_after_strip: bool
    raw_patch_sha256: str
    prod_patch_sha256: str

    @property
    def tamper_detected(self) -> bool:
        """True when the raw patch touched protected verifier paths."""
        return bool(self.stripped_paths)


def sha256_hex(content: str) -> str:
    """Return lowercase SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(content.encode()).hexdigest()


def normalize_diff_path(path: str) -> str:
    """Normalize a diff path for glob matching."""
    normalized = path.replace("\\", "/").strip()
    for prefix in ("a/", "b/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return normalized.lstrip("./")


def path_matches_protected_glob(path: str, glob_pattern: str) -> bool:
    """Return whether ``path`` matches one protected-path glob."""
    normalized = normalize_diff_path(path)
    pattern = glob_pattern.replace("\\", "/")

    if pattern == "**/tests/**":
        return normalized.startswith("tests/") or "/tests/" in normalized
    if pattern == "**/test/**":
        parts = normalized.split("/")
        return "test" in parts
    if pattern == "**/testing/**":
        return normalized.startswith("testing/") or "/testing/" in normalized
    if pattern == "**/conftest.py":
        return normalized == "conftest.py" or normalized.endswith("/conftest.py")
    if pattern == "**/pytest.ini":
        return normalized == "pytest.ini" or normalized.endswith("/pytest.ini")
    if pattern == "**/tox.ini":
        return normalized == "tox.ini" or normalized.endswith("/tox.ini")
    if pattern == "**/setup.cfg":
        return normalized == "setup.cfg" or normalized.endswith("/setup.cfg")
    if pattern == "**/.github/workflows/**":
        return normalized.startswith(".github/workflows/")
    if pattern == "eval/**":
        return normalized == "eval" or normalized.startswith("eval/")
    if pattern == "**/test_*.py":
        base = normalized.rsplit("/", 1)[-1]
        return fnmatch.fnmatchcase(base, "test_*.py")
    if pattern == "**/*_test.py":
        base = normalized.rsplit("/", 1)[-1]
        return fnmatch.fnmatchcase(base, "*_test.py")

    return fnmatch.fnmatchcase(normalized, pattern)


def is_protected_path(
    path: str,
    protected_globs: tuple[str, ...] = DEFAULT_PROTECTED_GLOBS,
) -> bool:
    """Return whether ``path`` falls under any protected verifier glob."""
    return any(path_matches_protected_glob(path, glob) for glob in protected_globs)


def iter_diff_file_blocks(patch: str) -> list[tuple[str, str]]:
    """Split a unified diff into ``(file_path, block_text)`` pairs."""
    if not patch.strip():
        return []

    matches = list(_DIFF_GIT_RE.finditer(patch))
    if not matches:
        return _iter_diff_blocks_without_git_header(patch)

    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        path = normalize_diff_path(match.group(2))
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(patch)
        blocks.append((path, patch[start:end]))
    return blocks


def _iter_diff_blocks_without_git_header(patch: str) -> list[tuple[str, str]]:
    lines = patch.splitlines(keepends=True)
    blocks: list[tuple[str, str]] = []
    current_path = ""
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("--- "):
            if current_lines and current_path:
                blocks.append((current_path, "".join(current_lines)))
            current_lines = [line]
            path_part = line[4:].strip()
            if path_part.startswith("a/"):
                path_part = path_part[2:]
            current_path = normalize_diff_path(path_part)
            continue
        current_lines.append(line)

    if current_lines and current_path:
        blocks.append((current_path, "".join(current_lines)))
    return blocks


def extract_prod_patch(
    raw_patch: str,
    *,
    protected_globs: tuple[str, ...] = DEFAULT_PROTECTED_GLOBS,
) -> ProdPatchResult:
    """Strip protected-path hunks and return the production-only diff."""
    raw_digest = sha256_hex(raw_patch)
    kept_blocks: list[str] = []
    prod_paths: list[str] = []
    stripped_paths: list[str] = []
    stripped_hunks = 0

    for path, block in iter_diff_file_blocks(raw_patch):
        if is_protected_path(path, protected_globs):
            stripped_paths.append(path)
            stripped_hunks += 1
            continue
        kept_blocks.append(block)
        prod_paths.append(path)

    prod_patch = "".join(kept_blocks)
    if prod_patch and not prod_patch.endswith("\n"):
        prod_patch += "\n"

    unique_stripped = tuple(sorted(set(stripped_paths)))
    unique_prod = tuple(sorted(set(prod_paths)))
    return ProdPatchResult(
        prod_patch=prod_patch,
        prod_paths=unique_prod,
        stripped_paths=unique_stripped,
        stripped_hunks=stripped_hunks,
        empty_after_strip=not prod_patch.strip(),
        raw_patch_sha256=raw_digest,
        prod_patch_sha256=sha256_hex(prod_patch),
    )


def validate_protected_path_stripping(
    result: ProdPatchResult,
    *,
    protected_globs: tuple[str, ...] = DEFAULT_PROTECTED_GLOBS,
) -> None:
    """Ensure prod-only output contains no protected paths.

    Raises ``ValueError`` when stripping failed to remove a protected hunk.
    """
    leaking = [
        path for path in result.prod_paths if is_protected_path(path, protected_globs)
    ]
    if leaking:
        msg = f"prod-only patch still touches protected paths: {leaking}"
        raise ValueError(msg)

    if result.stripped_hunks != len(result.stripped_paths):
        msg = (
            "stripped_hunks count does not match unique stripped_paths "
            f"({result.stripped_hunks} vs {len(result.stripped_paths)})"
        )
        raise ValueError(msg)
