"""Generate maintainer-certified controls manifests from Phase A runs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from earnbench.bootstrap_uncertainty import load_phase_summary_rows
from earnbench.certified_controls.github_metadata import (
    GitHubMergeEvidence,
    GitHubMetadataClient,
    HttpGitHubMetadataClient,
    parse_issue_number,
)
from earnbench.certified_controls.manifest import REQUIRED_COLUMNS, validate_certified_controls_manifest
from earnbench.provenance import utc_timestamp

PATCH_SOURCE_DATASET_GOLDEN = "dataset_golden"
REASON_GITHUB_METADATA_UNVERIFIED = "github_metadata_unverified"
REASON_UPSTREAM_SCOPE_NOT_AUDITED = "upstream_scope_not_audited"
REASON_MISSING_ISSUE_NUMBER = "missing_github_issue_number"


@dataclass(frozen=True, slots=True)
class GenerateManifestResult:
    output_path: Path
    row_count: int
    verified_count: int
    undecidable_count: int
    enrichment_cache_path: Path


def _bool_csv(value: bool) -> str:
    return "yes" if value else "no"


def _load_instance_meta(phase_a_run_dir: Path, instance_id: str) -> dict[str, Any]:
    meta_path = phase_a_run_dir / instance_id / "meta.json"
    if not meta_path.is_file():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _scope_flags_from_meta(meta: dict[str, Any]) -> dict[str, str]:
    stripped_paths = meta.get("stripped_paths") or []
    touches_tests = "no"
    touches_verifier = "no"
    touches_ci = "no"
    touches_environment = "no"
    if isinstance(stripped_paths, list) and stripped_paths:
        for path in stripped_paths:
            normalized = str(path).replace("\\", "/")
            if normalized.startswith("tests/") or "/tests/" in normalized or normalized.endswith("_test.py"):
                touches_tests = "yes"
            if "conftest.py" in normalized or normalized.endswith("pytest.ini"):
                touches_verifier = "yes"
            if normalized.startswith(".github/workflows/"):
                touches_ci = "yes"
            if normalized in {"setup.cfg", "tox.ini", "setup.py"}:
                touches_environment = "yes"
    return {
        "touches_tests": touches_tests,
        "touches_verifier": touches_verifier,
        "touches_ci": touches_ci,
        "touches_environment": touches_environment,
    }


def _derive_certification_status(
    *,
    evidence: GitHubMergeEvidence,
    nominal_success: bool,
    production_only: bool,
    scope_flags: dict[str, str],
    patch_sha256: str,
) -> tuple[str, str, str]:
    if not evidence.verified:
        reason = REASON_GITHUB_METADATA_UNVERIFIED
        if evidence.detail:
            reason = REASON_GITHUB_METADATA_UNVERIFIED
        return "undecidable", reason, evidence.detail or "github metadata could not be verified"

    scope_violation = any(
        scope_flags[key] == "yes"
        for key in (
            "touches_tests",
            "touches_verifier",
            "touches_ci",
            "touches_environment",
        )
    )
    if scope_violation:
        return (
            "undecidable",
            REASON_UPSTREAM_SCOPE_NOT_AUDITED,
            "EarnBench prod-only extract touched protected paths; upstream merge scope not audited",
        )

    certified_ready = (
        evidence.merged_by_maintainer
        and evidence.issue_closed
        and bool(evidence.upstream_commit)
        and bool(evidence.pr_url)
        and bool(evidence.issue_url)
        and production_only
        and nominal_success
        and bool(patch_sha256)
    )
    if certified_ready:
        return "certified_correct", "", evidence.detail

    return (
        "undecidable",
        REASON_GITHUB_METADATA_UNVERIFIED,
        evidence.detail or "certification gates not satisfied",
    )


def build_manifest_row(
    *,
    instance_id: str,
    repo: str,
    phase_a_row: dict[str, Any],
    meta: dict[str, Any],
    evidence: GitHubMergeEvidence,
    issue_number: int | None,
) -> dict[str, str]:
    y0 = str(phase_a_row.get("y0", "")).strip().lower() in {"1", "true", "yes"}
    patch_sha256 = str(meta.get("prod_patch_sha256", "")).strip()
    scope_flags = _scope_flags_from_meta(meta)
    stripped_paths = meta.get("stripped_paths") or []
    base_commit = str(meta.get("base_commit", "")).strip()

    status, exclusion_reason, detail = _derive_certification_status(
        evidence=evidence,
        nominal_success=y0,
        production_only=True,
        scope_flags=scope_flags,
        patch_sha256=patch_sha256,
    )

    notes_parts = [
        "Maintainer metadata enrichment from public GitHub API.",
    ]
    if issue_number is not None:
        notes_parts.append(f"github_issue_number={issue_number}.")
    if base_commit:
        notes_parts.append(f"base_commit={base_commit} is pre-fix snapshot.")
    if isinstance(stripped_paths, list) and stripped_paths:
        notes_parts.append(f"stripped_paths={stripped_paths}.")
    if detail:
        notes_parts.append(detail)

    return {
        "control_id": f"MC-{instance_id}",
        "instance_id": instance_id,
        "repo": repo,
        "upstream_commit": evidence.upstream_commit if evidence.verified else "",
        "upstream_pr": evidence.pr_url,
        "upstream_issue": evidence.issue_url,
        "patch_source": PATCH_SOURCE_DATASET_GOLDEN,
        "patch_sha256": patch_sha256,
        "merged_by_maintainer": _bool_csv(evidence.merged_by_maintainer and evidence.verified),
        "issue_closed": _bool_csv(evidence.issue_closed and evidence.verified),
        "production_only": "yes",
        "touches_tests": scope_flags["touches_tests"],
        "touches_verifier": scope_flags["touches_verifier"],
        "touches_ci": scope_flags["touches_ci"],
        "touches_environment": scope_flags["touches_environment"],
        "nominal_success": _bool_csv(y0),
        "certification_status": status,
        "exclusion_reason": exclusion_reason,
        "notes": " ".join(notes_parts),
    }


def generate_certified_controls_manifest(
    *,
    phase_a_run_dir: Path,
    output_path: Path,
    github_client: GitHubMetadataClient | None = None,
    github_token: str | None = None,
    write_enrichment_cache: bool = True,
) -> GenerateManifestResult:
    """Build a maintainer controls manifest with public GitHub metadata enrichment."""
    phase_a_run_dir = phase_a_run_dir.resolve()
    output_path = output_path.resolve()
    client = github_client or HttpGitHubMetadataClient(token=github_token)

    summary_rows = load_phase_summary_rows(phase_a_run_dir)
    manifest_rows: list[dict[str, str]] = []
    enrichment_records: list[dict[str, Any]] = []
    verified_count = 0

    for phase_a_row in sorted(summary_rows, key=lambda row: str(row["instance_id"])):
        instance_id = str(phase_a_row["instance_id"]).strip()
        repo = str(phase_a_row.get("repo") or "").strip()
        if not repo:
            meta = _load_instance_meta(phase_a_run_dir, instance_id)
            repo = str(meta.get("repo", "")).strip()

        issue_number = parse_issue_number(instance_id)
        if issue_number is None or not repo:
            evidence = GitHubMergeEvidence.unverified(REASON_MISSING_ISSUE_NUMBER)
        else:
            evidence = client.fetch_merge_evidence(repo=repo, issue_number=issue_number)

        meta = _load_instance_meta(phase_a_run_dir, instance_id)
        row = build_manifest_row(
            instance_id=instance_id,
            repo=repo,
            phase_a_row=phase_a_row,
            meta=meta,
            evidence=evidence,
            issue_number=issue_number,
        )
        manifest_rows.append(row)
        if evidence.verified:
            verified_count += 1
        enrichment_records.append(
            {
                "instance_id": instance_id,
                "repo": repo,
                "issue_number": issue_number,
                "verified": evidence.verified,
                "issue_url": evidence.issue_url,
                "pr_url": evidence.pr_url,
                "upstream_commit": evidence.upstream_commit,
                "merged_by_maintainer": evidence.merged_by_maintainer,
                "issue_closed": evidence.issue_closed,
                "detail": evidence.detail,
                "certification_status": row["certification_status"],
                "exclusion_reason": row["exclusion_reason"],
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(row)

    cache_path = output_path.with_suffix(".enrichment.json")
    if write_enrichment_cache:
        cache_path.write_text(
            json.dumps(
                {
                    "schema_version": "earnbench.maintainer_certified_enrichment.v1",
                    "phase_a_run": str(phase_a_run_dir),
                    "generated_at_utc": utc_timestamp(),
                    "row_count": len(manifest_rows),
                    "verified_count": verified_count,
                    "records": enrichment_records,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    validation = validate_certified_controls_manifest(output_path)
    if not validation.ok:
        msg = "; ".join(validation.errors)
        raise ValueError(msg)

    undecidable_count = sum(
        1 for row in manifest_rows if row["certification_status"] == "undecidable"
    )
    return GenerateManifestResult(
        output_path=output_path,
        row_count=len(manifest_rows),
        verified_count=verified_count,
        undecidable_count=undecidable_count,
        enrichment_cache_path=cache_path,
    )


__all__ = [
    "GenerateManifestResult",
    "REASON_GITHUB_METADATA_UNVERIFIED",
    "REASON_MISSING_ISSUE_NUMBER",
    "REASON_UPSTREAM_SCOPE_NOT_AUDITED",
    "build_manifest_row",
    "generate_certified_controls_manifest",
]
