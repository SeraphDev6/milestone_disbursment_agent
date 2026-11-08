"""Co-funded grants (#47): several funders per agreement, each holding its share of each tranche.

The lead funder (the payer that created the agreement) drafts, reviews, releases and decides; co-funders join by
invitation (`InvitationKind.funder`), sign the criteria, fund the share each tranche's `shares` give them, and can
contest a held tranche. The money model is in app.services.settlement: one `Hold` per funder, one capture per hold
per release, payouts and refunds per capture.

What this module adds is the funding window. A tranche is funded only once every share is held. When the first hold
is `funding_window_days` old and others are still missing, the window has lapsed: the daily job records it once
(`funding_lapsed`) and tells the lead and the funders who haven't held their share. The lead then decides:

- void: release every hold placed so far; the tranche goes back to pending and can be funded again later.
- shrink: propose the tranche at what was funded. The payee accepts (the tranche amount, the agreement total and
  the unfunded shares change; the criteria hash is re-locked with both consents recorded) or declines.
- cover: reassign the unfunded part to the funders who agree (the lead, or co-funders by invited email). Each agrees
  by funding the extra, through a new hold on top of the one it has.

Shares aren't part of the criteria hash, so covering a gap changes the funders' split without the payee re-signing.
"""

from datetime import datetime
from decimal import Decimal

from sqlmodel import Session, col, select

from app.db import as_utc, record_audit
from app.domain.criteria import criteria_hash
from app.integrations.paypal import PayPalGateway
from app.models import AuditEvent, FunderShare, Milestone, MilestoneStatus, Role
from app.services import ServiceError, settlement
from app.services.notifications import notify_party, notify_side, tranche_subject


def _lapse_recorded(session: Session, m: Milestone) -> bool:
    """A `funding_lapsed` event since the oldest live hold was placed (and since the last shortfall decision)."""
    first = min((as_utc(h.funded_at) for h in settlement.live_holds(m) if h.funded_at), default=None)
    if first is None:
        return True
    events = session.exec(
        select(AuditEvent)
        .where(
            AuditEvent.milestone_id == m.id,
            col(AuditEvent.action).in_(("funding_lapsed", "shortfall_covered", "shrink_proposed", "shrink_declined")),
        )
        .order_by(col(AuditEvent.at).desc())
        .limit(1)
    ).first()
    return events is not None and events.action == "funding_lapsed" and as_utc(events.at) >= first


def mark_lapses(session: Session, now: datetime, actor: str = "system:funding") -> int:
    """Daily: record each co-funded tranche whose funding window has lapsed, once, and tell the people concerned."""
    count = 0
    candidates = session.exec(
        select(Milestone).where(col(Milestone.status).in_((MilestoneStatus.pending, MilestoneStatus.overdue)))
    ).all()
    for m in candidates:
        if not settlement.funding_lapsed(m, now) or _lapse_recorded(session, m):
            continue
        missing = settlement.missing_funders(m)
        a = m.agreement
        record_audit(
            session,
            a.id,
            actor,
            "funding_lapsed",
            milestone_id=m.id,
            detail={
                "funded": str(settlement.funded_amount(m)),
                "missing": [{"funder": p.display_name, "amount": str(x)} for p, x in missing],
                "window_days": a.funding_window_days,
            },
        )
        session.commit()
        names = ", ".join(f"{p.display_name} ({a.currency} {x})" for p, x in missing)
        notify_party(
            session,
            m,
            settlement.lead(m),
            tranche_subject(m, "Funding incomplete"),
            f"{a.funding_window_days} days after the first hold on '{m.title}', {names} still haven't held their "
            f"share. {a.currency} {settlement.funded_amount(m)} of {m.amount} is held, and PayPal holds last 29 days. "
            "From the tranche you can cancel the holds, propose the tranche at the funded amount to the payee, or ask "
            "the funders who agree to cover the gap.",
            tone="alert",
        )
        for p, x in missing:
            notify_party(
                session,
                m,
                p,
                tranche_subject(m, "Your share is still unfunded"),
                f"Your {a.currency} {x} share of '{m.title}' isn't held yet, and the funding window has passed. Fund "
                "it from the tranche, or the lead funder may cancel the holds, shrink the tranche or reassign your "
                "share.",
                tone="alert",
            )
        count += 1
    return count


