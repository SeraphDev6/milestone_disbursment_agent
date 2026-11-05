"""Verified delivery: the payer sees the verdict, not the work, until capture (#3); capture reveals it and starts
the dispute window (#4); what's revealed must match what was verified (#5); and auto-release on withheld work
needs a skeptic's second opinion (#9)."""

import copy
import hashlib
import json
from datetime import timedelta

from sqlmodel import select

from app.ai.agents import AIError
from app.db import as_utc
from app.models import (
    AuditEvent,
    CriterionResult,
    EvidenceCreate,
    EvidenceType,
    EvidenceVisibility,
    Milestone,
    OutboxMessage,
)
from app.services import verification
from app.services.disclosure import WITHHELD_RATIONALE, redact_result
from app.services.packet import milestone_section
from tests.helpers import REVIEWER_EMAIL, accept, confirm, create_draft, h, sign_in
from tests.test_escrow import fund
from tests.test_reporting import subscribe

REPORT = "Riverside Reads final report. Table 2 lists all 87 enrolled students with pre and post scores. SECRET-42"
SECRET = "SECRET-42"


def on_capture(draft, **extra) -> dict:
    d = copy.deepcopy(draft)
    d.update({"deliverable_release": "on_capture", "dispute_window_days": 3, **extra})
    return d


def setup(client, draft, *, auto_release=False, reviewer=False, people=None, **extra):
    """Active agreement; returns (aid, m2, ctx). Tranche 2 has a model-judged criterion and a date check."""
    reviewers = (REVIEWER_EMAIL,) if reviewer else ()
    aid, mids, ctx = create_draft(client, on_capture(draft, **extra), reviewers=reviewers, **(people or {}))
    ctx["m1"] = mids[0]
    if reviewer:
        token = sign_in(client, REVIEWER_EMAIL)
        accept(client, token, ctx["links"][REVIEWER_EMAIL], new_org={"name": "Fiscal Sponsor LLC"})
        ctx["reviewer"] = token
        assert confirm(client, aid, token).status_code == 200
    assert confirm(client, aid, ctx["payer"], auto_release_enabled=auto_release).status_code == 200
    assert confirm(client, aid, ctx["payee"]).json()["status"] == "active"
    return aid, mids[1], ctx


def upload(client, mid, ctx, content=REPORT, visibility=None, name="report.txt"):
    data = {"dated": "2027-09-01"} | ({"payer_visibility": visibility} if visibility else {})
    r = client.post(
        f"/milestones/{mid}/uploads",
        files={"files": (name, content.encode(), "text/plain")},
        data=data,
        headers=h(ctx["payee"]),
    )
    assert r.status_code == 202, r.text
    return r.json()[0]["evidence_id"]


def milestone(client, mid) -> Milestone:
    with client.app.state.sessionmaker() as s:
        m = s.get(Milestone, mid)
        s.expunge(m)
        return m


def actions(client, mid) -> list[AuditEvent]:
    with client.app.state.sessionmaker() as s:
        return list(s.exec(select(AuditEvent).where(AuditEvent.milestone_id == mid).order_by(AuditEvent.at)).all())


# --- #3: payer visibility before capture ------------------------------------------------


def test_payer_sees_verdict_not_work_before_capture(client, draft):
    aid, m2, ctx = setup(client, draft, reviewer=True)
    fund(client, m2, ctx)
    eid = upload(client, m2, ctx, visibility="verdict_only")

    [payer_view] = client.get(f"/milestones/{m2}/evidence", headers=h(ctx["payer"])).json()
    assert payer_view["withheld"] and payer_view["excerpt"] is None and payer_view["artifact_uris"] == []
    assert payer_view["payer_visibility"] == "verdict_only"
    # The commitment is visible at every level (#5).
    assert payer_view["pinned_ref"].startswith("sha256:")
    assert payer_view["file_sha256"] == hashlib.sha256(REPORT.encode()).hexdigest()

    for who in ("payee", "reviewer"):
        [full] = client.get(f"/milestones/{m2}/evidence", headers=h(ctx[who])).json()
        assert not full["withheld"] and SECRET in full["excerpt"]
        r = client.get(f"/evidence/{eid}/file", headers=h(ctx[who]))
        assert r.status_code == 200 and SECRET in r.text

    r = client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"]))
    assert r.status_code == 403 and "captured" in r.text

    verdict = client.get(f"/milestones/{m2}/verdict", headers=h(ctx["payer"])).json()
    assert all(r["quote"] is None for r in verdict["criteria_results"])
    assert any(r["observed"] for r in verdict["criteria_results"])  # verdict_only keeps the observed values
    assert verdict["rationale"] == WITHHELD_RATIONALE
    full = client.get(f"/milestones/{m2}/verdict", headers=h(ctx["reviewer"])).json()
    assert full["rationale"] != WITHHELD_RATIONALE

    packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(ctx["payer"])).text
    assert SECRET not in packet
    section = next(m for m in json.loads(packet)["milestones"] if m["id"] == m2)
    assert section["evidence_withheld"] and section["evidence"][0]["external_ref"] is None
    assert section["verdicts"][0]["rationale"] == WITHHELD_RATIONALE
    reviewer_packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(ctx["reviewer"])).json()
    assert not next(m for m in reviewer_packet["milestones"] if m["id"] == m2)["evidence_withheld"]

    pdf = client.get(f"/agreements/{aid}/compliance-packet?format=pdf", headers=h(ctx["payer"]))
    assert pdf.status_code == 200


