"""GitHub adapter: a merged pull request labelled with a milestone, or a release or tag named for one ->
code_change evidence.

The payee registers their repo on the agreement (through the GitHub App, or with a repo webhook we hand back).
Matching is explicit: a PR counts for a milestone only when it carries that milestone's label, and a release or tag
only when its name starts with the label. Anything else is logged and dropped, never guessed.

Two modes (Integration.mode):
- merged_pr: PRs merged into the registered repo. The client usually owns that repo, so it has the code at merge;
  for agreements whose work is released on submit.
- contractor_repo: the work stays in the contractor's private repo, evidence is a pinned commit, and the code is
  handed over (a PR into the client's repo, a source archive, or the contractor transferring the repo to the client)
  only once the tranche is captured.

Facts this adapter vouches for (and a payee can't assert through the API):
- the merge commit SHA (pinned_ref), so later pushes can't change what was verified;
- ci_green: every check run on the PR's head commit completed successfully.
"""

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

from app.models import EvidenceCreate, EvidenceType

LABEL_PREFIX = "mde-"
PASSING = {"success", "neutral", "skipped"}
MAX_FILES_LISTED = 100
_REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
_SHA = re.compile(r"[0-9a-f]{40}")


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
    def commit(self, repo: str, ref: str) -> dict: ...


class GitHubWriter(GitHubAPI, Protocol):
    """What a handover needs: read the contractor's repo, write a branch and a PR into the client's, or find the repo
    after it was transferred to the client."""

    def repo(self, repo: str) -> dict: ...
    def tarball(self, repo: str, sha: str) -> bytes: ...
    def create_blob(self, repo: str, data: bytes) -> str: ...
    def create_tree(self, repo: str, entries: list[dict]) -> str: ...
    def create_commit(self, repo: str, message: str, tree: str, parents: list[str]) -> str: ...
    def create_ref(self, repo: str, ref: str, sha: str) -> None: ...
    def open_pull(self, repo: str, title: str, head: str, base: str, body: str) -> str: ...
    def repository(self, repo_id: int) -> dict: ...
    def compare(self, repo: str, base: str, head: str) -> str: ...


class GitHubClient:
    def __init__(
        self, token: str | None = None, base_url: str = "https://api.github.com", timeout: float = 10.0, transport=None
    ):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout, transport=transport)

    def _get(self, path: str) -> dict | list:
        try:
            r = self._client.get(path, params={"per_page": 100})
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub unreachable: {e}") from e
        if r.status_code != 200:
            raise GitHubError(f"GitHub {r.status_code} for {path}")
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = self._client.post(path, json=body)
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub unreachable: {e}") from e
        if r.status_code not in (200, 201):
            raise GitHubError(f"GitHub {r.status_code} for POST {path}: {r.text[:200]}")
        return r.json()

    def repo(self, repo: str) -> dict:
        """{'private': bool, 'default_branch': str, ...}"""
        if not valid_repo(repo):
            raise GitHubError("invalid repo")
        return self._get(f"/repos/{repo}")  # type: ignore[return-value]

    def tarball(self, repo: str, sha: str) -> bytes:
        if not valid_repo(repo) or not _SHA.fullmatch(sha):
            raise GitHubError("invalid repo or commit")
        try:
            r = self._client.get(f"/repos/{repo}/tarball/{sha}", follow_redirects=True, timeout=120.0)
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub unreachable: {e}") from e
        if r.status_code != 200:
            raise GitHubError(f"GitHub {r.status_code} for the archive of {repo}@{sha[:12]}")
        return r.content

    def repository(self, repo_id: int) -> dict:
        """A repo by its id, which survives renames and transfers. 404 when this token can't read it."""
        return self._get(f"/repositories/{int(repo_id)}")  # type: ignore[return-value]

    def compare(self, repo: str, base: str, head: str) -> str:
        """identical, ahead (head contains base), behind or diverged."""
        if not valid_repo(repo) or not _SHA.fullmatch(base) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", head):
            raise GitHubError("invalid repo or ref")
        if ".." in head:
            raise GitHubError("invalid ref")
        r = self._get(f"/repos/{repo}/compare/{base}...{head}")
        return str(r.get("status"))  # type: ignore[union-attr]

    def create_blob(self, repo: str, data: bytes) -> str:
        return self._post(
            f"/repos/{repo}/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"}
        )["sha"]

    def create_tree(self, repo: str, entries: list[dict]) -> str:
        return self._post(f"/repos/{repo}/git/trees", {"tree": entries})["sha"]

    def create_commit(self, repo: str, message: str, tree: str, parents: list[str]) -> str:
        return self._post(f"/repos/{repo}/git/commits", {"message": message, "tree": tree, "parents": parents})["sha"]

    def create_ref(self, repo: str, ref: str, sha: str) -> None:
        self._post(f"/repos/{repo}/git/refs", {"ref": ref, "sha": sha})

    def open_pull(self, repo: str, title: str, head: str, base: str, body: str) -> str:
        return self._post(f"/repos/{repo}/pulls", {"title": title, "head": head, "base": base, "body": body})[
            "html_url"
        ]

    def check_runs(self, repo: str, sha: str) -> list[dict]:
        return self._get(f"/repos/{repo}/commits/{sha}/check-runs").get("check_runs", [])  # type: ignore[union-attr]

    def pr_files(self, repo: str, number: int) -> list[dict]:
        return self._get(f"/repos/{repo}/pulls/{number}/files")  # type: ignore[return-value]

    def commit(self, repo: str, ref: str) -> dict:
        """The commit a ref points to: {'sha': ..., 'commit': {'tree': {'sha': ...}}, ...}."""
        if not valid_repo(repo) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", ref) or ".." in ref:
            raise GitHubError("invalid repo or ref")
        return self._get(f"/repos/{repo}/commits/{ref}")  # type: ignore[return-value]


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