def shortfall(m: Milestone) -> Decimal:
    return m.amount - settlement.funded_amount(m)


def _require_shortfall(m: Milestone, now: datetime, lapsed_needed: bool) -> None:
    if not settlement.co_funded(m):
        raise ServiceError("this tranche has one funder; there is no shortfall to resolve", 409)
    if m.status not in (MilestoneStatus.pending, MilestoneStatus.overdue) or settlement.fully_funded(m):
        raise ServiceError("this tranche is fully funded or already moving; nothing to resolve", 409)
    if not settlement.live_holds(m):
        raise ServiceError("nobody has funded this tranche yet; there is nothing to resolve", 409)
    if lapsed_needed and not settlement.funding_lapsed(m, now):
        first = min(as_utc(h.funded_at) for h in settlement.live_holds(m) if h.funded_at)
        closes = first + settlement.funding_window(m)
        raise ServiceError(
            f"the funding window is still open until {closes:%B %d, %Y}; the other funders can still hold their share",
            409,
        )
    if m.proposed_amount is not None:
        raise ServiceError("a proposal to shrink this tranche is waiting for the payee's answer", 409)


def resolve_shortfall(
    session: Session,
    gw: PayPalGateway,
    m: Milestone,
    actor: str,
    decision: str,
    cover: list[tuple[str | None, Decimal]],
    note: str,
    now: datetime,
) -> None:
    """The lead funder's decision on a tranche some funders never held. `void` is allowed as soon as anyone has
    funded; `shrink` and `cover` once the funding window has lapsed."""
    a = m.agreement
    _require_shortfall(m, now, lapsed_needed=decision != "void")
    if decision == "void":
        record_audit(session, a.id, actor, "shortfall_voided", milestone_id=m.id, detail={"note": note})
        settlement.void(session, gw, m, actor)
        return
    gap = shortfall(m)
    missing = settlement.missing_funders(m)
    if decision == "shrink":
        m.proposed_amount = settlement.funded_amount(m)
        record_audit(
            session,
            a.id,
            actor,
            "shrink_proposed",
            milestone_id=m.id,
            detail={
                "from": str(m.amount),
                "to": str(m.proposed_amount),
                "missing": [{"funder": p.display_name, "amount": str(x)} for p, x in missing],
                "note": note,
            },
        )
        session.commit()
        notify_side(
            session,
            m,
            Role.payee,
            tranche_subject(m, "Proposal to shrink a tranche"),
            f"The lead funder proposes '{m.title}' at {a.currency} {m.proposed_amount} instead of {m.amount}: "
            f"{', '.join(p.display_name for p, _ in missing)} never funded their share."
            + (f" Note: {note}" if note else "")
            + " Accept from the tranche and it is funded at the new amount; decline and the holds stay as they are.",
            tone="review",
        )
        return
    if decision != "cover":
        raise ServiceError(f"unknown decision {decision}", 422)
    if not cover:
        raise ServiceError("say who covers the gap: the lead (email null) and/or co-funders by invited email", 422)
    total = sum((x for _, x in cover), start=Decimal("0.00"))
    if total != gap:
        raise ServiceError(f"the cover adds up to {total}, but the gap is {gap}", 422)
    funded_emails = {
        (p.invited_email or "").lower() for p, _ in settlement.funders(m) if settlement.held_by(m, p) > 0 and not p.lead
    }
    for email, _ in cover:
        if email is not None and email.lower() not in funded_emails:
            raise ServiceError(f"{email} isn't a co-funder who has held a share of this tranche", 422)
    # The missing funders keep only what they actually hold; the covering funders' shares grow by their part.
    shares: dict[str, Decimal] = {sh.email.lower(): sh.amount for sh in m.shares}
    for p, x in missing:
        if p.lead:
            continue  # the lead's share is what the others leave; it shrinks by itself
        key = (p.invited_email or "").lower()
        shares[key] = shares.get(key, Decimal("0.00")) - x
        if shares[key] <= 0:
            del shares[key]
    for email, x in cover:
        if email is not None:
            shares[email.lower()] = shares.get(email.lower(), Decimal("0.00")) + x
    m.shares = [FunderShare(email=e, amount=x) for e, x in shares.items()]
    record_audit(
        session,
        a.id,
        actor,
        "shortfall_covered",
        milestone_id=m.id,
        detail={
            "gap": str(gap),
            "cover": [{"funder": e or "lead", "amount": str(x)} for e, x in cover],
            "shares": [{"email": sh.email, "amount": str(sh.amount)} for sh in m.shares],
            "note": note,
        },
    )
    session.commit()
    by_email = {(p.invited_email or "").lower(): p for p in a.parties if p.role == Role.payer and not p.lead}
    for email, x in cover:
        if email is None:
            continue
        notify_party(
            session,
            m,
            by_email[email.lower()],
            tranche_subject(m, "Please fund an additional share"),
            f"The lead funder asks you to cover {a.currency} {x} more of '{m.title}', which "
            f"{', '.join(p.display_name for p, _ in missing)} never funded."
            + (f" Note: {note}" if note else "")
            + " Funding it from the tranche is your agreement; until then the tranche stays unfunded.",
            tone="review",
        )