def test_hash_only_hides_observed_values(client, draft):
    _, m2, ctx = setup(client, draft)
    fund(client, m2, ctx)
    upload(client, m2, ctx)  # defaults to the agreement minimum: hash_only
    verdict = client.get(f"/milestones/{m2}/verdict", headers=h(ctx["payer"])).json()
    assert all(r["observed"] is None and r["missing"] is None for r in verdict["criteria_results"])
    assert [r["met"] for r in verdict["criteria_results"]] == [True, True]  # pass/fail is always shown


def test_excerpt_level_shows_rationale(client, draft):
    _, m2, ctx = setup(client, draft)
    fund(client, m2, ctx)
    upload(client, m2, ctx, visibility="excerpt")
    verdict = client.get(f"/milestones/{m2}/verdict", headers=h(ctx["payer"])).json()
    assert verdict["rationale"] != WITHHELD_RATIONALE
    [ev] = client.get(f"/milestones/{m2}/evidence", headers=h(ctx["payer"])).json()
    assert ev["excerpt"] is None  # the evidence text itself still waits for capture


def test_redact_result_levels():
    r = CriterionResult(
        criterion_id="c1", met=False, decided_by="model", observed="80", evidence_ids=["e"], quote="q", missing="m"
    )
    assert redact_result(r, EvidenceVisibility.hash_only).model_dump(include={"observed", "quote", "missing"}) == {
        "observed": None,
        "quote": None,
        "missing": None,
    }
    v = redact_result(r, EvidenceVisibility.verdict_only)
    assert (v.observed, v.quote, v.missing) == ("80", None, "m")
    e = redact_result(r, EvidenceVisibility.excerpt)
    assert (e.observed, e.quote, e.missing) == ("80", "q", "m")


def test_visibility_cant_go_below_the_agreement_minimum(client, draft):
    _, m2, ctx = setup(client, draft, min_evidence_visibility="excerpt")
    r = client.post(
        f"/milestones/{m2}/evidence",
        json={"evidence_type": "document", "title": "r", "content": REPORT, "payer_visibility": "verdict_only"},
        headers=h(ctx["payee"]),
    )
    assert r.status_code == 422 and "excerpt" in r.text
    eid = upload(client, m2, ctx)
    [ev] = client.get(f"/milestones/{m2}/evidence", headers=h(ctx["payee"])).json()
    assert ev["id"] == eid and ev["payer_visibility"] == "excerpt"


def test_minimum_visibility_is_signed(client, draft):
    a1, _, c1 = create_draft(client, on_capture(draft))
    a2, _, c2 = create_draft(
        client, on_capture(draft, min_evidence_visibility="excerpt"), payer_email="p2@h.org", payee_email="e2@r.org"
    )
    h1 = client.get(f"/agreements/{a1}", headers=h(c1["payer"])).json()["criteria_hash"]
    h2 = client.get(f"/agreements/{a2}", headers=h(c2["payer"])).json()["criteria_hash"]
    assert h1 != h2


def test_payer_api_key_gets_the_same_restriction(client, draft):
    _, m2, ctx = setup(client, draft)
    fund(client, m2, ctx)
    eid = upload(client, m2, ctx)
    r = client.post(
        f"/orgs/{ctx['payer_org']}/api-keys", json={"name": "erp", "org_role": "member"}, headers=h(ctx["payer"])
    )
    key = r.json()["token"]
    [ev] = client.get(f"/milestones/{m2}/evidence", headers=h(key)).json()
    assert ev["withheld"] and ev["excerpt"] is None
    assert client.get(f"/evidence/{eid}/file", headers=h(key)).status_code == 403


