"""Custody of held deliverables: how long originals are kept, and who can still open them.

Retention (RETENTION_DAYS, off by default): a tranche whose story is over (settled or refunded, or on a cancelled
agreement and never captured into a hold) and has had no activity for RETENTION_DAYS, and whose capture is past
PayPal's 180-day chargeback window (so the dispute pack still has the work), ends custody:

- delete: each original file's bytes and each evidence item's text are removed. Hashes, sizes, pinned refs,
  verdicts (with their short quotes) and the audit trail stay, so the record still proves what was verified. A
  file shared with a tranche still in custody is kept until that one ends too.
- payee_only: nothing is removed, but only the payee side can open the originals, previews and handed-over
  archives from then on.

Either way `custody_ended` is recorded and both sides are told. Files are encrypted at rest throughout
(app/integrations/filecrypt.py); `encrypt_existing` converts files stored before that, or under an old key.
"""

from datetime import datetime, timedelta

from sqlalchemy import bindparam, func, text
from sqlmodel import Session, select

from app.db import as_utc, record_audit
from app.integrations import filecrypt
from app.models import (
    Agreement,
    AgreementStatus,
    AuditEvent,
    Evidence,
    GitHubHandover,
    Milestone,
    MilestoneStatus,
    Role,
    StoredFile,
)
from app.services.notifications import notify_side, tranche_subject

CHARGEBACK_WINDOW = timedelta(days=180)
CLOSED = {MilestoneStatus.settled, MilestoneStatus.refunded}
MONEY_IN_FLIGHT = {
    MilestoneStatus.settling,
    MilestoneStatus.held,
    MilestoneStatus.contested,
    MilestoneStatus.payout_failed,
}
ACTOR = "system:retention"


def closed(m: Milestone) -> bool:
    if m.status in CLOSED:
        return True
    return m.agreement.status == AgreementStatus.cancelled and m.status not in MONEY_IN_FLIGHT


def due_at(session: Session, m: Milestone, days: int) -> datetime:
    last = session.exec(select(func.max(AuditEvent.at)).where(AuditEvent.milestone_id == m.id)).one()
    if last is None:
        last = session.exec(select(func.max(AuditEvent.at)).where(AuditEvent.agreement_id == m.agreement_id)).one()
    due = as_utc(last or m.agreement.created_at) + timedelta(days=days)
    if m.captured_at:
        due = max(due, as_utc(m.captured_at) + CHARGEBACK_WINDOW)
    return due


def run(session: Session, settings, now: datetime) -> dict[str, int]:
    """End custody for every tranche that's due. Returns counts."""
    if settings.retention_days is None:
        return {}
    ended = purged = 0
    rows = session.exec(
        select(Milestone)
        .join(Agreement)
        .where(
            Milestone.custody_ended_at.is_(None),  # type: ignore[union-attr]
            (Milestone.status.in_(CLOSED)) | (Agreement.status == AgreementStatus.cancelled),  # type: ignore[attr-defined]
        )
    ).all()
    for m in rows:
        if not closed(m) or now < due_at(session, m, settings.retention_days) or not m.evidence:
            continue
        purged += _end(session, m, settings.retention_action, now)
        ended += 1
        session.commit()
    return {"custody ended": ended, "files purged": purged}


def _end(session: Session, m: Milestone, action: str, now: datetime) -> int:
    m.custody_ended_at = now
    file_ids = {i for e in m.evidence for i in (e.file_id, e.preview_file_id) if i}
    file_ids |= {
        h.file_id for h in session.exec(select(GitHubHandover).where(GitHubHandover.milestone_id == m.id)) if h.file_id
    }
    purged = []
    if action == "delete":
        for e in m.evidence:
            e.content = f"[Removed by retention on {now:%Y-%m-%d}. What was verified is committed as {e.pinned_ref}.]"
            e.artifact_uris, e.preview_url = [], None
        session.flush()
        for fid in sorted(file_ids):
            f = session.get(StoredFile, fid)
            if f is not None and f.purged_at is None and not _still_held(session, fid):
                f.data, f.purged_at = b"", now
                purged.append({"file_id": f.id, "sha256": f.sha256, "size": f.size})
    record_audit(
        session,
        m.agreement_id,
        ACTOR,
        "custody_ended",
        milestone_id=m.id,
        detail={"action": action, "evidence_ids": [e.id for e in m.evidence], "purged": purged},
    )
    what = (
        "its original files and evidence text were deleted. Their hashes, the verdict and the record of what "
        "happened are kept."
        if action == "delete"
        else "only the payee side can open its original files from now on."
    )
    for role in (Role.payer, Role.payee):
        body = f"'{m.title}' closed a while ago, so under this deployment's retention rules {what}"
        notify_side(session, m, role, tranche_subject(m, "Retention period ended"), body)
    return len(purged)


def _still_held(session: Session, file_id: str) -> bool:
    """Another tranche whose custody hasn't ended still references this file (files are shared by hash)."""
    refs = session.exec(
        select(Milestone.custody_ended_at)
        .join(Evidence, Evidence.milestone_id == Milestone.id)
        .where((Evidence.file_id == file_id) | (Evidence.preview_file_id == file_id))
    ).all()
    refs += session.exec(
        select(Milestone.custody_ended_at)
        .join(GitHubHandover, GitHubHandover.milestone_id == Milestone.id)
        .where(GitHubHandover.file_id == file_id)
    ).all()
    return any(r is None for r in refs)


def blocked(m: Milestone, f: StoredFile | None, role: Role | str | None) -> str | None:
    """Why this caller can't open this file any more, if retention is the reason."""
    if f is not None and f.purged_at is not None:
        return f"this file was deleted by retention on {f.purged_at:%Y-%m-%d}; its hash is kept ({f.sha256[:16]}…)"
    if m.custody_ended_at is not None and role != Role.payee:
        return "the retention period ended; only the payee side keeps the original files now"
    return None


def encrypt_existing(session: Session, batch: int = 50) -> int:
    """Encrypt files stored in plain form, or under an old key, with the current key. Returns how many."""
    c = filecrypt.cipher()
    if c is None:
        raise RuntimeError("no file encryption key is configured")
    ids = [r[0] for r in session.connection().execute(text("SELECT id FROM files WHERE purged_at IS NULL"))]
    done = 0
    for start in range(0, len(ids), batch):
        for fid, raw in session.connection().execute(
            text("SELECT id, data FROM files WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"ids": ids[start : start + batch]},
        ):
            blob = bytes(raw or b"")
            if not blob or c.current(blob):
                continue
            session.connection().execute(
                text("UPDATE files SET data = :d WHERE id = :id"), {"d": c.encrypt(c.decrypt(blob)), "id": fid}
            )
            done += 1
        session.commit()
    return done
