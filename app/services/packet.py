"""Compliance packet: everything a funder or auditor needs to see why each payment was made.

Built from the append-only records (confirmations, evidence, verdicts, settlements, ledger, audit),
so it can be regenerated at any time and always matches what happened.
"""

import io
from datetime import UTC, datetime
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlmodel import Session, select

from app.models import Agreement, AuditEvent, LedgerEntry, Milestone, Role, SkepticPass
from app.services import disclosure


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _skeptic(v) -> dict | None:
    if v.skeptic is None:
        return None
    return SkepticPass.model_validate(v.skeptic).model_dump(mode="json")


def _delivery_result(d: dict) -> str:
    if d["action"] == "deliverable_mismatch" or d.get("matches") is False:
        return "DOES NOT MATCH what was verified"
    if d["action"] == "deliverable_handed_over":
        where = d.get("pr_url") or (f"repo transferred: {d['transferred_to']}" if d.get("transferred_to") else None)
        where = where or "source archive"
        return f"tree {str(d.get('tree_sha'))[:12]} matches; {where}"
    if d["action"] == "handover_queued":
        return f"{d.get('method')} from {d.get('source_repo')}@{str(d.get('pinned_sha'))[:12]}"
    if d["action"] == "handover_failed":
        return f"failed: {d.get('why')}"
    if d.get("matches"):
        return "matches what was verified"
    if d["action"] == "deliverable_released":
        return f"{len(d.get('evidence') or [])} item(s) committed"
    return ""


# Reads of a tranche's evidence: list views (filtered or full), downloads of originals and handed-over archives.
ACCESS_ACTIONS = ("evidence_viewed", "evidence_downloaded", "deliverable_downloaded")


def _access(e: AuditEvent) -> dict:
    d = e.detail or {}
    side = d.get("side") or ("payer" if e.action == "deliverable_downloaded" else None)
    return {
        "at": _iso(e.at),
        "actor": e.actor,
        "side": side,
        "action": e.action,
        "view": d.get("view") or "full",
        "evidence_ids": d.get("evidence_ids") or ([d["evidence_id"]] if d.get("evidence_id") else []),
        "handover_id": d.get("handover_id"),
        "sha256": d.get("sha256"),
    }


def _access_what(x: dict) -> str:
    if x["action"] == "evidence_viewed":
        return f"viewed {len(x['evidence_ids'])} item(s), {x['view']} view"
    target = f"archive {x['handover_id'][:8]}" if x.get("handover_id") else f"file {', '.join(x['evidence_ids'])}"
    return f"downloaded {target}, sha256 {str(x.get('sha256'))[:16]}"


def milestone_section(session: Session, m: Milestone, role: Role | str | None = None) -> dict:
    """`role`: the side asking. The payer side gets the filtered view while the tranche's evidence is withheld
    (deliverable_release=on_capture, not yet captured); None or another side gets everything."""
    events = session.exec(select(AuditEvent).where(AuditEvent.milestone_id == m.id).order_by(AuditEvent.at)).all()
    hidden = disclosure.restricted(role, m)
    shown = {v.id: disclosure.verdict_read(v, m, role) for v in m.verdicts if not v.is_precheck}
    return {
        "id": m.id,
        "title": m.title,
        "amount": str(m.amount),
        "due_date": m.due_date.isoformat() if m.due_date else None,
        "status": m.status,
        "revealed_at": _iso(m.revealed_at),
        "evidence_withheld": hidden,
        "criteria": [c.model_dump(mode="json") for c in m.criteria],
        "evidence": [
            {
                "id": e.id,
                "title": e.title,
                "type": e.evidence_type,
                "source": e.source,
                "external_ref": None if hidden else e.external_ref,
                "pinned_ref": e.pinned_ref,
                "file_sha256": e.file.sha256 if e.file is not None else None,
                "payer_visibility": e.payer_visibility,
                "dated": e.dated.isoformat() if e.dated else None,
                "facts_from_adapter": e.check_results,
                "has_original_file": e.file_id is not None,
                "received_at": _iso(e.received_at),
                "submitted_by": e.submitted_by,
            }
            for e in m.evidence
        ],
        "verdicts": [
            {
                "id": v.id,
                "created_at": _iso(v.created_at),
                "met": v.met,
                "gate": v.gate,
                "model": v.model,
                "prompt_version": v.prompt_version,
                "model_confidence": v.model_confidence,
                "criteria_hash": v.criteria_hash,
                "evidence_ids": v.evidence_ids,
                "results": [r.model_dump(mode="json") for r in shown[v.id].criteria_results],
                "rationale": shown[v.id].rationale,
                "skeptic": _skeptic(shown[v.id]),
            }
            for v in m.verdicts
            if not v.is_precheck
        ],
        "delivery": [
            {"at": _iso(e.at), "actor": e.actor, "action": e.action, **(e.detail or {})}
            for e in events
            if e.action
            in (
                "deliverable_released",
                "deliverable_downloaded",
                "deliverable_mismatch",
                "handover_checked",
                "handover_queued",
                "handover_retried",
                "handover_failed",
                "deliverable_handed_over",
            )
        ],
        "access_log": [_access(e) for e in events if e.action in ACCESS_ACTIONS],
        "reviews": [
            {"at": _iso(e.at), "actor": e.actor, "action": e.action, "note": (e.detail or {}).get("note")}
            for e in events
            if e.action in ("review_approved", "review_rejected", "disputed", "contested", "contest_resolved")
        ],
        "settlements": [
            {
                "attempt": s.attempt,
                "mode": s.mode,
                "idempotency_key": s.idempotency_key,
                "paypal_ref": s.paypal_ref,
                "status": s.status,
                "error": s.error,
                "at": _iso(s.updated_at),
            }
            for s in m.settlements
        ],
        "payout_route": m.payout_route,
        "disbursements": [
            {
                "kind": d.kind,
                "attempt": d.attempt,
                "idempotency_key": d.idempotency_key,
                "paypal_ref": d.paypal_ref,
                "status": d.status,
                "error": d.error,
                "at": _iso(d.updated_at),
            }
            for d in m.disbursements
        ],
        "audit": [
            {
                "at": _iso(e.at),
                "actor": e.actor,
                "action": e.action,
                "from": e.from_status,
                "to": e.to_status,
                "detail": e.detail,
            }
            for e in events
        ],
    }


