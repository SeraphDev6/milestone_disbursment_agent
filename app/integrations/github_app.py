"""The GitHub App: installations instead of hand-made repo webhooks, and short-lived per-installation tokens.

- The app authenticates as itself with a JWT signed by its private key (RS256, 9 minutes), and exchanges it for an
  installation token (1 hour, cached) to act on the repos an installation was given.
- Installing uses "request user authorization during installation": GitHub sends the installer back with an OAuth
  code, and we check with *their* token that they can access the installation before it's linked to an org, so a
  forged `installation_id` can't claim someone else's installation.
- Mock mode without app credentials uses MockGitHubApp: an install page on this server and canned repos.

The app's permissions (set in the manifest, scripts/github_app_setup.py): metadata read; contents, pull requests and
checks write. Write access to contents and pull requests is only used to hand work over: a branch and a PR into the
client's repo once a tranche is paid. Events: installation, pull_request, release, push, repository (transfers).
"""

import hashlib
import io
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from typing import Protocol
from urllib.parse import quote, urlencode

import httpx
import jwt

from app.integrations import gittree
from app.integrations.github import GitHubClient, GitHubError, GitHubWriter

CHECK_NAME = "Milestone Ledger"


@dataclass
class Installation:
    id: int
    account_login: str
    account_type: str  # User or Organization


class GitHubApp(Protocol):
    slug: str
    webhook_secret: str

    def transfer(self, repo: str, new_owner: str) -> str:
        """The owner transfers `repo` to `new_owner`: its id, history and commits move, it leaves every installation
        and joins the new owner's (as with "all repositories"). Returns the new full name."""
        self.describe(repo)
        new = f"{new_owner}/{repo.split('/', 1)[1]}"
        self.repo_ids[new] = self.repo_ids.pop(repo)
        for table in (self.history, self.repo_meta):
            if repo in table:
                table[new] = table.pop(repo)
        for table in (self.commits, self.tarballs):
            for r, ref in [k for k in table if k[0] == repo]:
                table[(new, ref)] = table.pop((r, ref))
        for iid, names in self.repo_lists.items():
            names[:] = [n for n in names if n != repo]
            if self.installs[iid].account_login.lower() == new_owner.lower():
                names.append(new)
        return new

    def install_url(self, state: str) -> str: ...
    def exchange_code(self, code: str) -> str: ...
    def user_installation_ids(self, user_token: str) -> set[int]: ...
    def installation(self, installation_id: int) -> Installation: ...
    def repos(self, installation_id: int) -> list[str]: ...
    def client(self, installation_id: int) -> GitHubWriter: ...
    def upsert_check_run(self, installation_id: int, repo: str, check_run_id: int | None, body: dict) -> int: ...


def _pem(key: str) -> str:
    return key.replace("\\n", "\n").strip() + "\n"


