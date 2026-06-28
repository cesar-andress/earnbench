"""Syntax-only unified diff repair for Phase C patch collection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from earnbench.agents.patch_validation import validate_unified_diff

REPAIR_TRAILING_NEWLINE = "trailing_newline"
REPAIR_MALFORMED_HEADERS = "malformed_headers"
REPAIR_DUPLICATED_HEADERS = "duplicated_headers"
REPAIR_HUNK_OFFSETS = "hunk_offsets"

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_FILE_HEADER_RE = re.compile(r"^(---|\+\+\+) (.+?)(?:\t(.*))?$")


@dataclass(frozen=True, slots=True)
class PatchRepairResult:
    """Outcome of optional syntax-only patch repair."""

    applied: bool
    patch: str
    original_patch: str
    repairs: tuple[str, ...] = ()

    @classmethod
    def unchanged(cls, patch: str) -> PatchRepairResult:
        return cls(applied=False, patch=patch, original_patch=patch)


def repair_unified_diff(patch: str) -> PatchRepairResult:
    """Apply syntax-only repairs without changing hunk line content."""
    original = patch
    if not patch or not patch.strip():
        return PatchRepairResult.unchanged(patch)

    repairs: list[str] = []
    current = patch

    for name, step in (
        (REPAIR_TRAILING_NEWLINE, _repair_trailing_newline),
        (REPAIR_MALFORMED_HEADERS, _repair_malformed_headers),
        (REPAIR_DUPLICATED_HEADERS, _repair_duplicated_headers),
        (REPAIR_HUNK_OFFSETS, _repair_hunk_offsets),
    ):
        updated = step(current)
        if updated != current:
            repairs.append(name)
            current = updated

    if not _hunk_content_lines_equal(original, current):
        return PatchRepairResult.unchanged(original)

    if current == original:
        return PatchRepairResult.unchanged(original)

    if not validate_unified_diff(current).valid:
        return PatchRepairResult.unchanged(original)

    return PatchRepairResult(
        applied=True,
        patch=current,
        original_patch=original,
        repairs=tuple(repairs),
    )


def maybe_repair_unified_diff(
    patch: str,
    *,
    enabled: bool,
) -> PatchRepairResult:
    """Validate patch text and optionally repair before re-validation."""
    if validate_unified_diff(patch).valid:
        return PatchRepairResult.unchanged(patch)
    if not enabled:
        return PatchRepairResult.unchanged(patch)
    return repair_unified_diff(patch)


def _repair_trailing_newline(patch: str) -> str:
    if not patch:
        return patch
    if patch.endswith("\n"):
        return patch
    return patch + "\n"


def _repair_malformed_headers(patch: str) -> str:
    blocks = _split_patch_blocks(patch)
    if not blocks:
        return _repair_trailing_newline(patch)

    repaired_blocks: list[str] = []
    for block in blocks:
        repaired_blocks.append(_repair_block_headers(block))
    body = "".join(repaired_blocks)
    return body if body.endswith("\n") else body + "\n"


def _repair_duplicated_headers(patch: str) -> str:
    blocks = _split_patch_blocks(patch)
    if not blocks:
        return patch

    repaired_blocks: list[str] = []
    for block in blocks:
        repaired_blocks.append(_dedupe_block_headers(block))
    return "".join(repaired_blocks)


def _repair_hunk_offsets(patch: str) -> str:
    blocks = _split_patch_blocks(patch)
    if not blocks:
        return patch

    repaired_blocks: list[str] = []
    for block in blocks:
        repaired_blocks.append(_repair_block_hunk_offsets(block))
    return "".join(repaired_blocks)


def _split_patch_blocks(patch: str) -> list[str]:
    lines = patch.splitlines(keepends=True)
    if not lines:
        return []

    starts: list[int] = []
    for index, line in enumerate(lines):
        if line.startswith("diff --git "):
            starts.append(index)
        elif line.startswith("--- ") and not starts:
            starts.append(index)

    if not starts:
        return [patch]

    blocks: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        blocks.append("".join(lines[start:end]))
    return blocks


def _repair_block_headers(block: str) -> str:
    lines = block.splitlines(keepends=True)
    if not lines:
        return block

    out: list[str] = []
    minus_path = ""
    plus_path = ""
    has_diff_git = lines[0].startswith("diff --git ")

    for line in lines:
        if line.startswith("diff --git "):
            out.append(line)
            continue
        if line.startswith("--- "):
            fixed = _fix_header_line(line, marker="---")
            minus_path = _header_path(fixed, marker="---")
            out.append(fixed)
            continue
        if line.startswith("+++ "):
            fixed = _fix_header_line(line, marker="+++")
            plus_path = _header_path(fixed, marker="+++")
            out.append(fixed)
            continue
        out.append(line)

    if not has_diff_git and minus_path and plus_path:
        out.insert(0, f"diff --git a/{minus_path} b/{plus_path}\n")

    return "".join(out)


def _dedupe_block_headers(block: str) -> str:
    lines = block.splitlines(keepends=True)
    if not lines:
        return block

    out: list[str] = []
    seen_minus: str | None = None
    seen_plus: str | None = None

    for line in lines:
        if line.startswith("--- "):
            path = _header_path(line, marker="---")
            if path == seen_minus:
                continue
            seen_minus = path
            out.append(line)
            continue
        if line.startswith("+++ "):
            path = _header_path(line, marker="+++")
            if path == seen_plus:
                continue
            seen_plus = path
            out.append(line)
            continue
        out.append(line)
    return "".join(out)


def _repair_block_hunk_offsets(block: str) -> str:
    lines = block.splitlines(keepends=True)
    if not lines:
        return block

    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("@@ "):
            out.append(line)
            index += 1
            continue

        hunk_lines: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            candidate = lines[cursor]
            if candidate.startswith(
                ("@@ ", "diff --git ", "--- ", "+++ ", "rename from ", "rename to ")
            ):
                break
            if candidate.startswith(("index ", "new file mode ", "deleted file mode ")):
                break
            hunk_lines.append(candidate)
            cursor += 1

        match = _HUNK_HEADER_RE.match(line.rstrip("\n"))
        if match is None:
            out.append(line)
            out.extend(hunk_lines)
            index = cursor
            continue

        old_count = sum(
            1
            for hunk_line in hunk_lines
            if hunk_line[:1] in {" ", "-"} and not hunk_line.startswith("---")
        )
        new_count = sum(
            1
            for hunk_line in hunk_lines
            if hunk_line[:1] in {" ", "+"} and not hunk_line.startswith("+++")
        )
        suffix = match.group(5) or ""
        old_start = match.group(1)
        new_start = match.group(3)
        repaired_header = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{suffix}\n"
        out.append(repaired_header)
        out.extend(hunk_lines)
        index = cursor

    return "".join(out)


def _fix_header_line(line: str, *, marker: str) -> str:
    match = _FILE_HEADER_RE.match(line.rstrip("\n"))
    if match is None or match.group(1) != marker:
        return line

    raw_path = match.group(2).strip()
    timestamp = match.group(3) or ""
    if raw_path == "/dev/null":
        fixed_path = "/dev/null"
    else:
        prefix = "a/" if marker == "---" else "b/"
        normalized = raw_path.replace("\\", "/")
        if normalized.startswith("a/") or normalized.startswith("b/"):
            if marker == "---" and normalized.startswith("b/"):
                normalized = "a/" + normalized[2:]
            elif marker == "+++" and normalized.startswith("a/"):
                normalized = "b/" + normalized[2:]
            fixed_path = normalized
        else:
            fixed_path = prefix + normalized.lstrip("/")

    suffix = f"\t{timestamp}" if timestamp else ""
    return f"{marker} {fixed_path}{suffix}\n"


def _header_path(line: str, *, marker: str) -> str:
    match = _FILE_HEADER_RE.match(line.rstrip("\n"))
    if match is None or match.group(1) != marker:
        return ""
    raw_path = match.group(2).strip()
    if raw_path == "/dev/null":
        return raw_path
    if raw_path.startswith("a/") or raw_path.startswith("b/"):
        return raw_path[2:]
    return raw_path.lstrip("/")


def _hunk_content_lines(patch: str) -> list[str]:
    lines: list[str] = []
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("@@ "):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("\\ No newline at end of file"):
            continue
        if line.startswith(("diff --git ", "--- ", "+++ ", "index ", "new file mode ")):
            in_hunk = False
            continue
        if line[:1] in {" ", "+", "-"}:
            lines.append(line)
    return lines


def _hunk_content_lines_equal(left: str, right: str) -> bool:
    return _hunk_content_lines(left) == _hunk_content_lines(right)


__all__ = [
    "PatchRepairResult",
    "REPAIR_DUPLICATED_HEADERS",
    "REPAIR_HUNK_OFFSETS",
    "REPAIR_MALFORMED_HEADERS",
    "REPAIR_TRAILING_NEWLINE",
    "maybe_repair_unified_diff",
    "repair_unified_diff",
]