def build_packet(session: Session, a: Agreement, role: Role | str | None = None) -> dict:
    ledger = session.exec(select(LedgerEntry).where(LedgerEntry.agreement_id == a.id).order_by(LedgerEntry.at)).all()
    released = sum((e.amount for e in ledger if e.kind in ("disbursed", "paid")), start=Decimal(0))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "agreement": {
            "id": a.id,
            "title": a.title,
            "total_amount": str(a.total_amount),
            "currency": a.currency,
            "settlement_mode": a.settlement_mode,
            "status": a.status,
            "criteria_hash": a.criteria_hash,
            "auto_release_enabled": a.auto_release_enabled,
            "auto_release_threshold": a.auto_release_threshold,
            "dispute_window_days": a.dispute_window_days,
            "deliverable_release": a.deliverable_release,
            "min_evidence_visibility": a.min_evidence_visibility,
            "released": str(released.quantize(Decimal("0.01"))),
        },
        "parties": [
            {
                "role": p.role,
                "name": p.display_name,
                "paypal_email": p.paypal_email,
                "confirmed_criteria_hash": p.confirmed_criteria_hash,
                "confirmed_at": _iso(p.confirmed_at),
            }
            for p in a.parties
        ],
        "milestones": [milestone_section(session, m, role) for m in a.milestones],
        "ledger": [
            {
                "at": _iso(e.at),
                "milestone_id": e.milestone_id,
                "kind": e.kind,
                "amount": str(e.amount),
                "paypal_ref": e.paypal_ref,
            }
            for e in ledger
        ],
    }


# --- PDF -------------------------------------------------------------------------------


def _p(text: object, style) -> Paragraph:
    return Paragraph(escape(str(text if text is not None else "")), style)