def test_audit_and_webhooks_carry_no_evidence_content(client, draft):
    aid, m2, ctx = setup(client, draft)
    subscribe(client, aid, ctx, role="payer")
    fund(client, m2, ctx)
    upload(client, m2, ctx)
    log = client.get(f"/agreements/{aid}/audit", headers=h(ctx["payer"])).text
    assert SECRET not in log
    with client.app.state.sessionmaker() as s:
        payloads = [json.dumps(o.payload) for o in s.exec(select(OutboxMessage)).all()]
    assert payloads and not any(SECRET in p for p in payloads)


def test_on_submit_agreements_are_unchanged(client, draft):
    from tests.helpers import create_active

    _, (_, m2), ctx = create_active(client, draft)
    upload(client, m2, ctx)
    [ev] = client.get(f"/milestones/{m2}/evidence", headers=h(ctx["payer"])).json()
    assert not ev["withheld"] and SECRET in ev["excerpt"]


# --- #4: reveal at capture, then the dispute window ---------------------------------------


def test_capture_reveals_and_starts_the_dispute_window(client, draft):
    _aid, m2, ctx = setup(client, draft)
    fund(client, m2, ctx)
    eid = upload(client, m2, ctx)
    assert client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"])).status_code == 403

    r = client.post(f"/milestones/{m2}/review", json={"decision": "approve"}, headers=h(ctx["payer"]))
    assert r.json()["milestone_status"] == "held", r.text  # approved, captured, in the dispute window

    m = milestone(client, m2)
    assert m.revealed_at is not None
    assert as_utc(m.release_due_at) - as_utc(m.revealed_at) == timedelta(days=3)
    [released] = [e for e in actions(client, m2) if e.action == "deliverable_released"]
    assert released.detail["evidence"][0]["evidence_id"] == eid
    assert released.detail["evidence"][0]["file_sha256"] == hashlib.sha256(REPORT.encode()).hexdigest()
    assert released.detail["dispute_window_ends"]

    [ev] = client.get(f"/milestones/{m2}/evidence", headers=h(ctx["payer"])).json()
    assert not ev["withheld"] and SECRET in ev["excerpt"]
    r = client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"]))
    assert r.status_code == 200 and SECRET in r.text
    verdict = client.get(f"/milestones/{m2}/verdict", headers=h(ctx["payer"])).json()
    assert verdict["rationale"] != WITHHELD_RATIONALE

    # A contest inside the window works as before; the reveal isn't undone.
    r = client.post(f"/milestones/{m2}/contest", json={"reason": "the table is missing rows"}, headers=h(ctx["payer"]))
    assert r.status_code == 200, r.text
    assert milestone(client, m2).revealed_at is not None


def test_void_before_capture_never_reveals(client, draft):
    _, m2, ctx = setup(client, draft)
    fund(client, m2, ctx)
    assert client.post(f"/milestones/{m2}/void", headers=h(ctx["payer"])).status_code == 200
    assert milestone(client, m2).revealed_at is None
    assert not [e for e in actions(client, m2) if e.action == "deliverable_released"]


def test_on_capture_escrow_needs_a_dispute_window(client, draft):
    aid, _, ctx = create_draft(client, on_capture(draft, dispute_window_days=0))
    lint = client.get(f"/agreements/{aid}", headers=h(ctx["payer"])).json()["lint"]
    assert not lint["ok"] and lint["issues"][0]["code"] == "reveal_without_window"
    r = confirm(client, aid, ctx["payer"])
    assert r.status_code == 422


def test_invoice_mode_reveals_when_paid(client, draft):
    d = on_capture(draft, settlement_mode="invoice_on_verify", dispute_window_days=0)
    aid, mids, ctx = create_draft(client, d)
    lint = client.get(f"/agreements/{aid}", headers=h(ctx["payer"])).json()["lint"]
    assert lint["ok"] and "reveal_after_payment" in [i["code"] for i in lint["issues"]]
    assert confirm(client, aid, ctx["payer"]).status_code == 200
    assert confirm(client, aid, ctx["payee"]).json()["status"] == "active"
    m2 = mids[1]
    upload(client, m2, ctx)
    r = client.post(f"/milestones/{m2}/review", json={"decision": "approve"}, headers=h(ctx["payer"]))
    assert r.status_code == 200 and r.json()["milestone_status"] == "settling", r.text
    assert milestone(client, m2).revealed_at is None  # invoiced, not paid
    [inv_id] = client.app.state.paypal.invoices
    client.post(f"/mock/paypal/invoices/{inv_id}/pay")
    assert milestone(client, m2).revealed_at is not None


