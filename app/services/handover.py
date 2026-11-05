"""Handing a paid tranche's code from the contractor's private repo to the client (Integration.mode=contractor_repo).

When a tranche is captured (`settlement._reveal`), `queue` adds a GitHubHandover for the newest pinned commit from
each contractor repo. `run` works them, retrying with backoff:

1. Look for the repo under the client: the contractor may have transferred it to one of the client org's GitHub
   accounts (found by repo id through the client's installations). If they did and the verified commit is on its
   default branch, the client has exactly that code and the handover is done, whatever the method.
2. transfer: otherwise keep waiting (the contractor transfers when they're ready; never given up on).
3. Read the pinned commit and its source archive through the contractor's installation, and recompute the git tree
   id from the archive. It must equal the verified commit's tree, or nothing is handed over (hash commitment).
4. pull_request: write those exact files as a branch into the client's repo through the client's installation,
   check GitHub computed the same tree, and open a PR. archive: keep the archive here for the payer to download.

Money never waits on GitHub: capture only queues the row.
"""

import hashlib
import re
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.db import record_audit, utcnow
from app.integrations import gittree
from app.integrations.github import GitHubError
from app.integrations.github_app import GitHubApp
from app.models import (
    Evidence,
    GitHubHandover,
    GitHubHandoverRead,
    Integration,
    Milestone,
    Role,
    StoredFile,
)
from app.services import ServiceError
from app.services import github_app as installs
from app.services.notifications import notify_side, tranche_subject

MAX_ATTEMPTS = 8
ACTOR = "system:handover"
_SHA = re.compile(r"[0-9a-f]{40}")


class HandoverError(ServiceError):
    pass


class _Permanent(Exception):
    """Retrying won't help: the code doesn't match what was verified, or is too big to hand over this way."""


def _from_repo(e: Evidence, repo: str) -> bool:
    return e.source == "github" and e.external_ref.lower().startswith(f"https://github.com/{repo.lower()}/")


def queue(session: Session, m: Milestone, actor: str) -> list[GitHubHandover]:
    """The tranche was captured: hand over the newest verified commit from each contractor repo on the agreement."""
    rows = []
    integrations = session.exec(
        select(Integration).where(Integration.agreement_id == m.agreement_id, Integration.mode == "contractor_repo")
    ).all()
    for integration in integrations:
        pinned = [e for e in m.evidence if _from_repo(e, integration.target) and _SHA.fullmatch(e.pinned_ref)]
        if not pinned:
            continue
        ev = max(pinned, key=lambda e: e.received_at)
        exists = session.exec(
            select(GitHubHandover).where(GitHubHandover.milestone_id == m.id, GitHubHandover.evidence_id == ev.id)
        ).first()
        if exists is not None:
            continue
        row = GitHubHandover(
            milestone_id=m.id,
            evidence_id=ev.id,
            integration_id=integration.id,
            method=integration.handover or "archive",
            source_repo=integration.target,
            repo_id=integration.repo_id,
            pinned_sha=ev.pinned_ref,
            target_repo=integration.handover_repo if integration.handover == "pull_request" else None,
        )
        session.add(row)
        record_audit(
            session,
            m.agreement_id,
            actor,
            "handover_queued",
            milestone_id=m.id,
            detail={"method": row.method, "source_repo": row.source_repo, "pinned_sha": row.pinned_sha},
        )
        rows.append(row)
    return rows


def read(row: GitHubHandover, session: Session) -> GitHubHandoverRead:
    f = session.get(StoredFile, row.file_id) if row.file_id else None
    m = session.get(Milestone, row.milestone_id)
    accounts = sorted({i.account_login for i in installs.installations(session, _payer_orgs(m))}) if m else []
    return GitHubHandoverRead.model_validate(
        row, update={"archive_sha256": f.sha256 if f else None, "transfer_to": accounts}
    )


def _payer_orgs(m: Milestone) -> list[str]:
    return [p.org_id or "" for p in m.agreement.parties if p.role == Role.payer]


