"""GitHub App installations linked to our organizations, GitHub reads through them, and PR check runs.

Linking an installation to an org takes two steps, so neither a forged installation id nor a link someone else
started can attach a GitHub account to the wrong org:
1. Browser callback (no bearer token): the GitHub user who installed proves, with their own OAuth token, that they
   can access the installation. We hand back a short-lived encrypted *claim*.
2. The signed-in admin who started the install (checked against the encrypted state) confirms the claim for their
   org from the web app.

Check runs follow a reconciliation loop (`publish_checks`): each GitHubCheck remembers the tranche status it last
showed, and the loop posts whatever changed. Money and verification code never wait on GitHub.
"""

import json
import secrets as pysecrets
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db import utcnow
from app.integrations.crypto import SecretBox
from app.integrations.github import GitHubAPI, GitHubError
from app.integrations.github_app import CHECK_NAME, GitHubApp
from app.models import GitHubCheck, GitHubInstallation, Integration, Milestone, MilestoneStatus, Role, WebhookEvent
from app.services import ServiceError
from app.services.access import Principal

STATE_TTL = 30 * 60  # seconds a started install, or a claim, stays usable


class GitHubAppError(ServiceError):
    pass


def _seal(box: SecretBox, payload: dict) -> str:
    return box.encrypt(json.dumps({**payload, "t": int(utcnow().timestamp())}, separators=(",", ":")))


def _open(box: SecretBox, token: str, what: str) -> dict:
    try:
        data = json.loads(box.decrypt(token))
    except (ValueError, TypeError) as e:
        raise GitHubAppError(f"this {what} isn't valid; start connecting GitHub again", 400) from e
    if utcnow().timestamp() - data.get("t", 0) > STATE_TTL:
        raise GitHubAppError(f"this {what} expired; start connecting GitHub again", 400)
    return data


# --- installing --------------------------------------------------------------------------


def start_install(app: GitHubApp, box: SecretBox, org_id: str, principal: Principal) -> str:
    user = principal.require_person("connect GitHub")
    return app.install_url(_seal(box, {"o": org_id, "u": user.id, "n": pysecrets.token_urlsafe(12)}))


def finish_install(
    session: Session, app: GitHubApp, box: SecretBox, *, code: str, installation_id: int, state: str
) -> tuple[str, str]:
    """GitHub sent the installer back. Returns (org id, claim). Each started install can finish once."""
    started = _open(box, state, "install link")
    nonce = f"ghstate:{started['n']}"
    if session.get(WebhookEvent, nonce) is not None:
        raise GitHubAppError("this install link was already used; start connecting GitHub again", 409)
    try:
        user_token = app.exchange_code(code)
        if installation_id not in app.user_installation_ids(user_token):
            raise GitHubAppError("your GitHub account can't access that installation", 403)
        inst = app.installation(installation_id)
    except GitHubError as e:
        raise GitHubAppError(str(e), 502) from e
    session.add(WebhookEvent(id=nonce, event_type="github.install_state", resource_id=None, outcome="used"))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise GitHubAppError("this install link was already used; start connecting GitHub again", 409) from None
    claim = _seal(
        box,
        {"o": started["o"], "u": started["u"], "i": inst.id, "a": inst.account_login, "ty": inst.account_type},
    )
    return started["o"], claim


def confirm_claim(
    session: Session, box: SecretBox, principal: Principal, org_id: str, claim: str
) -> GitHubInstallation:
    data = _open(box, claim, "GitHub connection")
    user = principal.require_person("connect GitHub")
    if data["o"] != org_id or data["u"] != user.id:
        raise GitHubAppError("this GitHub connection was started by someone else or for another organization", 403)
    row = session.exec(
        select(GitHubInstallation).where(
            GitHubInstallation.org_id == org_id, GitHubInstallation.installation_id == data["i"]
        )
    ).first()
    if row is None:
        row = GitHubInstallation(
            org_id=org_id,
            installation_id=data["i"],
            account_login=data["a"],
            account_type=data["ty"],
            created_by=principal.actor,
        )
        session.add(row)
        session.commit()
    return row


def installations(session: Session, org_ids: list[str]) -> list[GitHubInstallation]:
    if not org_ids:
        return []
    return list(
        session.exec(
            select(GitHubInstallation)
            .where(GitHubInstallation.org_id.in_(org_ids))  # type: ignore[union-attr]
            .order_by(GitHubInstallation.created_at)
        ).all()
    )


def unlink(session: Session, org_id: str, installation_id: int) -> None:
    row = session.exec(
        select(GitHubInstallation).where(
            GitHubInstallation.org_id == org_id, GitHubInstallation.installation_id == installation_id
        )
    ).first()
    if row is None:
        raise GitHubAppError("that installation isn't linked to this organization", 404)
    session.delete(row)
    session.commit()


def installation_removed(session: Session, installation_id: int) -> int:
    """The app was uninstalled or suspended on GitHub: drop every org's link to it. Returns how many."""
    rows = session.exec(select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)).all()
    for row in rows:
        session.delete(row)
    return len(rows)


# --- reading through installations -------------------------------------------------------


def repos(app: GitHubApp, rows: list[GitHubInstallation]) -> list[tuple[str, int]]:
    """(repo, installation id) for every repo the given installations can read."""
    out: dict[str, int] = {}
    for row in rows:
        try:
            for name in app.repos(row.installation_id):
                out.setdefault(name, row.installation_id)
        except GitHubError:
            continue  # an installation GitHub won't answer for shouldn't hide the others
    return sorted(out.items())