# --- #5: hash commitment ---------------------------------------------------------------


def test_downloads_after_reveal_carry_and_record_the_hash(client, draft):
    aid, m2, ctx = setup(client, draft)
    fund(client, m2, ctx)
    eid = upload(client, m2, ctx)
    client.post(f"/milestones/{m2}/review", json={"decision": "approve"}, headers=h(ctx["payer"]))
    r = client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"]))
    digest = hashlib.sha256(REPORT.encode()).hexdigest()
    assert r.headers["X-Content-SHA256"] == digest and r.headers["X-Pinned-Ref"].startswith("sha256:")
    [dl] = [e for e in actions(client, m2) if e.action == "deliverable_downloaded"]
    assert dl.detail == {"evidence_id": eid, "sha256": digest, "committed_sha256": digest, "matches": True}
    packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(ctx["payer"])).json()
    delivery = next(m for m in packet["milestones"] if m["id"] == m2)["delivery"]
    assert [d["action"] for d in delivery] == ["deliverable_released", "deliverable_downloaded"]


def test_tampered_file_is_refused_and_recorded(client, draft):
    from app.models import Evidence, StoredFile

    _, m2, ctx = setup(client, draft)
    fund(client, m2, ctx)
    eid = upload(client, m2, ctx)
    client.post(f"/milestones/{m2}/review", json={"decision": "approve"}, headers=h(ctx["payer"]))
    with client.app.state.sessionmaker() as s:
        f = s.get(StoredFile, s.get(Evidence, eid).file_id)
        f.data = b"something else"
        s.commit()
    assert client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"])).status_code == 409
    assert [e.detail["matches"] for e in actions(client, m2) if e.action == "deliverable_mismatch"] == [False]


class FakeGitHub:
    def __init__(self, commits: dict[tuple[str, str], tuple[str, str]]):
        self.commits = commits  # (repo, ref) -> (sha, tree)

    def commit(self, repo, ref):
        sha, tree = self.commits[(repo, ref)]
        return {"sha": sha, "commit": {"tree": {"sha": tree}}}


def _code_evidence(client, m2, ctx, sha):
    with client.app.state.sessionmaker() as s:
        m = s.get(Milestone, m2)
        ev = EvidenceCreate(
            evidence_type=EvidenceType.code_change,
            title="PR #7: final",
            content="Pull request #7",
            external_ref="https://github.com/river/app/pull/7",
        )
        row, _ = verification.submit_evidence(s, m, ev, source="github", actor="github:dev", pinned_ref=sha)
        return row.id


def test_handover_check_by_sha_and_by_tree(client, draft):
    aid, m2, ctx = setup(client, draft)
    pinned, other = "a" * 40, "b" * 40
    eid = _code_evidence(client, m2, ctx, pinned)

    r = client.post(f"/evidence/{eid}/handover", json={"delivered_sha": pinned}, headers=h(ctx["payer"]))
    assert r.json() == {
        "evidence_id": eid,
        "pinned_ref": pinned,
        "delivered_sha": pinned,
        "matches": True,
        "how": "same_commit",
    }
    r = client.post(f"/evidence/{eid}/handover", json={"delivered_sha": other}, headers=h(ctx["payer"]))
    assert r.json()["matches"] is False

    client.app.state.github = FakeGitHub(
        {("harbor/site", "main"): (other, "tree1"), ("river/app", pinned): (pinned, "tree1")}
    )
    r = client.post(f"/evidence/{eid}/handover", json={"repo": "harbor/site", "ref": "main"}, headers=h(ctx["payer"]))
    assert r.json()["matches"] and r.json()["how"] == "same_tree"
    checks = [e.detail["how"] for e in actions(client, m2) if e.action == "handover_checked"]
    assert checks == ["same_commit", "different", "same_tree"]
    packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(ctx["payer"])).json()
    assert len(next(m for m in packet["milestones"] if m["id"] == m2)["delivery"]) == 3


def test_handover_needs_commit_evidence(client, draft):
    _, m2, ctx = setup(client, draft)
    eid = upload(client, m2, ctx)
    r = client.post(f"/evidence/{eid}/handover", json={"delivered_sha": "a" * 40}, headers=h(ctx["payer"]))
    assert r.status_code == 422
    r = client.post(f"/evidence/{eid}/handover", json={"delivered_sha": "not-a-sha"}, headers=h(ctx["payer"]))
    assert r.status_code == 422