def poke(session: Session, repo_ids: list[int], now: datetime | None = None) -> int:
    """GitHub says these repos moved (transferred, or added to an installation): check their handovers now."""
    rows = session.exec(
        select(GitHubHandover).where(
            GitHubHandover.status == "pending",
            GitHubHandover.repo_id.in_(repo_ids),  # type: ignore[union-attr]
        )
    ).all()
    for row in rows:
        row.next_attempt_at = now or utcnow()
    session.commit()
    return len(rows)


def for_milestone(session: Session, milestone_id: str) -> list[GitHubHandover]:
    return list(
        session.exec(
            select(GitHubHandover)
            .where(GitHubHandover.milestone_id == milestone_id)
            .order_by(GitHubHandover.created_at)  # type: ignore[arg-type]
        ).all()
    )


def retry(session: Session, row: GitHubHandover, actor: str, now: datetime | None = None) -> GitHubHandover:
    """Try a failed or waiting handover again now, e.g. once the client installed the app on their repo."""
    if row.status == "delivered":
        raise HandoverError("this handover was already delivered", 409)
    row.status, row.next_attempt_at = "pending", now or utcnow()
    if row.attempts >= MAX_ATTEMPTS:
        row.attempts = MAX_ATTEMPTS - 1
    m = session.get(Milestone, row.milestone_id)
    record_audit(session, m.agreement_id, actor, "handover_retried", milestone_id=m.id, detail={"handover_id": row.id})
    session.commit()
    return row


# --- working the queue -----------------------------------------------------------------


def run(session: Session, app: GitHubApp | None, now: datetime | None = None, limit: int = 10) -> int:
    """Attempt every handover that's due. Returns how many were delivered."""
    if app is None:
        return 0
    now = now or utcnow()
    due = session.exec(
        select(GitHubHandover)
        .where(GitHubHandover.status == "pending", GitHubHandover.next_attempt_at <= now)
        .order_by(GitHubHandover.next_attempt_at)  # type: ignore[arg-type]
        .limit(limit)
    ).all()
    delivered = 0
    for row in due:
        delivered += _attempt(session, app, row, now)
        session.commit()
    return delivered


def _attempt(session: Session, app: GitHubApp, row: GitHubHandover, now: datetime) -> bool:
    m = session.get(Milestone, row.milestone_id)
    integration = session.get(Integration, row.integration_id)
    row.attempts += 1
    try:
        if (integration is None or integration.installation_id is None) and row.method != "transfer":
            raise GitHubError("the contractor's repo is no longer connected through the GitHub App")
        if _transferred(session, app, m, row):
            return _done(session, m, row, now)
        if row.method == "transfer":
            raise GitHubError(f"{row.source_repo} hasn't been transferred to the client yet")
        src = app.client(integration.installation_id)
        tree = src.commit(row.source_repo, row.pinned_sha)["commit"]["tree"]["sha"]
        archive = src.tarball(row.source_repo, row.pinned_sha)
        try:
            entries = gittree.untar(archive)
        except gittree.ArchiveError as e:
            raise _Permanent(str(e)) from e
        got = gittree.tree_id(entries)
        if got != tree:
            _mismatch(session, m, row, tree, got, "archive")
            raise _Permanent(
                f"the archive of {row.pinned_sha[:12]} doesn't reproduce the verified tree (submodules, or files "
                "left out of archives by .gitattributes, can cause this); nothing was handed over"
            )
        row.tree_sha = tree
        if row.method == "pull_request":
            row.pr_url = _open_pr(session, app, m, row, entries, tree)
        else:
            row.file_id = _keep_archive(session, row, archive)
    except _Permanent as e:
        return _give_up(session, m, row, str(e))
    except GitHubError as e:
        row.error = str(e)[:500]
        if row.attempts >= MAX_ATTEMPTS and row.method != "transfer":
            return _give_up(session, m, row, row.error)
        if row.attempts == 1:
            _tell_waiting(session, m, row)
        row.next_attempt_at = now + timedelta(minutes=min(2 ** min(row.attempts, 9), 360))
        return False
    return _done(session, m, row, now)