def _table(rows: list[list], widths: list[float], body) -> Table:
    t = Table([[_p(c, body) for c in r] for r in rows], colWidths=widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF4")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C4D0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return t


def _measure(m: dict) -> str:
    return " ".join(str(x) for x in (m["metric"], m["comparator"], m.get("target"), m.get("unit")) if x)


def render_pdf(packet: dict) -> bytes:
    styles = getSampleStyleSheet()
    h1, h2, h3 = styles["Title"], styles["Heading2"], styles["Heading3"]
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)
    a = packet["agreement"]
    title = f"{packet.get('kind', 'Compliance packet')}: {a['title']}"
    flow: list = [
        _p(title, h1),
        _p(f"Generated {packet['generated_at']} | Agreement {a['id']}", small),
        Spacer(1, 8),
        _table(
            [
                ["Total", "Released", "Mode", "Status", "Auto-release"],
                [
                    f"{a['currency']} {a['total_amount']}",
                    f"{a['currency']} {a['released']}",
                    a["settlement_mode"],
                    a["status"],
                    f"{'on' if a['auto_release_enabled'] else 'off'} (threshold {a['auto_release_threshold']})",
                ],
            ],
            [1.1 * inch, 1.1 * inch, 1.3 * inch, 1.0 * inch, 2.0 * inch],
            small,
        ),
        Spacer(1, 6),
        _p(f"Locked criteria hash: {a['criteria_hash']}", small),
        _p("Parties and criteria confirmations", h2),
        _table(
            [["Role", "Name", "PayPal", "Confirmed"]]
            + [
                [p["role"], p["name"], p["paypal_email"], p["confirmed_at"] or "not confirmed"]
                for p in packet["parties"]
            ],
            [0.9 * inch, 1.7 * inch, 2.0 * inch, 1.9 * inch],
            small,
        ),
    ]
    if packet.get("dispute"):
        flow += _dispute_flow(packet["dispute"], h2, small)
    for m in packet["milestones"]:
        flow += [_p(f"{m['title']} ({a['currency']} {m['amount']}): {m['status']}", h2), _p("Acceptance criteria", h3)]
        flow.append(
            _table(
                [["Id", "Criterion", "Measure", "Decided by"]]
                + [
                    [
                        c["id"],
                        c["statement"],
                        _measure(c["measure"]),
                        c["verification"] + (f":{c['check_id']}" if c.get("check_id") else ""),
                    ]
                    for c in m["criteria"]
                ],
                [0.4 * inch, 3.0 * inch, 1.9 * inch, 1.2 * inch],
                small,
            )
        )
        if m["evidence"]:
            flow += [
                _p("Evidence", h3),
                _table(
                    [["Title", "Source", "Pinned", "Received"]]
                    + [[e["title"], e["source"], e["pinned_ref"][:24], e["received_at"]] for e in m["evidence"]],
                    [2.6 * inch, 0.8 * inch, 1.7 * inch, 1.4 * inch],
                    small,
                ),
            ]
        for v in m["verdicts"]:
            flow += [
                _p(
                    f"Verdict {v['created_at']}: {'met' if v['met'] else 'not met'}, gate {v['gate']}, "
                    f"confidence {v['model_confidence']:.2f} ({v['model']}, {v['prompt_version']})",
                    h3,
                ),
                _table(
                    [["Criterion", "Met", "Observed", "Cited", "Missing"]]
                    + [
                        [
                            r["criterion_id"],
                            "yes" if r["met"] else "no",
                            r.get("observed"),
                            ", ".join(r["evidence_ids"]),
                            r.get("missing"),
                        ]
                        for r in v["results"]
                    ],
                    [0.7 * inch, 0.4 * inch, 2.0 * inch, 1.5 * inch, 1.9 * inch],
                    small,
                ),
                _p(f"Rationale: {v['rationale']}", small),
            ]
            if v.get("skeptic"):
                sk = v["skeptic"]
                verdict = (
                    f"couldn't run ({sk['error']})"
                    if sk.get("error")
                    else f"disagreed on {', '.join(sk['disagrees_on'])}"
                    if sk["disagrees_on"]
                    else "agreed"
                )
                flow.append(
                    _p(f"Skeptic pass ({sk['model']}, {sk['prompt_version']}): {verdict}. {sk['rationale']}", small)
                )
        if m["delivery"]:
            flow += [
                _p("Delivery", h3),
                _table(
                    [["When", "Who", "Event", "Result"]]
                    + [
                        [
                            d["at"],
                            d["actor"],
                            d["action"],
                            _delivery_result(d),
                        ]
                        for d in m["delivery"]
                    ],
                    [1.4 * inch, 1.6 * inch, 1.5 * inch, 2.0 * inch],
                    small,
                ),
            ]
        if m.get("access_log"):
            flow += [
                _p("Who opened the evidence", h3),
                _table(
                    [["When", "Who", "Side", "What"]]
                    + [[x["at"], x["actor"], x["side"], _access_what(x)] for x in m["access_log"]],
                    [1.4 * inch, 1.9 * inch, 0.7 * inch, 2.5 * inch],
                    small,
                ),
            ]
        if m["reviews"]:
            flow += [
                _p("Reviews", h3),
                _table(
                    [["When", "Who", "Decision", "Note"]]
                    + [[r["at"], r["actor"], r["action"], r["note"]] for r in m["reviews"]],
                    [1.4 * inch, 1.5 * inch, 1.2 * inch, 2.4 * inch],
                    small,
                ),
            ]
        if m["settlements"]:
            flow += [
                _p("Payments", h3),
                _table(
                    [["Attempt", "Mode", "PayPal ref", "Status"]]
                    + [[s["attempt"], s["mode"], s["paypal_ref"], s["status"]] for s in m["settlements"]],
                    [0.7 * inch, 1.5 * inch, 2.8 * inch, 1.5 * inch],
                    small,
                ),
            ]
    flow += [
        _p("Ledger", h2),
        _table(
            [["When", "Kind", "Amount", "PayPal ref"]]
            + [[e["at"], e["kind"], e["amount"], e["paypal_ref"]] for e in packet["ledger"]],
            [1.8 * inch, 1.1 * inch, 1.0 * inch, 2.6 * inch],
            small,
        ),
    ]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=LETTER, title=title, leftMargin=0.75 * inch, rightMargin=0.75 * inch).build(flow)
    return buf.getvalue()