def answer_shrink(session: Session, m: Milestone, actor: str, accept: bool, note: str, now: datetime) -> None:
    """The payee's answer. Accepting re-locks the criteria hash: the lead proposed and the payee accepted, which is
    the consent of the two parties the change binds; funders who held their share are unaffected."""
    a = m.agreement
    if m.proposed_amount is None:
        raise ServiceError("no proposal to shrink this tranche is open", 409)
    if not accept:
        record_audit(session, a.id, actor, "shrink_declined", milestone_id=m.id, detail={"note": note})
        m.proposed_amount = None
        session.commit()
        notify_party(
            session,
            m,
            settlement.lead(m),
            tranche_subject(m, "Shrink declined"),
            f"The payee declined '{m.title}' at {a.currency} {settlement.funded_amount(m)}."
            + (f" Note: {note}" if note else "")
            + " The holds stay as they are; you can cancel them, or ask the funders who agree to cover the gap.",
            tone="alert",
        )
        return
    if settlement.funded_amount(m) != m.proposed_amount:
        m.proposed_amount = None
        session.commit()
        raise ServiceError("the funded amount changed since the proposal; ask the lead funder to propose again", 409)
    before, after = m.amount, m.proposed_amount
    proposed = session.exec(
        select(AuditEvent)
        .where(AuditEvent.milestone_id == m.id, AuditEvent.action == "shrink_proposed")
        .order_by(col(AuditEvent.at).desc())
        .limit(1)
    ).first()
    # Every funder keeps exactly what it holds; funders who never held anything are dropped from this tranche.
    m.shares = [
        FunderShare(email=p.invited_email or "", amount=settlement.held_by(m, p))
        for p, _ in settlement.funders(m)
        if not p.lead and settlement.held_by(m, p) > 0
    ]
    m.amount = after
    m.proposed_amount = None
    a.total_amount = a.total_amount - (before - after)
    new_hash = criteria_hash(a.milestones, a.deliverable_release, a.min_evidence_visibility, a.partial_release)
    old_hash, a.criteria_hash = a.criteria_hash, new_hash
    for p in a.parties:
        p.confirmed_criteria_hash = new_hash
    record_audit(
        session,
        a.id,
        actor,
        "tranche_shrunk",
        milestone_id=m.id,
        detail={
            "from": str(before),
            "to": str(after),
            "agreement_total": str(a.total_amount),
            "criteria_hash": {"from": old_hash, "to": new_hash},
            "agreed_by": {"lead": proposed.actor if proposed else None, "payee": actor},
            "shares": [{"email": sh.email, "amount": str(sh.amount)} for sh in m.shares],
            "note": note,
        },
    )
    m.funded_at = now
    if m.status == MilestoneStatus.pending:
        settlement.transition(session, m, MilestoneStatus.funded, actor, "funded", amended=True)
    session.commit()
    notify_side(
        session,
        m,
        Role.payer,
        tranche_subject(m, "Tranche shrunk and funded"),
        f"The payee accepted '{m.title}' at {a.currency} {after} (was {before}). The agreement total is now "
        f"{a.currency} {a.total_amount}; the tranche is funded with the holds in place.",
        tone="held",
        all_funders=True,
    )
