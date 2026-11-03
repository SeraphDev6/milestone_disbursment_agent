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

from app.models import Agreement, AuditEvent, LedgerEntry, Milestone


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def milestone_section(session: Session, m: Milestone) -> dict:
    events = session.exec(select(AuditEvent).where(AuditEvent.milestone_id == m.id).order_by(AuditEvent.at)).all()
    return {
        "id": m.id,
        "title": m.title,
        "amount": str(m.amount),
        "due_date": m.due_date.isoformat() if m.due_date else None,
        "status": m.status,
        "criteria": [c.model_dump(mode="json") for c in m.criteria],
        "evidence": [
            {
                "id": e.id,
                "title": e.title,
                "type": e.evidence_type,
                "source": e.source,
                "external_ref": e.external_ref,
                "pinned_ref": e.pinned_ref,
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
                "results": [r.model_dump(mode="json") for r in v.criteria_results],
                "rationale": v.rationale,
            }
            for v in m.verdicts
            if not v.is_precheck
        ],
        "reviews": [
            {"at": _iso(e.at), "actor": e.actor, "action": e.action, "note": (e.detail or {}).get("note")}
            for e in events
            if e.action in ("review_approved", "review_rejected", "disputed")
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


def build_packet(session: Session, a: Agreement) -> dict:
    ledger = session.exec(select(LedgerEntry).where(LedgerEntry.agreement_id == a.id).order_by(LedgerEntry.at)).all()
    released = sum((e.amount for e in ledger if e.kind in ("captured", "paid")), start=Decimal(0))
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
        "milestones": [milestone_section(session, m) for m in a.milestones],
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
    flow: list = [
        _p(f"Compliance packet: {a['title']}", h1),
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
    SimpleDocTemplate(
        buf, pagesize=LETTER, title=f"Compliance packet: {a['title']}", leftMargin=0.75 * inch, rightMargin=0.75 * inch
    ).build(flow)
    return buf.getvalue()
