from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


def _token() -> str:
    return os.getenv("GITHUB_TOKEN", "").strip() or os.getenv("GH_TOKEN", "").strip()


@dataclass(slots=True)
class GitHubCommitInfo:
    repo: str
    branch: str
    sha: str
    html_url: str | None
    commit_url: str | None
    message: str | None
    author: str | None
    fetched_at: str | None = None


def fetch_remote_commit(repo: str, branch: str, *, timeout: int = 30) -> GitHubCommitInfo:
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "SalesBrain",
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"github_commit_fetch_failed: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"github_commit_fetch_failed: {exc.reason}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("github_commit_fetch_failed: invalid_response")

    sha = str(payload.get("sha") or "").strip()
    if not sha:
        raise RuntimeError("github_commit_fetch_failed: missing_sha")

    commit = payload.get("commit") if isinstance(payload.get("commit"), dict) else {}
    author = None
    message = None
    if isinstance(commit, dict):
        author_info = commit.get("author")
        if isinstance(author_info, dict):
            author = str(author_info.get("name") or author_info.get("email") or "").strip() or None
        message = str(commit.get("message") or "").strip() or None
    return GitHubCommitInfo(
        repo=repo,
        branch=branch,
        sha=sha,
        html_url=str(payload.get("html_url") or "").strip() or None,
        commit_url=str(payload.get("url") or "").strip() or None,
        message=message,
        author=author,
    )


def compare_commit_ids(installed_sha: str | None, remote_sha: str | None) -> dict[str, Any]:
    installed = (installed_sha or "").strip()
    remote = (remote_sha or "").strip()
    return {
        "installed_sha": installed or None,
        "remote_sha": remote or None,
        "update_available": bool(installed and remote and installed != remote),
        "baseline_missing": not installed,
    }
