"""Public GitHub metadata lookup for maintainer certification."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

ISSUE_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)"
)
PR_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)


@dataclass(frozen=True, slots=True)
class GitHubMergeEvidence:
    """Verified maintainer merge evidence from public GitHub metadata."""

    issue_url: str
    issue_closed: bool
    pr_url: str
    merged_by_maintainer: bool
    upstream_commit: str
    verified: bool
    detail: str = ""

    @classmethod
    def unverified(cls, detail: str) -> GitHubMergeEvidence:
        return cls(
            issue_url="",
            issue_closed=False,
            pr_url="",
            merged_by_maintainer=False,
            upstream_commit="",
            verified=False,
            detail=detail,
        )


class GitHubMetadataClient(Protocol):
    """Protocol for GitHub metadata fetchers (live API or test doubles)."""

    def fetch_merge_evidence(
        self,
        *,
        repo: str,
        issue_number: int,
    ) -> GitHubMergeEvidence: ...


def parse_issue_number(instance_id: str) -> int | None:
    """Parse trailing GitHub issue number from a SWE-bench instance id."""
    suffix = instance_id.rsplit("-", 1)[-1]
    if suffix.isdigit():
        return int(suffix)
    return None


def issue_url(owner: str, repo_name: str, issue_number: int) -> str:
    return f"https://github.com/{owner}/{repo_name}/issues/{issue_number}"


def pull_url(owner: str, repo_name: str, pull_number: int) -> str:
    return f"https://github.com/{owner}/{repo_name}/pull/{pull_number}"


def resolve_github_token(explicit: str | None = None) -> str | None:
    """Return an explicit token or fall back to the GITHUB_TOKEN environment variable."""
    if explicit and explicit.strip():
        return explicit.strip()
    env_token = os.environ.get("GITHUB_TOKEN", "").strip()
    return env_token or None


class HttpGitHubMetadataClient:
    """Fetch maintainer merge evidence from the public GitHub REST API."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._token = resolve_github_token(token) or ""
        self._api_base = api_base.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def fetch_merge_evidence(
        self,
        *,
        repo: str,
        issue_number: int,
    ) -> GitHubMergeEvidence:
        owner, repo_name = _split_repo(repo)
        if owner is None or repo_name is None:
            return GitHubMergeEvidence.unverified(f"invalid repo slug: {repo!r}")

        issue_payload = self._get_json(
            f"/repos/{owner}/{repo_name}/issues/{issue_number}",
        )
        if issue_payload is None:
            return GitHubMergeEvidence.unverified(
                f"github issue not found: {owner}/{repo_name}#{issue_number}"
            )
        if issue_payload.get("pull_request"):
            return GitHubMergeEvidence.unverified(
                f"github number {issue_number} is a pull request, not an issue"
            )

        issue_html_url = str(issue_payload.get("html_url", "")).strip()
        if not issue_html_url:
            issue_html_url = issue_url(owner, repo_name, issue_number)
        issue_closed = str(issue_payload.get("state", "")).strip().lower() == "closed"

        pull_payload = self._find_merged_pull(
            owner=owner,
            repo_name=repo_name,
            issue_number=issue_number,
        )
        if pull_payload is None:
            detail = "no merged pull request linked to issue"
            if not issue_closed:
                detail = "issue open and no merged pull request found"
            return GitHubMergeEvidence(
                issue_url=issue_html_url,
                issue_closed=issue_closed,
                pr_url="",
                merged_by_maintainer=False,
                upstream_commit="",
                verified=False,
                detail=detail,
            )

        pr_html_url = str(pull_payload.get("html_url", "")).strip()
        merged = bool(pull_payload.get("merged"))
        merge_commit_sha = str(pull_payload.get("merge_commit_sha", "")).strip()
        if not merged or not merge_commit_sha:
            return GitHubMergeEvidence(
                issue_url=issue_html_url,
                issue_closed=issue_closed,
                pr_url=pr_html_url,
                merged_by_maintainer=False,
                upstream_commit="",
                verified=False,
                detail="pull request found but not merged",
            )

        if not issue_closed:
            return GitHubMergeEvidence(
                issue_url=issue_html_url,
                issue_closed=False,
                pr_url=pr_html_url,
                merged_by_maintainer=True,
                upstream_commit=merge_commit_sha,
                verified=False,
                detail="merged pull request found but issue is not closed",
            )

        return GitHubMergeEvidence(
            issue_url=issue_html_url,
            issue_closed=True,
            pr_url=pr_html_url,
            merged_by_maintainer=True,
            upstream_commit=merge_commit_sha,
            verified=True,
            detail="verified from public GitHub issue and merged pull request",
        )

    def _find_merged_pull(
        self,
        *,
        owner: str,
        repo_name: str,
        issue_number: int,
    ) -> dict[str, Any] | None:
        for event in self._get_json_list(
            f"/repos/{owner}/{repo_name}/issues/{issue_number}/events",
        ):
            if str(event.get("event", "")).strip().lower() != "cross-referenced":
                continue
            source = event.get("source") or {}
            source_issue = source.get("issue") or {}
            pull_ref = source_issue.get("pull_request") or {}
            pull_api_url = str(pull_ref.get("url", "")).strip()
            if not pull_api_url:
                continue
            pull_payload = self._get_json_from_url(pull_api_url)
            if pull_payload is not None and bool(pull_payload.get("merged")):
                return pull_payload

        query = urllib.parse.quote(
            f"repo:{owner}/{repo_name} is:pr is:merged {issue_number} in:body",
            safe="",
        )
        search_payload = self._get_json(f"/search/issues?q={query}&per_page=5")
        if isinstance(search_payload, dict):
            for item in search_payload.get("items") or []:
                if not isinstance(item, dict):
                    continue
                pull_number = item.get("number")
                if pull_number is None:
                    continue
                pull_payload = self._get_json(
                    f"/repos/{owner}/{repo_name}/pulls/{int(pull_number)}",
                )
                if pull_payload is not None and bool(pull_payload.get("merged")):
                    return pull_payload
        return None

    def _get_json(self, path: str) -> dict[str, Any] | None:
        url = f"{self._api_base}{path}"
        payload = self._request(url)
        if isinstance(payload, dict):
            return payload
        return None

    def _get_json_list(self, path: str) -> list[dict[str, Any]]:
        payload = self._request(f"{self._api_base}{path}")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _get_json_from_url(self, url: str) -> dict[str, Any] | None:
        payload = self._request(url)
        if isinstance(payload, dict):
            return payload
        return None

    def _request(self, url: str) -> Any | None:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "earnbench-maintainer-certification",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as handle:
                body = handle.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 451}:
                return None
            detail = exc.read().decode("utf-8", errors="replace")
            msg = f"github api error {exc.code} for {url}: {detail}"
            raise RuntimeError(msg) from exc
        except urllib.error.URLError as exc:
            msg = f"github api request failed for {url}: {exc}"
            raise RuntimeError(msg) from exc
        if not body.strip():
            return None
        return json.loads(body)


def _split_repo(repo: str) -> tuple[str | None, str | None]:
    normalized = repo.strip().replace("\\", "/")
    if normalized.count("/") != 1:
        return None, None
    owner, repo_name = normalized.split("/", 1)
    if not owner or not repo_name:
        return None, None
    return owner, repo_name


__all__ = [
    "GitHubMergeEvidence",
    "GitHubMetadataClient",
    "HttpGitHubMetadataClient",
    "issue_url",
    "parse_issue_number",
    "pull_url",
]
