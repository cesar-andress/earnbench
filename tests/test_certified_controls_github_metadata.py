"""Tests for GitHub metadata client."""

from __future__ import annotations

import json
from unittest.mock import patch

from earnbench.certified_controls.github_metadata import HttpGitHubMetadataClient


def test_http_client_fetches_verified_merge_evidence() -> None:
    client = HttpGitHubMetadataClient()

    def _fake_urlopen(request, timeout=0):
        url = request.full_url

        class _Response:
            def __init__(self, payload: object) -> None:
                self._payload = payload

            def read(self) -> bytes:
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        if url.endswith("/issues/1724"):
            return _Response(
                {
                    "html_url": "https://github.com/psf/requests/issues/1724",
                    "state": "closed",
                }
            )
        if url.endswith("/issues/1724/events"):
            return _Response(
                [
                    {
                        "event": "cross-referenced",
                        "source": {
                            "issue": {
                                "pull_request": {
                                    "url": "https://api.github.com/repos/psf/requests/pulls/2000",
                                }
                            }
                        },
                    }
                ]
            )
        if url.endswith("/pulls/2000"):
            return _Response(
                {
                    "html_url": "https://github.com/psf/requests/pull/2000",
                    "merged": True,
                    "merge_commit_sha": "abc123def4567890abcdef1234567890abcdef1234",
                }
            )
        raise AssertionError(f"unexpected url: {url}")

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        evidence = client.fetch_merge_evidence(repo="psf/requests", issue_number=1724)

    assert evidence.verified is True
    assert evidence.issue_url == "https://github.com/psf/requests/issues/1724"
    assert evidence.pr_url == "https://github.com/psf/requests/pull/2000"
    assert evidence.upstream_commit == "abc123def4567890abcdef1234567890abcdef1234"
    assert evidence.merged_by_maintainer is True
    assert evidence.issue_closed is True
