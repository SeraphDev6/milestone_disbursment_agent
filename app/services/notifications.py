"""Emails to the people on an agreement when something needs them: a tranche to review, a rejection with what's
missing, a pay link, an overdue deadline, an unclaimed payout.

Services reach the mailer through `session.info["notifier"]`, set on the sessionmaker by the app and the jobs, so
money and verification code doesn't carry a mailer argument through every call. Recipients: the side's assignee
on the tranche when one is set, otherwise every member (member or above) of the party's organization. A missing
notifier (tests that build their own session) means no email; the audit log and outbound webhooks still record
the event.
"""

from dataclasses import dataclass
from urllib.parse import quote

from sqlmodel import Session, select

from app.integrations.email import Mailer
from app.models import Membership, Milestone, OrgRole, Party, Role, User

NOTIFIER = "notifier"


@dataclass(frozen=True)
class Notifier:
    mailer: Mailer
    base_url: str
    app_url: str | None = None

    def tranche_link(self, m: Milestone, org_id: str) -> str:
        if self.app_url:
            a, mid, o = quote(m.agreement_id or ""), quote(m.id), quote(org_id)
            return f"{self.app_url.rstrip('/')}/o/{o}/a/{a}/m/{mid}"
        return f"{self.base_url}/agreements/{quote(m.agreement_id or '')}"


def notifier_for(session: Session) -> Notifier | None:
    n = session.info.get(NOTIFIER)
    return n if isinstance(n, Notifier) else None


def _recipients(session: Session, m: Milestone, party: Party) -> list[str]:
    assignee_id = m.payer_assignee_id if party.role == Role.payer else m.payee_assignee_id
    if assignee_id and party.role != Role.reviewer:
        user = session.get(User, assignee_id)
        if user is not None:
            return [user.email]
    members = session.exec(select(Membership).where(Membership.org_id == party.org_id)).all()
    return sorted({mb.user.email for mb in members if OrgRole(mb.role).at_least(OrgRole.member)})


def notify_side(session: Session, m: Milestone, role: Role, subject: str, body: str, link: str | None = None) -> int:
    """Email one side of the tranche's agreement. Returns how many addresses were written to."""
    notifier = notifier_for(session)
    if notifier is None:
        return 0
    count = 0
    for party in (p for p in m.agreement.parties if p.role == role):
        target = link or notifier.tranche_link(m, party.org_id or "")
        for email in _recipients(session, m, party):
            notifier.mailer.send(email, subject, body, target)
            count += 1
    return count


def tranche_subject(m: Milestone, what: str) -> str:
    return f"{what}: '{m.title}' ({m.agreement.title})"