def _dispute_flow(d: dict, h2, small) -> list:
    rows = [
        ["Revealed to the payer", d["revealed_at"] or "not revealed"],
        ["Criteria hash both parties signed", d["criteria_hash"]],
    ] + [[f"Committed: {c['title']}", c["commitment"]] for c in d["commitments"]]
    flow: list = [
        _p("Dispute summary", h2),
        _p(
            "The payee delivered against criteria both parties signed; the verdict below was reached before the "
            "work was opened to the payer, and the payer's access to it afterwards is listed.",
            small,
        ),
        _table([["", ""], *rows], [2.2 * inch, 4.3 * inch], small),
    ]
    if d["payer_access"]:
        flow += [
            Spacer(1, 6),
            _p("The payer side's access to the delivered work", small),
            _table(
                [["When", "Who", "What"]] + [[x["at"], x["actor"], _access_what(x)] for x in d["payer_access"]],
                [1.5 * inch, 2.2 * inch, 2.8 * inch],
                small,
            ),
        ]
    if d["paypal_disputes"]:
        flow += [
            Spacer(1, 6),
            _table(
                [["When", "PayPal dispute", "Reason", "Status"]]
                + [[x["at"], x["dispute_id"], x["reason"], x["status"]] for x in d["paypal_disputes"]],
                [1.5 * inch, 1.8 * inch, 1.8 * inch, 1.4 * inch],
                small,
            ),
        ]
    return flow


def build_dispute_pack(session: Session, m: Milestone) -> dict:
    """One tranche's record for answering a chargeback or customer dispute: the signed criteria, the commitment
    (what was verified, by hash), when the work was opened to the payer, everything the payer side opened since,
    the verdict with citations and the payments. Built for the payee, so nothing is filtered."""
    a = m.agreement
    packet = build_packet(session, a, Role.payee)
    section = next(x for x in packet["milestones"] if x["id"] == m.id)
    events = session.exec(select(AuditEvent).where(AuditEvent.milestone_id == m.id).order_by(AuditEvent.at)).all()
    released = next((e for e in events if e.action == "deliverable_released"), None)
    by_id = {e.id: e for e in m.evidence}
    commitments = []
    for item in (released.detail or {}).get("evidence", []) if released else []:
        ev = by_id.get(item.get("id") or item.get("evidence_id") or "")
        commitments.append(
            {
                "title": ev.title if ev else item.get("id"),
                "commitment": " / ".join(
                    str(x) for x in (item.get("pinned_ref"), item.get("file_sha256") or item.get("sha256")) if x
                ),
            }
        )
    if not commitments:
        commitments = [
            {
                "title": e.title,
                "commitment": " / ".join(x for x in (e.pinned_ref, e.file.sha256 if e.file else None) if x),
            }
            for e in m.evidence
        ]
    return packet | {
        "kind": "Dispute pack",
        "milestones": [section],
        "ledger": [e for e in packet["ledger"] if e["milestone_id"] == m.id],
        "dispute": {
            "milestone_id": m.id,
            "revealed_at": _iso(m.revealed_at),
            "criteria_hash": a.criteria_hash,
            "commitments": commitments,
            "payer_access": [x for x in section["access_log"] if x["side"] == "payer"],
            "paypal_disputes": [
                {
                    "at": _iso(e.at),
                    "dispute_id": (e.detail or {}).get("dispute_id"),
                    "reason": (e.detail or {}).get("reason"),
                    "status": (e.detail or {}).get("status"),
                }
                for e in events
                if (e.detail or {}).get("dispute_id")
            ],
        },
    }