class RealGitHubApp:
    def __init__(
        self,
        *,
        app_id: str,
        slug: str,
        client_id: str,
        client_secret: str,
        private_key: str,
        webhook_secret: str,
        api_url: str = "https://api.github.com",
        web_url: str = "https://github.com",
        transport: httpx.BaseTransport | None = None,
        clock=time.time,
    ):
        self.app_id, self.slug, self.webhook_secret = app_id, slug, webhook_secret
        self._client_id, self._client_secret, self._key = client_id, client_secret, _pem(private_key)
        self._api_url, self._web_url, self._transport, self._clock = api_url, web_url, transport, clock
        self._http = httpx.Client(timeout=10.0, transport=transport)
        self._tokens: dict[int, tuple[str, float]] = {}

    # --- authentication ------------------------------------------------------------------

    def app_jwt(self) -> str:
        now = int(self._clock())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id}, self._key, algorithm="RS256")

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _call(self, method: str, path: str, token: str, **kwargs) -> dict:
        try:
            r = self._http.request(method, self._api_url + path, headers=self._headers(token), **kwargs)
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub unreachable: {e}") from e
        if r.status_code >= 300:
            raise GitHubError(f"GitHub {r.status_code} for {method} {path}")
        return r.json() if r.content else {}

    def installation_token(self, installation_id: int) -> str:
        token, expires = self._tokens.get(installation_id, ("", 0.0))
        if token and expires - self._clock() > 300:
            return token
        data = self._call("POST", f"/app/installations/{installation_id}/access_tokens", self.app_jwt())
        expires_at = data.get("expires_at", "")
        try:
            exp = datetime.fromisoformat(expires_at).timestamp()
        except ValueError:
            exp = self._clock() + 3000
        self._tokens[installation_id] = (data["token"], exp)
        return data["token"]

    # --- installing ----------------------------------------------------------------------

    def transfer(self, repo: str, new_owner: str) -> str:
        """The owner transfers `repo` to `new_owner`: its id, history and commits move, it leaves every installation
        and joins the new owner's (as with "all repositories"). Returns the new full name."""
        self.describe(repo)
        new = f"{new_owner}/{repo.split('/', 1)[1]}"
        self.repo_ids[new] = self.repo_ids.pop(repo)
        for table in (self.history, self.repo_meta):
            if repo in table:
                table[new] = table.pop(repo)
        for table in (self.commits, self.tarballs):
            for r, ref in [k for k in table if k[0] == repo]:
                table[(new, ref)] = table.pop((r, ref))
        for iid, names in self.repo_lists.items():
            names[:] = [n for n in names if n != repo]
            if self.installs[iid].account_login.lower() == new_owner.lower():
                names.append(new)
        return new

    def install_url(self, state: str) -> str:
        return f"{self._web_url}/apps/{quote(self.slug)}/installations/new?{urlencode({'state': state})}"

    def exchange_code(self, code: str) -> str:
        try:
            r = self._http.post(
                f"{self._web_url}/login/oauth/access_token",
                json={"client_id": self._client_id, "client_secret": self._client_secret, "code": code},
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise GitHubError(f"GitHub unreachable: {e}") from e
        token = r.json().get("access_token") if r.status_code == 200 else None
        if not token:
            raise GitHubError("GitHub didn't accept the authorization code; start the install again")
        return token

    def user_installation_ids(self, user_token: str) -> set[int]:
        ids: set[int] = set()
        for page in range(1, 11):
            data = self._call("GET", "/user/installations", user_token, params={"per_page": 100, "page": page})
            batch = data.get("installations", [])
            ids |= {int(i["id"]) for i in batch}
            if len(batch) < 100:
                break
        return ids

    def installation(self, installation_id: int) -> Installation:
        data = self._call("GET", f"/app/installations/{installation_id}", self.app_jwt())
        account = data.get("account") or {}
        return Installation(installation_id, account.get("login", "unknown"), account.get("type", "User"))

    # --- acting for an installation ------------------------------------------------------

    def repos(self, installation_id: int) -> list[str]:
        token = self.installation_token(installation_id)
        names: list[str] = []
        for page in range(1, 11):
            data = self._call("GET", "/installation/repositories", token, params={"per_page": 100, "page": page})
            batch = data.get("repositories", [])
            names += [r["full_name"] for r in batch]
            if len(batch) < 100:
                break
        return sorted(names)

    def client(self, installation_id: int) -> GitHubWriter:
        return GitHubClient(
            token=self.installation_token(installation_id), base_url=self._api_url, transport=self._transport
        )

    def upsert_check_run(self, installation_id: int, repo: str, check_run_id: int | None, body: dict) -> int:
        token = self.installation_token(installation_id)
        if check_run_id:
            data = self._call("PATCH", f"/repos/{repo}/check-runs/{check_run_id}", token, json=body)
        else:
            data = self._call("POST", f"/repos/{repo}/check-runs", token, json=body)
        return int(data["id"])


# --- mock --------------------------------------------------------------------------------


class MockGitHubClient:
    """What an installation token can read and write, in mock mode. State lives on the MockGitHubApp."""

    def __init__(self, app: "MockGitHubApp", installation_id: int):
        self._app, self._iid = app, installation_id

    def _visible(self, repo: str) -> None:
        if repo not in self._app.repo_lists.get(self._iid, []):
            raise GitHubError(f"GitHub 404 for /repos/{repo}")

    def check_runs(self, repo: str, sha: str) -> list[dict]:
        return [{"name": "tests", "status": "completed", "conclusion": "success"}]

    def pr_files(self, repo: str, number: int) -> list[dict]:
        return [{"filename": "README.md", "additions": 1, "deletions": 0}]

    def commit(self, repo: str, ref: str) -> dict:
        default = self._app.repo_meta.get(repo, {}).get("default_branch", "main")
        if (repo, ref) not in self._app.commits and ref == default and repo in self._app.repo_lists.get(self._iid, []):
            self._app.commits[(repo, ref)] = (hashlib.sha1(repo.encode()).hexdigest(), gittree.tree_id([]))
        if (repo, ref) not in self._app.commits:
            raise GitHubError(f"GitHub 404 for /repos/{repo}/commits/{ref}")
        sha, tree = self._app.commits[(repo, ref)]
        return {"sha": sha, "commit": {"tree": {"sha": tree}, "message": f"Work for {ref}"}}

    def repo(self, repo: str) -> dict:
        self._visible(repo)
        return self._app.describe(repo)

    def repository(self, repo_id: int) -> dict:
        for name, rid in self._app.repo_ids.items():
            if rid == repo_id and name in self._app.repo_lists.get(self._iid, []):
                return self._app.describe(name)
        raise GitHubError(f"GitHub 404 for /repositories/{repo_id}")

    def compare(self, repo: str, base: str, head: str) -> str:
        self._visible(repo)
        if (repo, base) not in self._app.commits:
            raise GitHubError(f"GitHub 404 for /repos/{repo}/compare/{base}...{head}")
        history = self._app.history.get(repo, [])
        if base not in history:
            return "diverged"
        return "identical" if history[-1] == base else "ahead"

    def tarball(self, repo: str, sha: str) -> bytes:
        self._visible(repo)
        if (repo, sha) not in self._app.tarballs:
            raise GitHubError(f"GitHub 404 for the archive of {repo}@{sha[:12]}")
        return self._app.tarballs[(repo, sha)]

    def create_blob(self, repo: str, data: bytes) -> str:
        self._visible(repo)
        oid = gittree.blob_id(data)
        self._app.blobs[oid] = data
        return oid

    def create_tree(self, repo: str, entries: list[dict]) -> str:
        self._visible(repo)
        files = [gittree.Entry(e["path"], e["mode"], self._app.blobs[e["sha"]]) for e in entries]
        return gittree.tree_id(files)

    def create_commit(self, repo: str, message: str, tree: str, parents: list[str]) -> str:
        self._visible(repo)
        sha = hashlib.sha1(f"{tree}{parents}{message}".encode()).hexdigest()  # stand-in id
        self._app.commits[(repo, sha)] = (sha, tree)
        return sha

    def create_ref(self, repo: str, ref: str, sha: str) -> None:
        self._visible(repo)
        if (repo, ref) in self._app.refs:
            raise GitHubError("GitHub 422 for POST /git/refs: Reference already exists")
        self._app.refs[(repo, ref)] = sha

    def open_pull(self, repo: str, title: str, head: str, base: str, body: str) -> str:
        self._visible(repo)
        self._app.pulls.append({"repo": repo, "title": title, "head": head, "base": base, "body": body})
        return f"https://github.com/{repo}/pull/{len(self._app.pulls)}"


@dataclass
class MockGitHubApp:
    """Stand-in app for mock mode and tests. `install` simulates someone installing it on an account."""

    base_url: str
    slug: str = "milestone-ledger-mock"
    webhook_secret: str = "mock-github-app-webhook-secret"
    installs: dict[int, Installation] = field(default_factory=dict)
    repo_lists: dict[int, list[str]] = field(default_factory=dict)
    codes: dict[str, int] = field(default_factory=dict)  # OAuth code -> installation the user can access
    commits: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)  # (repo, ref) -> (sha, tree)
    check_runs: dict[int, dict] = field(default_factory=dict)
    repo_meta: dict[str, dict] = field(default_factory=dict)  # e.g. {"private": False}
    tarballs: dict[tuple[str, str], bytes] = field(default_factory=dict)
    blobs: dict[str, bytes] = field(default_factory=dict)
    refs: dict[tuple[str, str], str] = field(default_factory=dict)
    pulls: list[dict] = field(default_factory=list)
    repo_ids: dict[str, int] = field(default_factory=dict)
    history: dict[str, list[str]] = field(default_factory=dict)  # commits on each repo's default branch, oldest first
    _ids: count = field(default_factory=lambda: count(1001))

    def install(self, account: str, repos: list[str]) -> tuple[int, str]:
        """Someone installs the app on `account`; returns (installation id, the OAuth code GitHub would send)."""
        iid = next(self._ids)
        self.installs[iid] = Installation(iid, account, "Organization")
        self.repo_lists[iid] = sorted(repos)
        code = f"mock-code-{iid}"
        self.codes[code] = iid
        return iid, code

    def describe(self, repo: str) -> dict:
        rid = self.repo_ids.setdefault(repo, 500000 + len(self.repo_ids))
        meta = {"private": True, "default_branch": "main", **self.repo_meta.get(repo, {})}
        return {"id": rid, "full_name": repo, "owner": {"login": repo.split("/", 1)[0]}, **meta}

    def push(
        self, repo: str, files: dict[str, bytes], tag: str | None = None, on_default: bool = True
    ) -> tuple[str, str]:
        """Commit `files` to `repo` (and tag it): what a contractor pushing work would leave. Returns (sha, tree).
        on_default=False leaves the commit off the default branch (e.g. only on a tag)."""
        entries = [gittree.Entry(path, "100644", data) for path, data in files.items()]
        tree = gittree.tree_id(entries)
        sha = hashlib.sha1(f"{repo}{tree}{tag}".encode()).hexdigest()
        self.commits[(repo, sha)] = (sha, tree)
        if on_default:
            self.history.setdefault(repo, []).append(sha)
        if tag:
            self.commits[(repo, tag)] = (sha, tree)
        buf = io.BytesIO()
        prefix = f"{repo.replace('/', '-')}-{sha[:7]}/"
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for e in entries:
                info = tarfile.TarInfo(prefix + e.path)
                info.size, info.mode = len(e.data), 0o644
                tar.addfile(info, io.BytesIO(e.data))
        self.tarballs[(repo, sha)] = buf.getvalue()
        return sha, tree

    def transfer(self, repo: str, new_owner: str) -> str:
        """The owner transfers `repo` to `new_owner`: its id, history and commits move, it leaves every installation
        and joins the new owner's (as with "all repositories"). Returns the new full name."""
        self.describe(repo)
        new = f"{new_owner}/{repo.split('/', 1)[1]}"
        self.repo_ids[new] = self.repo_ids.pop(repo)
        for table in (self.history, self.repo_meta):
            if repo in table:
                table[new] = table.pop(repo)
        for table in (self.commits, self.tarballs):
            for r, ref in [k for k in table if k[0] == repo]:
                table[(new, ref)] = table.pop((r, ref))
        for iid, names in self.repo_lists.items():
            names[:] = [n for n in names if n != repo]
            if self.installs[iid].account_login.lower() == new_owner.lower():
                names.append(new)
        return new

    def install_url(self, state: str) -> str:
        return f"{self.base_url}/mock/github/install?{urlencode({'state': state})}"

    def exchange_code(self, code: str) -> str:
        if code not in self.codes:
            raise GitHubError("GitHub didn't accept the authorization code; start the install again")
        return f"mock-user-token-{self.codes.pop(code)}"

    def user_installation_ids(self, user_token: str) -> set[int]:
        return {int(user_token.rsplit("-", 1)[1])} if user_token.startswith("mock-user-token-") else set()

    def installation(self, installation_id: int) -> Installation:
        if installation_id not in self.installs:
            raise GitHubError(f"GitHub 404 for /app/installations/{installation_id}")
        return self.installs[installation_id]

    def repos(self, installation_id: int) -> list[str]:
        return list(self.repo_lists.get(installation_id, []))

    def client(self, installation_id: int) -> GitHubWriter:
        return MockGitHubClient(self, installation_id)

    def upsert_check_run(self, installation_id: int, repo: str, check_run_id: int | None, body: dict) -> int:
        rid = check_run_id or len(self.check_runs) + 1
        self.check_runs[rid] = {
            **self.check_runs.get(rid, {}),
            **body,
            "repo": repo,
            "installation_id": installation_id,
        }
        return rid


def make_github_app(settings) -> GitHubApp | None:
    if settings.github_app_configured:
        return RealGitHubApp(
            app_id=settings.github_app_id,
            slug=settings.github_app_slug,
            client_id=settings.github_app_client_id,
            client_secret=settings.github_app_client_secret.get_secret_value(),
            private_key=settings.github_app_private_key.get_secret_value(),
            webhook_secret=settings.github_app_webhook_secret.get_secret_value(),
        )
    if settings.is_mock:
        return MockGitHubApp(base_url=settings.api_base_url)
    return None