def installation_for_repo(app: GitHubApp | None, rows: list[GitHubInstallation], repo: str) -> int | None:
    if app is None:
        return None
    wanted = repo.lower()
    return next((iid for name, iid in repos(app, rows) if name.lower() == wanted), None)


def client_for_repo(
    app: GitHubApp | None, fallback: GitHubAPI, session: Session, org_ids: list[str], repo: str
) -> GitHubAPI:
    """A client that can read `repo`: an installation of one of these orgs, else the server's token."""
    iid = installation_for_repo(app, installations(session, org_ids), repo)
    return app.client(iid) if app is not None and iid is not None else fallback


# --- check runs --------------------------------------------------------------------------


def track(session: Session, m: Milestone, installation_id: int, repo: str, sha: str) -> None:
    """Keep a check run on this merge commit in step with the tranche."""
    exists = session.exec(
        select(GitHubCheck).where(GitHubCheck.milestone_id == m.id, GitHubCheck.repo == repo, GitHubCheck.sha == sha)
    ).first()
    if exists is None:
        session.add(GitHubCheck(milestone_id=m.id, installation_id=installation_id, repo=repo, sha=sha))


# status -> (check run status, conclusion or None, title)
_SHOWN: dict[MilestoneStatus, tuple[str, str | None, str]] = {
    MilestoneStatus.evidence_received: ("queued", None, "Evidence received; verifying next"),
    MilestoneStatus.verifying: ("in_progress", None, "Verifying against the acceptance criteria"),
    MilestoneStatus.needs_review: ("completed", "neutral", "Waiting for a person to review"),
    MilestoneStatus.verified: ("completed", "success", "Verified"),
    MilestoneStatus.settling: ("completed", "success", "Verified; payment in progress"),
    MilestoneStatus.held: ("completed", "success", "Paid for; in the dispute window"),
    MilestoneStatus.settled: ("completed", "success", "Paid"),
    MilestoneStatus.contested: ("completed", "neutral", "Payment contested"),
    MilestoneStatus.refunded: ("completed", "failure", "Refunded to the payer"),
    MilestoneStatus.disputed: ("completed", "neutral", "Rejection disputed; waiting for review"),
    MilestoneStatus.settlement_failed: ("completed", "neutral", "Verified; payment failed and will be retried"),
    MilestoneStatus.payout_failed: ("completed", "neutral", "Paid for; payout to the payee failed"),
}
_REJECTED = ("completed", "failure", "Not accepted yet: see what's missing")


def _shown(m: Milestone) -> tuple[str, str | None, str]:
    if m.status in _SHOWN:
        return _SHOWN[m.status]
    return _REJECTED  # back to pending/funded/overdue after this evidence: rejected or superseded


def _summary(m: Milestone) -> str:
    from app.services.verification import latest_verdict

    v = latest_verdict(m)
    lines = [f"**{m.title}** ({m.agreement.currency} {m.amount}) under *{m.agreement.title}*: {m.status}."]
    if v is not None:
        met = sum(1 for r in v.criteria_results if r.met)
        lines.append(f"\nVerifier: {met} of {len(v.criteria_results)} criteria met.")
        missing = [f"- `{r.criterion_id}`: {r.missing}" for r in v.criteria_results if not r.met and r.missing]
        if missing and m.status not in AFTER_VERIFIED:
            lines += ["\nWhat's missing:", *missing[:10]]
    return "\n".join(lines)


AFTER_VERIFIED = {
    MilestoneStatus.verified,
    MilestoneStatus.settling,
    MilestoneStatus.held,
    MilestoneStatus.settled,
    MilestoneStatus.contested,
    MilestoneStatus.refunded,
}


def publish_checks(session: Session, app: GitHubApp | None, link, now: datetime | None = None, limit: int = 50) -> int:
    """Post every check run whose tranche moved since it was last shown. `link(milestone, org_id)` builds the
    details URL. Returns how many were posted; failures are kept on the row and retried next time."""
    if app is None:
        return 0
    now = now or utcnow()
    posted = 0
    for check in session.exec(select(GitHubCheck)).all():
        m = session.get(Milestone, check.milestone_id)
        if m is None or check.posted_state == m.status:
            continue
        status_, conclusion, title = _shown(m)
        payee = next((p for p in m.agreement.parties if p.role == Role.payee), None)
        body: dict = {
            "name": CHECK_NAME,
            "head_sha": check.sha,
            "status": status_,
            "external_id": m.id,
            "output": {"title": title, "summary": _summary(m)},
        }
        if link and payee is not None:
            body["details_url"] = link(m, payee.org_id or "")
        if conclusion:
            body |= {"conclusion": conclusion, "completed_at": now.isoformat().replace("+00:00", "Z")}
        try:
            check.check_run_id = app.upsert_check_run(check.installation_id, check.repo, check.check_run_id, body)
            check.posted_state, check.error = m.status, None
            posted += 1
        except GitHubError as e:
            check.error = str(e)[:300]
        check.updated_at = now
        if posted >= limit:
            break
    session.commit()
    return posted


def integrations_for(session: Session, installation_id: int, repo: str) -> list[Integration]:
    rows = session.exec(
        select(Integration).where(Integration.kind == "github", Integration.installation_id == installation_id)
    ).all()
    return [r for r in rows if r.target.lower() == repo.lower()]
