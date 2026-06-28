"""Unified diff validation for Phase C agent patch collection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from earnbench.adapters.swebench_patch import iter_diff_file_blocks

REASON_EMPTY_PATCH = "empty_patch"
REASON_MALFORMED_PATCH = "malformed_patch"
REASON_DUPLICATED_HEADERS = "duplicated_headers"
REASON_INVALID_FILENAME = "invalid_filename"

PATCH_INVALID_REASONS = (
    REASON_EMPTY_PATCH,
    REASON_MALFORMED_PATCH,
    REASON_DUPLICATED_HEADERS,
    REASON_INVALID_FILENAME,
)

_DIFF_GIT_LINE_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)\s*$")
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?: .*)?$")
_FILE_HEADER_RE = re.compile(r"^(---|\+\+\+) ([^\t\n]+?)(?:\t(.*))?$")
_INVALID_PATH_SEGMENT_RE = re.compile(r"(?:^|/)\.\.(?:/|$)")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_FORBIDDEN_PATH_CHAR_RE = re.compile(r"[:*?\"<>|]")


@dataclass(frozen=True, slots=True)
class PatchValidationResult:
    valid: bool
    reason: str = ""
    detail: str = ""

    @classmethod
    def ok(cls) -> PatchValidationResult:
        return cls(valid=True)

    @classmethod
    def invalid(cls, reason: str, detail: str) -> PatchValidationResult:
        return cls(valid=False, reason=reason, detail=detail)


def validate_unified_diff(patch: str) -> PatchValidationResult:
    """Validate unified diff syntax without rejecting semantically odd but legal diffs."""
    if not patch or not patch.strip():
        return PatchValidationResult.invalid(REASON_EMPTY_PATCH, "patch content is empty")

    if "diff --git " not in patch and "--- " not in patch:
        return PatchValidationResult.invalid(
            REASON_MALFORMED_PATCH,
            "missing diff --git or --- file header",
        )

    try:
        blocks = iter_diff_file_blocks(patch)
    except Exception as exc:
        return PatchValidationResult.invalid(
            REASON_MALFORMED_PATCH,
            f"failed to parse file blocks: {exc}",
        )

    if not blocks:
        return PatchValidationResult.invalid(
            REASON_MALFORMED_PATCH,
            "no file blocks parsed from patch",
        )

    seen_paths: set[str] = set()
    has_add_or_remove = False

    for path, block in blocks:
        path_error = _validate_filename(path)
        if path_error is not None:
            return PatchValidationResult.invalid(REASON_INVALID_FILENAME, path_error)

        for line in block.splitlines():
            if line.startswith("diff --git "):
                git_error = _validate_diff_git_line(line)
                if git_error is not None:
                    return PatchValidationResult.invalid(
                        REASON_INVALID_FILENAME,
                        git_error,
                    )
            elif line.startswith("--- ") or line.startswith("+++ "):
                marker = "---" if line.startswith("--- ") else "+++"
                header_error = _validate_file_header_line(line, marker=marker)
                if header_error is not None:
                    reason = (
                        REASON_INVALID_FILENAME
                        if "path" in header_error
                        or "filename" in header_error
                        or "absolute" in header_error
                        or "traversal" in header_error
                        or "forbidden" in header_error
                        else REASON_MALFORMED_PATCH
                    )
                    return PatchValidationResult.invalid(reason, header_error)

        if path in seen_paths:
            return PatchValidationResult.invalid(
                REASON_DUPLICATED_HEADERS,
                f"duplicate file header for {path!r}",
            )
        seen_paths.add(path)

        block_result = _validate_file_block(block)
        if block_result is not None:
            return block_result

        if _block_has_add_or_remove(block):
            has_add_or_remove = True

    if not has_add_or_remove:
        return PatchValidationResult.invalid(
            REASON_EMPTY_PATCH,
            "patch contains no added or removed lines",
        )

    return PatchValidationResult.ok()


def _strip_ab_prefix(path: str) -> str:
    raw = path.replace("\\", "/").strip()
    if raw.startswith("a/"):
        return raw[2:]
    if raw.startswith("b/"):
        return raw[2:]
    return raw


def _validate_filename(path: str) -> str | None:
    raw = _strip_ab_prefix(path)

    if raw in {"dev/null", "/dev/null"}:
        return None

    if not raw or raw in {".", ".."}:
        return f"invalid filename: {path!r}"

    if raw.startswith("/"):
        return f"absolute path not allowed: {path!r}"

    if _INVALID_PATH_SEGMENT_RE.search(raw):
        return f"path traversal segment in filename: {path!r}"

    if _CONTROL_CHAR_RE.search(raw):
        return f"control characters in path: {path!r}"

    if _FORBIDDEN_PATH_CHAR_RE.search(raw):
        return f"forbidden characters in path: {path!r}"

    if "\\" in path:
        return f"backslash path separator not allowed: {path!r}"

    return None


def _validate_file_block(block: str) -> PatchValidationResult | None:
    lines = block.splitlines()
    if not lines:
        return PatchValidationResult.invalid(REASON_MALFORMED_PATCH, "empty file block")

    index = 0
    if lines[0].startswith("diff --git "):
        git_error = _validate_diff_git_line(lines[0])
        if git_error is not None:
            return PatchValidationResult.invalid(REASON_MALFORMED_PATCH, git_error)
        index = 1

    minus_headers = 0
    plus_headers = 0
    hunk_count = 0
    in_hunk = False

    while index < len(lines):
        line = lines[index]

        if line.startswith("diff --git "):
            return PatchValidationResult.invalid(
                REASON_DUPLICATED_HEADERS,
                "nested diff --git header inside file block",
            )

        if line.startswith("--- "):
            header_error = _validate_file_header_line(line, marker="---")
            if header_error is not None:
                return PatchValidationResult.invalid(REASON_MALFORMED_PATCH, header_error)
            minus_headers += 1
            if minus_headers > 1:
                return PatchValidationResult.invalid(
                    REASON_DUPLICATED_HEADERS,
                    "duplicate --- header in file block",
                )
            index += 1
            continue

        if line.startswith("+++ "):
            header_error = _validate_file_header_line(line, marker="+++")
            if header_error is not None:
                return PatchValidationResult.invalid(REASON_MALFORMED_PATCH, header_error)
            plus_headers += 1
            if plus_headers > 1:
                return PatchValidationResult.invalid(
                    REASON_DUPLICATED_HEADERS,
                    "duplicate +++ header in file block",
                )
            index += 1
            continue

        if line.startswith("@@ "):
            if minus_headers == 0 or plus_headers == 0:
                return PatchValidationResult.invalid(
                    REASON_MALFORMED_PATCH,
                    "hunk header before ---/+++ file headers",
                )
            if not _HUNK_HEADER_RE.match(line):
                return PatchValidationResult.invalid(
                    REASON_MALFORMED_PATCH,
                    f"invalid hunk header: {line!r}",
                )
            in_hunk = True
            hunk_count += 1
            index += 1
            continue

        if line.startswith(("index ", "new file mode ", "deleted file mode ", "similarity ")):
            index += 1
            continue

        if line.startswith("rename from ") or line.startswith("rename to "):
            index += 1
            continue

        if in_hunk:
            if line.startswith("\\ No newline at end of file"):
                index += 1
                continue
            if not line and index == len(lines) - 1:
                index += 1
                continue
            if line[:1] not in {" ", "+", "-"}:
                return PatchValidationResult.invalid(
                    REASON_MALFORMED_PATCH,
                    f"invalid hunk line prefix: {line!r}",
                )
            index += 1
            continue

        if line.strip():
            return PatchValidationResult.invalid(
                REASON_MALFORMED_PATCH,
                f"unexpected line outside hunk: {line!r}",
            )
        index += 1

    if minus_headers == 0 or plus_headers == 0:
        return PatchValidationResult.invalid(
            REASON_MALFORMED_PATCH,
            "file block missing --- or +++ header",
        )
    if hunk_count == 0:
        return PatchValidationResult.invalid(
            REASON_MALFORMED_PATCH,
            "file block contains no hunk headers",
        )
    return None


def _validate_diff_git_line(line: str) -> str | None:
    match = _DIFF_GIT_LINE_RE.match(line)
    if match is None:
        return f"invalid diff --git line: {line!r}"
    left = match.group(1)
    right = match.group(2)
    for raw in (left, right):
        if raw == "dev/null":
            continue
        error = _validate_filename(raw)
        if error is not None:
            return error
    return None


def _validate_file_header_line(line: str, *, marker: str) -> str | None:
    match = _FILE_HEADER_RE.match(line)
    if match is None or match.group(1) != marker:
        return f"invalid {marker} header: {line!r}"
    raw_path = match.group(2).strip()
    if raw_path == "/dev/null":
        return None
    if raw_path.startswith("a/") or raw_path.startswith("b/"):
        raw_path = raw_path[2:]
    error = _validate_filename(raw_path)
    if error is not None:
        return error
    return None


def _block_has_add_or_remove(block: str) -> bool:
    in_hunk = False
    for line in block.splitlines():
        if line.startswith("@@ "):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            return True
        if line.startswith("-") and not line.startswith("---"):
            return True
    return False


__all__ = [
    "PATCH_INVALID_REASONS",
    "REASON_DUPLICATED_HEADERS",
    "REASON_EMPTY_PATCH",
    "REASON_INVALID_FILENAME",
    "REASON_MALFORMED_PATCH",
    "PatchValidationResult",
    "validate_unified_diff",
]