# --- #9: skeptic pass ------------------------------------------------------------------


def latest_verdict(client, m2, ctx, who="payee"):
    return client.get(f"/milestones/{m2}/verdict", headers=h(ctx[who])).json()


def test_skeptic_agrees_and_auto_release_goes_ahead(client, draft):
    _, m2, ctx = setup(client, draft, auto_release=True, reviewer=True)
    fund(client, m2, ctx)
    upload(client, m2, ctx)
    v = latest_verdict(client, m2, ctx)
    assert v["gate"] == "auto_release"
    assert v["skeptic"]["prompt_version"] == "skeptic-v1" and v["skeptic"]["disagrees_on"] == []
    assert milestone(client, m2).status == "held"


def test_skeptic_disagreement_sends_to_review(client, draft):
    _, m2, ctx = setup(client, draft, auto_release=True, reviewer=True)
    fund(client, m2, ctx)
    upload(client, m2, ctx, content=REPORT + " Scores for section B: TODO.")
    v = latest_verdict(client, m2, ctx)
    assert v["met"] and v["gate"] == "needs_review"
    model_criterion = v["criteria_results"][0]["criterion_id"]
    assert v["skeptic"]["disagrees_on"] == [model_criterion]
    assert "unfinished" in v["skeptic"]["results"][0]["missing"]
    assert milestone(client, m2).status == "needs_review"
    [sent] = [e for e in actions(client, m2) if e.action == "sent_to_review"]
    assert sent.detail["skeptic_disagrees_on"] == [model_criterion]
    # The payer's view of the skeptic is filtered like the rest of the verdict.
    payer = latest_verdict(client, m2, ctx, "payer")
    assert payer["skeptic"]["rationale"] == WITHHELD_RATIONALE
    assert payer["skeptic"]["results"][0]["missing"] is None


def test_skeptic_failure_fails_closed(client, draft, monkeypatch):
    _, m2, ctx = setup(client, draft, auto_release=True, reviewer=True)
    fund(client, m2, ctx)

    def broken(*a, **k):
        raise AIError("provider down")

    monkeypatch.setattr(client.app.state.ai, "skeptic", broken)
    upload(client, m2, ctx)
    v = latest_verdict(client, m2, ctx)
    assert v["gate"] == "needs_review" and "provider down" in v["skeptic"]["error"]


def test_no_skeptic_for_on_submit_or_checks_only(client, draft):
    from tests.helpers import create_active
    from tests.test_verification import BUDGET, SIGN_IN, submit

    _, (m1, m2), ctx = create_active(client, draft, auto_release=True)
    fund(client, m2, ctx)
    upload(client, m2, ctx, content=REPORT + " TODO")
    assert latest_verdict(client, m2, ctx)["skeptic"] is None  # on_submit: no second opinion

    _, _, ctx2 = setup(client, draft, auto_release=True, people={"payer_email": "p3@h.org", "payee_email": "e3@r.org"})
    m1 = ctx2["m1"]
    fund(client, m1, ctx2)
    submit(client, m1, ctx2, "spreadsheet", "sign-in.csv", SIGN_IN)
    submit(client, m1, ctx2, "spreadsheet", "budget.csv", BUDGET)
    v = latest_verdict(client, m1, ctx2)
    assert v["skeptic"] is None and v["gate"] == "auto_release"  # deterministic checks only


def test_milestone_section_defaults_to_full_view(client, draft):
    _, m2, ctx = setup(client, draft)
    upload(client, m2, ctx)
    with client.app.state.sessionmaker() as s:
        section = milestone_section(s, s.get(Milestone, m2))
    assert not section["evidence_withheld"] and section["evidence"][0]["external_ref"]


def test_levels_are_ordered_least_to_most_disclosed():
    assert [v.rank for v in EvidenceVisibility] == [0, 1, 2, 3]


def test_review_queue_flags_a_second_opinion_disagreement(client, draft):
    _, m2, ctx = setup(client, draft, auto_release=True, reviewer=True)
    fund(client, m2, ctx)
    upload(client, m2, ctx, content=REPORT + " Scores for section B: TODO.")
    [item] = client.get(f"/orgs/{ctx['payer_org']}/review-queue", headers=h(ctx["payer"])).json()
    assert item["milestone_id"] == m2 and item["verdict_met"] and item["second_opinion_disagreed"]