def _done(session: Session, m: Milestone, row: GitHubHandover, now: datetime) -> bool:
    row.status, row.error, row.delivered_at = "delivered", None, now
    _delivered(session, m, row)
    return True


def _transferred(session: Session, app: GitHubApp, m: Milestone, row: GitHubHandover) -> bool:
    """Whether the contractor transferred the repo to one of the client's GitHub accounts, with the verified commit
    on its default branch. An installation only reads repos its own account owns, so finding the repo (by id)
    through a client installation means the client owns it. A commit id covers its whole tree, so the same commit
    is the same code; no archive needs checking."""
    if row.repo_id is None:
        return False
    for inst in installs.installations(session, _payer_orgs(m)):
        dst = app.client(inst.installation_id)
        try:
            info = dst.repository(row.repo_id)
        except GitHubError:
            continue  # not owned by (or not shared with) this installation's account
        owner = str((info.get("owner") or {}).get("login") or "")
        if owner.lower() != inst.account_login.lower():
            continue
        name, branch = info["full_name"], info.get("default_branch") or "main"
        try:
            status = dst.compare(name, row.pinned_sha, branch)
        except GitHubError as e:
            raise GitHubError(
                f"{row.source_repo} was transferred to {owner} as {name}, but the verified commit "
                f"{row.pinned_sha[:12]} isn't in it ({e})"
            ) from e
        if status not in ("identical", "ahead"):
            raise GitHubError(
                f"{row.source_repo} was transferred to {owner} as {name}, but the verified commit "
                f"{row.pinned_sha[:12]} isn't on its default branch ({branch}); push or merge it there"
            )
        row.transferred_to = name
        row.target_repo = row.target_repo or name
        row.tree_sha = dst.commit(name, row.pinned_sha)["commit"]["tree"]["sha"]
        return True
    return False


def _open_pr(
    session: Session, app: GitHubApp, m: Milestone, row: GitHubHandover, entries: list[gittree.Entry], tree: str
) -> str:
    target = row.target_repo or ""
    iid = installs.installation_for_repo(app, installs.installations(session, _payer_orgs(m)), target)
    if iid is None:
        raise GitHubError(f"the client's organization needs the GitHub App installed on {target} to receive the PR")
    dst = app.client(iid)
    base = dst.repo(target).get("default_branch") or "main"
    parent = dst.commit(target, base)["sha"]
    blobs = [{"path": e.path, "mode": e.mode, "type": "blob", "sha": dst.create_blob(target, e.data)} for e in entries]
    written = dst.create_tree(target, blobs)
    if written != tree:
        _mismatch(session, m, row, tree, written, "push")
        raise _Permanent(f"GitHub stored a different tree in {target} than the one verified; no PR was opened")
    message = f"{m.title}\n\nHanded over by Milestone Ledger once the tranche was paid.\nVerified commit: " + (
        f"{row.source_repo}@{row.pinned_sha} (tree {tree})"
    )
    commit = dst.create_commit(target, message, tree, [parent])
    branch = f"milestone-ledger/{m.id[:8]}-{row.id[:6]}"
    try:
        dst.create_ref(target, f"refs/heads/{branch}", commit)
    except GitHubError as e:
        if "already exists" not in str(e).lower():  # left by an earlier attempt that failed before the PR
            raise
    body = (
        f"The work for **{m.title}** under *{m.agreement.title}*, handed over now that the tranche is paid.\n\n"
        f"- Verified commit: `{row.pinned_sha}` in the contractor's repo\n"
        f"- Tree: `{tree}`. This branch has exactly that tree, so it is the code that was verified.\n\n"
        "Merging is up to you; the branch is based on the current default branch."
    )
    return dst.open_pull(target, f"Handover: {m.title}"[:250], branch, base, body)


