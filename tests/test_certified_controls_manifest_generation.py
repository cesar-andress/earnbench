"""Tests for maintainer-certified controls manifest generation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from earnbench.certified_controls.generate_manifest import (
    REASON_GITHUB_METADATA_UNVERIFIED,
    build_manifest_row,
    generate_certified_controls_manifest,
)
from earnbench.certified_controls.github_metadata import (
    GitHubMergeEvidence,
    parse_issue_number,
)
from earnbench.certified_controls.manifest import validate_certified_controls_manifest

VALID_SHA256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

SUMMARY_HEADER = (
    "instance_id",
    "repo",
    "y0",
    "y_vtest",
    "y_verif",
    "y_env",
    "pi_vtest_status",
    "pi_verif_status",
    "pi_env_status",
    "valid_pi_count",
    "ef_pi",
    "ef_exclude_invalid",
    "ef_invalid_as_fail",
    "invalid_pi_count",
    "invalid_pi_rate",
    "ef_sensitivity_gap",
    "ef_status",
    "false_unearned",
    "retained",
    "exclude_reason",
    "run_id",
    "config_digest",
)


class FakeGitHubClient:
    def __init__(self, responses: dict[tuple[str, int], GitHubMergeEvidence]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, int]] = []

    def fetch_merge_evidence(
        self,
        *,
        repo: str,
        issue_number: int,
    ) -> GitHubMergeEvidence:
        self.calls.append((repo, issue_number))
        return self.responses.get(
            (repo, issue_number),
            GitHubMergeEvidence.unverified("not configured in fake client"),
        )


def _write_phase_a_run(tmp_path: Path, *, instance_id: str, y0: str = "1") -> Path:
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    with (phase_a / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SUMMARY_HEADER)
        writer.writerow(
            [
                instance_id,
                "psf/requests",
                y0,
                "1",
                "1",
                "1",
                "ok",
                "ok",
                "ok",
                "3",
                "1.0",
                "1.0",
                "1.0",
                "0",
                "0.0",
                "0.0",
                "defined",
                "0",
                "1",
                "",
                "run-1",
                "digest",
            ]
        )
    instance_dir = phase_a / instance_id
    instance_dir.mkdir()
    (instance_dir / "meta.json").write_text(
        json.dumps(
            {
                "instance_id": instance_id,
                "repo": "psf/requests",
                "base_commit": "deadbeef1234567890abcdef1234567890abcdef12",
                "prod_patch_sha256": VALID_SHA256,
                "stripped_paths": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return phase_a


def test_parse_issue_number_from_instance_id() -> None:
    assert parse_issue_number("psf__requests-1724") == 1724
    assert parse_issue_number("astropy__astropy-12907") == 12907
    assert parse_issue_number("invalid") is None


def test_build_manifest_row_verified_metadata() -> None:
    evidence = GitHubMergeEvidence(
        issue_url="https://github.com/psf/requests/issues/1724",
        issue_closed=True,
        pr_url="https://github.com/psf/requests/pull/2000",
        merged_by_maintainer=True,
        upstream_commit="abc123def4567890abcdef1234567890abcdef1234",
        verified=True,
        detail="verified from public GitHub issue and merged pull request",
    )
    row = build_manifest_row(
        instance_id="psf__requests-1724",
        repo="psf/requests",
        phase_a_row={"y0": "1"},
        meta={"prod_patch_sha256": VALID_SHA256, "stripped_paths": []},
        evidence=evidence,
        issue_number=1724,
    )
    assert row["upstream_commit"] == evidence.upstream_commit
    assert row["upstream_pr"] == evidence.pr_url
    assert row["upstream_issue"] == evidence.issue_url
    assert row["merged_by_maintainer"] == "yes"
    assert row["issue_closed"] == "yes"
    assert row["certification_status"] == "certified_correct"
    assert row["exclusion_reason"] == ""


def test_build_manifest_row_unverified_metadata_is_undecidable() -> None:
    evidence = GitHubMergeEvidence.unverified("github issue not found")
    row = build_manifest_row(
        instance_id="psf__requests-1724",
        repo="psf/requests",
        phase_a_row={"y0": "1"},
        meta={"prod_patch_sha256": VALID_SHA256, "stripped_paths": []},
        evidence=evidence,
        issue_number=1724,
    )
    assert row["upstream_commit"] == ""
    assert row["upstream_pr"] == ""
    assert row["upstream_issue"] == ""
    assert row["merged_by_maintainer"] == "no"
    assert row["issue_closed"] == "no"
    assert row["certification_status"] == "undecidable"
    assert row["exclusion_reason"] == REASON_GITHUB_METADATA_UNVERIFIED


def test_generate_manifest_with_fake_github_client(tmp_path: Path) -> None:
    instance_id = "psf__requests-1724"
    phase_a = _write_phase_a_run(tmp_path, instance_id=instance_id)
    output = tmp_path / "maintainer_certified_controls.csv"
    fake = FakeGitHubClient(
        {
            ("psf/requests", 1724): GitHubMergeEvidence(
                issue_url="https://github.com/psf/requests/issues/1724",
                issue_closed=True,
                pr_url="https://github.com/psf/requests/pull/2000",
                merged_by_maintainer=True,
                upstream_commit="abc123def4567890abcdef1234567890abcdef1234",
                verified=True,
            ),
        }
    )

    result = generate_certified_controls_manifest(
        phase_a_run_dir=phase_a,
        output_path=output,
        github_client=fake,
    )

    assert result.row_count == 1
    assert result.verified_count == 1
    assert output.is_file()
    validation = validate_certified_controls_manifest(output)
    assert validation.ok
    with output.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["merged_by_maintainer"] == "yes"
    assert row["upstream_pr"] == "https://github.com/psf/requests/pull/2000"
    assert row["certification_status"] == "certified_correct"
    cache = json.loads(result.enrichment_cache_path.read_text(encoding="utf-8"))
    assert cache["verified_count"] == 1


def test_generate_manifest_does_not_fabricate_missing_github_metadata(tmp_path: Path) -> None:
    instance_id = "psf__requests-1724"
    phase_a = _write_phase_a_run(tmp_path, instance_id=instance_id)
    output = tmp_path / "maintainer_certified_controls.csv"
    fake = FakeGitHubClient({})

    result = generate_certified_controls_manifest(
        phase_a_run_dir=phase_a,
        output_path=output,
        github_client=fake,
    )

    assert result.verified_count == 0
    assert result.undecidable_count == 1
    with output.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["merged_by_maintainer"] == "no"
    assert row["upstream_commit"] == ""
    assert row["certification_status"] == "undecidable"


def test_cli_generate_manifest(capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from earnbench.cli import main

    instance_id = "psf__requests-1724"
    phase_a = _write_phase_a_run(tmp_path, instance_id=instance_id)
    output = tmp_path / "manifest.csv"

    fake = FakeGitHubClient(
        {
            ("psf/requests", 1724): GitHubMergeEvidence(
                issue_url="https://github.com/psf/requests/issues/1724",
                issue_closed=True,
                pr_url="https://github.com/psf/requests/pull/2000",
                merged_by_maintainer=True,
                upstream_commit="abc123def4567890abcdef1234567890abcdef1234",
                verified=True,
            ),
        }
    )

    monkeypatch.setattr(
        "earnbench.certified_controls.generate_manifest.HttpGitHubMetadataClient",
        lambda **kwargs: fake,
    )
    exit_code = main(
        [
            "controls",
            "generate-manifest",
            "--phase-a-run",
            str(phase_a),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verified_count"] == 1
    assert Path(payload["manifest_path"]).is_file()