def build_evidence(pr: MergedPR, api: GitHubAPI) -> tuple[EvidenceCreate, dict[str, bool]]:
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
    evidence = EvidenceCreate(
        evidence_type=EvidenceType.code_change,
        title=f"PR #{pr.number}: {pr.title}"[:200],
        content=content,
        external_ref=pr.url,
        dated=pr.merged_at.date(),
        artifact_uris=[pr.url, f"https://github.com/{pr.repo}/commit/{pr.merge_sha}"],
    )
    facts = {} if ci.green is None else {"ci_green": ci.green}
    return evidence, facts


# --- contractor repos: a release or tag pins a commit ----------------------------------


@dataclass
class TaggedCommit:
    """A release published, or a tag pushed, in the contractor's repo. The commit is looked up from the tag."""

    repo: str
    tag: str
    title: str
    body: str  # release notes (written by the payee); empty for a bare tag
    url: str
    author: str
    at: datetime

    def milestone_ids(self, candidates) -> list[str]:
        """Milestones whose label the tag starts with: `mde-<id>`, `mde-<id>-v2`, `mde-<id>.1`."""
        out = []
        for mid in candidates:
            label = label_for(mid)
            if self.tag == label or self.tag.startswith((label + "-", label + ".", label + "+")):
                out.append(mid)
        return out


def parse_tagged_commit(event: str | None, payload: dict, now: datetime) -> TaggedCommit | None:
    """`release` published, or `push` creating a tag. Drafts and deleted tags don't count."""
    repo = (payload.get("repository") or {}).get("full_name")
    if not repo:
        return None
    if event == "release" and payload.get("action") == "published":
        rel = payload.get("release") or {}
        if rel.get("draft") or not rel.get("tag_name"):
            return None
        at = rel.get("published_at")
        return TaggedCommit(
            repo=repo,
            tag=rel["tag_name"],
            title=rel.get("name") or rel["tag_name"],
            body=rel.get("body") or "",
            url=rel.get("html_url") or f"https://github.com/{repo}/releases/tag/{rel['tag_name']}",
            author=(rel.get("author") or {}).get("login", "unknown"),
            at=datetime.fromisoformat(at) if at else now,
        )
    if event == "push" and str(payload.get("ref", "")).startswith("refs/tags/") and not payload.get("deleted"):
        tag = payload["ref"].removeprefix("refs/tags/")
        return TaggedCommit(
            repo=repo,
            tag=tag,
            title=tag,
            body="",
            url=f"https://github.com/{repo}/tree/{tag}",
            author=(payload.get("sender") or {}).get("login") or (payload.get("pusher") or {}).get("name", "unknown"),
            at=now,
        )
    return None


_PREVIEW = re.compile(r"^\s*preview\s*:\s*(https://\S+)", re.IGNORECASE | re.MULTILINE)


def preview_url(text: str) -> str | None:
    """A 'Preview: https://...' line in the release notes or PR description: the deploy the payer can try."""
    m = _PREVIEW.search(text or "")
    return m.group(1)[:500] if m else None


def build_commit_evidence(tc: TaggedCommit, api: GitHubAPI) -> tuple[EvidenceCreate, dict[str, bool], str]:
    """Evidence for the commit a tag points to. Returns (evidence, facts, commit sha). The repo stays private; the
    payer is shown the commit, tree, CI and the preview link, not the source."""
    c = api.commit(tc.repo, tc.tag)
    sha, tree = c["sha"].lower(), c["commit"]["tree"]["sha"]
    message = ((c.get("commit") or {}).get("message") or "")[:4000]
    ci = ci_status(api, tc.repo, sha)
    preview = preview_url(tc.body)
    commit_url = f"https://github.com/{tc.repo}/commit/{sha}"
    content = (
        f"Tag '{tc.tag}' in the contractor's repo {tc.repo}, by {tc.author} at {tc.at.isoformat()}.\n"
        f"Commit: {sha}\nTree: {tree}\nThe source stays in the contractor's repo until the tranche is paid; "
        f"it is then handed over and checked against this tree.\n\n"
        f"CI on the commit (checked by the engine): {ci.summary}\n\n"
        + (f"Preview deploy (given by the payee, not checked): {preview}\n\n" if preview else "")
        + f"Commit message:\n{message}\n\n"
        + (f"Release notes (written by the payee, not verified):\n{tc.body[:20_000]}" if tc.body else "")
    ).rstrip()
    evidence = EvidenceCreate(
        evidence_type=EvidenceType.code_change,
        title=f"{tc.title} ({sha[:12]})"[:200],
        content=content,
        external_ref=commit_url,
        dated=tc.at.date(),
        artifact_uris=[u for u in (preview, tc.url, commit_url) if u],
        preview_url=preview,
    )
    facts = {} if ci.green is None else {"ci_green": ci.green}
    return evidence, facts, sha