def _keep_archive(session: Session, row: GitHubHandover, archive: bytes) -> str:
    digest = hashlib.sha256(archive).hexdigest()
    f = session.exec(select(StoredFile).where(StoredFile.sha256 == digest)).first()
    if f is None:
        name = row.source_repo.split("/", 1)[1]
        f = StoredFile(
            filename=f"{name}-{row.pinned_sha[:12]}.tar.gz",
            media_type="application/gzip",
            sha256=digest,
            size=len(archive),
            data=archive,
        )
        session.add(f)
        session.flush()
    return f.id


def _mismatch(session: Session, m: Milestone, row: GitHubHandover, expected: str, got: str, stage: str) -> None:
    record_audit(
        session,
        m.agreement_id,
        ACTOR,
        "deliverable_mismatch",
        milestone_id=m.id,
        detail={"handover_id": row.id, "stage": stage, "expected_tree": expected, "got_tree": got},
    )


def _give_up(session: Session, m: Milestone, row: GitHubHandover, why: str) -> bool:
    row.status, row.error = "failed", why[:500]
    record_audit(
        session, m.agreement_id, ACTOR, "handover_failed", milestone_id=m.id, detail={"handover_id": row.id, "why": why}
    )
    for role in (Role.payer, Role.payee):
        notify_side(
            session,
            m,
            role,
            tranche_subject(m, "Handover failed"),
            f"The code for '{m.title}' couldn't be handed over from {row.source_repo}: {why}. The tranche is paid; "
            "once the cause is fixed either side can retry the handover from the tranche.",
            tone="alert",
        )
    return False


def _tell_waiting(session: Session, m: Milestone, row: GitHubHandover) -> None:
    if row.method == "transfer":
        accounts = sorted({i.account_login for i in installs.installations(session, _payer_orgs(m))})
        where = " or ".join(accounts) if accounts else "the client's GitHub account (they need the GitHub App on it)"
        notify_side(
            session,
            m,
            Role.payee,
            tranche_subject(m, "Transfer the repo"),
            f"'{m.title}' is paid. To hand the code over, transfer {row.source_repo} to {where} (GitHub: Settings > "
            f"Transfer ownership), with commit {row.pinned_sha[:12]} on its default branch. We'll see the transfer "
            "and record the handover. The client gets the whole repo, including work for tranches not yet paid.",
            tone="review",
        )
        notify_side(
            session,
            m,
            Role.payer,
            tranche_subject(m, "Handover waiting"),
            f"'{m.title}' is paid. The contractor hands the code over by transferring {row.source_repo} to {where}; "
            "we'll tell you when it arrives.",
            tone="review",
        )
        return
    notify_side(
        session,
        m,
        Role.payer,
        tranche_subject(m, "Handover waiting"),
        f"'{m.title}' is paid for and its code is ready to hand over, but it hasn't reached you yet: {row.error}. "
        "It will be retried automatically.",
        tone="review",
    )


def _delivered(session: Session, m: Milestone, row: GitHubHandover) -> None:
    if row.transferred_to:
        where = f"the repo itself, now {row.transferred_to} under your GitHub account"
    elif row.pr_url:
        where = f"a pull request into {row.target_repo}: {row.pr_url}"
    else:
        where = "a source archive to download"
    record_audit(
        session,
        m.agreement_id,
        ACTOR,
        "deliverable_handed_over",
        milestone_id=m.id,
        detail={
            "handover_id": row.id,
            "method": row.method,
            "source_repo": row.source_repo,
            "pinned_sha": row.pinned_sha,
            "tree_sha": row.tree_sha,
            "target_repo": row.target_repo,
            "pr_url": row.pr_url,
            "transferred_to": row.transferred_to,
            "matches": True,
        },
    )
    notify_side(
        session,
        m,
        Role.payer,
        tranche_subject(m, "Code handed over"),
        f"The code for '{m.title}' was handed over as {where}. It was checked against the tree of commit "
        f"{row.pinned_sha[:12]} that was verified, and matched.",
        link=row.pr_url or (f"https://github.com/{row.transferred_to}" if row.transferred_to else None),
        tone="settled",
    )
