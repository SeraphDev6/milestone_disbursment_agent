"""GitHub adapter: a merged pull request labelled with a milestone -> code_change evidence.

The payee registers their repo on the agreement and adds a repo webhook (pull_request events)
with the URL and secret we return. Matching is explicit: a PR counts for a milestone only when it
carries that milestone's label. Unlabelled PRs are logged and dropped, never guessed.

Facts this adapter vouches for (and a payee can't assert through the API):
- the merge commit SHA (pinned_ref), so later pushes can't change what was verified;
- ci_green: every check run on the PR's head commit completed successfully.
"""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

from app.domain import EvidenceIn, EvidenceType

LABEL_PREFIX = "mde-"
PASSING = {"success", "neutral", "skipped"}
MAX_FILES_LISTED = 100
_REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")


def label_for(milestone_id: str) -> str:
    return f"{LABEL_PREFIX}{milestone_id}"


def valid_repo(repo: str) -> bool:
    return bool(_REPO.match(repo))


def signature_ok(secret: str, body: bytes, header: str | None) -> bool:
    """GitHub's X-Hub-Signature-256: 'sha256=' + HMAC-SHA256(secret, raw body)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


class GitHubError(Exception):
    pass


class GitHubAPI(Protocol):
    def check_runs(self, repo: str, sha: str) -> list[dict]: ...
    def pr_files(self, repo: str, number: int) -> list[dict]: ...


class GitHubClient:
    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com", timeout: float = 10.0):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def _get(self, path: str) -> dict | list:
        try:
            r = self._client.get(path, params={"per_page": 100})
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub unreachable: {e}") from e
        if r.status_code != 200:
            raise GitHubError(f"GitHub {r.status_code} for {path}")
        return r.json()

    def check_runs(self, repo: str, sha: str) -> list[dict]:
        return self._get(f"/repos/{repo}/commits/{sha}/check-runs").get("check_runs", [])  # type: ignore[union-attr]

    def pr_files(self, repo: str, number: int) -> list[dict]:
        return self._get(f"/repos/{repo}/pulls/{number}/files")  # type: ignore[return-value]


@dataclass
class MergedPR:
    repo: str
    number: int
    title: str
    body: str
    url: str
    labels: list[str]
    head_sha: str
    merge_sha: str
    base_ref: str
    merged_by: str
    merged_at: datetime

    def milestone_ids(self) -> list[str]:
        return [lbl.removeprefix(LABEL_PREFIX) for lbl in self.labels if lbl.startswith(LABEL_PREFIX)]


def parse_merged_pr(event: str | None, payload: dict) -> MergedPR | None:
    """Only `pull_request` events that closed a PR by merging it count."""
    if event != "pull_request" or payload.get("action") != "closed":
        return None
    pr = payload.get("pull_request") or {}
    if not pr.get("merged") or not pr.get("merge_commit_sha"):
        return None
    return MergedPR(
        repo=payload["repository"]["full_name"],
        number=pr["number"],
        title=pr.get("title") or "",
        body=pr.get("body") or "",
        url=pr["html_url"],
        labels=[lbl["name"] for lbl in pr.get("labels", [])],
        head_sha=pr["head"]["sha"],
        merge_sha=pr["merge_commit_sha"],
        base_ref=pr["base"]["ref"],
        merged_by=(pr.get("merged_by") or {}).get("login", "unknown"),
        merged_at=datetime.fromisoformat(pr["merged_at"]),
    )


@dataclass
class CIStatus:
    green: bool | None  # None: couldn't determine
    summary: str


def ci_status(api: GitHubAPI, repo: str, sha: str) -> CIStatus:
    try:
        runs = api.check_runs(repo, sha)
    except GitHubError as e:
        return CIStatus(None, f"CI status unavailable ({e}).")
    if not runs:
        return CIStatus(False, "No CI check runs on the head commit.")
    lines = [f"- {r.get('name')}: {r.get('conclusion') or r.get('status')}" for r in runs]
    pending = [r for r in runs if r.get("status") != "completed"]
    failed = [r for r in runs if r.get("status") == "completed" and r.get("conclusion") not in PASSING]
    if pending:
        verdict = f"{len(pending)} check run(s) still running at merge."
    elif failed:
        verdict = f"{len(failed)} check run(s) did not pass."
    else:
        verdict = f"All {len(runs)} check run(s) passed."
    return CIStatus(not pending and not failed, verdict + "\n" + "\n".join(lines))


def build_evidence(pr: MergedPR, api: GitHubAPI) -> tuple[EvidenceIn, dict[str, bool]]:
    ci = ci_status(api, pr.repo, pr.head_sha)
    try:
        files = api.pr_files(pr.repo, pr.number)
        changed = "\n".join(
            f"- {f.get('filename')} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})"
            for f in files[:MAX_FILES_LISTED]
        )
        files_text = f"Changed files ({len(files)}{'+' if len(files) >= MAX_FILES_LISTED else ''}):\n{changed}"
    except GitHubError as e:
        files_text = f"Changed files unavailable ({e})."
    content = (
        f"Pull request #{pr.number} '{pr.title}' in {pr.repo}, merged into {pr.base_ref} by {pr.merged_by} "
        f"at {pr.merged_at.isoformat()}.\nURL: {pr.url}\nHead commit: {pr.head_sha}\nMerge commit: {pr.merge_sha}\n\n"
        f"CI on head commit (checked by the engine): {ci.summary}\n\n{files_text}\n\n"
        f"PR description (written by the payee, not verified):\n{pr.body[:20_000]}"
    )
    evidence = EvidenceIn(
        evidence_type=EvidenceType.code_change,
        title=f"PR #{pr.number}: {pr.title}"[:200],
        content=content,
        external_ref=pr.url,
        dated=pr.merged_at.date(),
        artifact_uris=[pr.url, f"https://github.com/{pr.repo}/commit/{pr.merge_sha}"],
    )
    facts = {} if ci.green is None else {"ci_green": ci.green}
    return evidence, facts
