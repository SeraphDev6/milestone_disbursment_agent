"""The demo (/demo): on sandbox deployments only; its people sign in without email and can't receive any; its
shortcut works on demo agreements alone."""

import io
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.demo import FILES, FILES_DIR
from app.config import DEMO_EMAIL_DOMAIN
from app.integrations.uploads import parse_upload
from tests.conftest import GOOD_DRAFT, hermetic_app
from tests.helpers import PAYER_EMAIL, confirm, create_active, h, new_org, sign_in

ROOT = Path(__file__).resolve().parents[1]


def cast(client) -> dict[str, dict]:
    r = client.post("/demo/cast")
    assert r.status_code == 200, r.text
    return {p["key"]: p for p in r.json()["people"]}


def demo_agreement(client) -> tuple[str, list[str], dict]:
    """The way the demo page builds its grant: the funder creates it, the others accept from their inbox."""
    people = cast(client)
    funder, grantee, reviewer = people["funder"], people["grantee"], people["reviewer"]
    harbor = new_org(client, funder["token"], "Harbor Foundation", "funder@example.org")
    body = {**GOOD_DRAFT, "counterparty_email": grantee["email"], "reviewer_emails": [reviewer["email"]]}
    r = client.post(f"/orgs/{harbor}/agreements", json=body, headers=h(funder["token"]))
    assert r.status_code == 201, r.text
    aid = r.json()["agreement"]["id"]
    orgs = {"funder": harbor}
    for key, org in (
        ("grantee", {"name": "Riverside Reads", "paypal_email": "grantee@example.org"}),
        ("reviewer", {"name": "Sponsor Audit"}),
    ):
        [inv] = client.get("/me", headers=h(people[key]["token"])).json()["invitations"]
        r = client.post(f"/invitations/{inv['id']}/accept", json={"new_org": org}, headers=h(people[key]["token"]))
        assert r.status_code == 200, r.text
        orgs[key] = r.json()["organization"]["id"]
    for key in ("grantee", "reviewer", "funder"):
        assert confirm(client, aid, people[key]["token"]).status_code == 200
    a = client.get(f"/agreements/{aid}", headers=h(funder["token"], harbor)).json()
    assert a["status"] == "active"
    return aid, [m["id"] for m in a["milestones"]], {"people": people, "orgs": orgs}


def test_health_and_info_describe_the_demo(client):
    assert client.get("/health").json()["demo"] is True
    info = client.get("/demo").json()
    assert info == {
        "mode": "sandbox",
        "paypal_web_url": "https://www.sandbox.paypal.com",
        "paypal_payer": None,
        "paypal_payee": None,
    }


def test_published_sandbox_accounts_show_on_the_demo():
    app = hermetic_app(paypal_test_payer_email="buyer@personal.example.com", paypal_test_payer_password="pw-1")
    with TestClient(app) as c:
        info = c.get("/demo").json()
        assert info["paypal_payer"] == {"email": "buyer@personal.example.com", "password": "pw-1"}
        assert info["paypal_payee"] is None  # the payee's account needs both the email and the password


def test_cast_signs_in_three_fresh_people_at_the_demo_domain(client):
    first, second = cast(client), cast(client)
    assert [p for p in first] == ["funder", "grantee", "reviewer"]
    for p in first.values():
        assert p["email"].endswith("@" + DEMO_EMAIL_DOMAIN) and p["user"]["email"] == p["email"]
        assert client.get("/me", headers=h(p["token"])).json()["user"]["email"] == p["email"]
    assert {p["email"] for p in first.values()}.isdisjoint({p["email"] for p in second.values()})
    # Nobody can request a sign-in link for a demo person: the mail is dropped before any provider sees it.
    before = len(client.app.state.mailer.sent)
    assert client.post("/auth/magic-link", json={"email": first["funder"]["email"]}).status_code == 200
    assert len(client.app.state.mailer.sent) == before


def test_cast_is_rate_limited_per_client():
    with TestClient(hermetic_app(rate_limit_demo_per_hour=2)) as c:
        assert c.post("/demo/cast").status_code == 200 and c.post("/demo/cast").status_code == 200
        r = c.post("/demo/cast")
        assert r.status_code == 429 and "retry-after" in r.headers


def test_demo_people_build_a_grant_through_the_ordinary_api_and_get_the_shortcut(client):
    aid, mids, ctx = demo_agreement(client)
    funder = ctx["people"]["funder"]
    before = len(client.app.state.mailer.sent)
    r = client.post(f"/demo/milestones/{mids[0]}/mark-verified", headers=h(funder["token"], ctx["orgs"]["funder"]))
    assert r.status_code == 200, r.text
    assert r.json()["milestones"][0]["status"] == "verified"
    audit = client.get(f"/agreements/{aid}/audit", headers=h(funder["token"], ctx["orgs"]["funder"])).json()
    assert any(e["action"] == "demo_mark_verified" for e in audit)
    assert len(client.app.state.mailer.sent) == before  # notices to the demo's people go nowhere
    # The grantee can't take the shortcut: it's the payer's (or a reviewer's) release decision.
    grantee = ctx["people"]["grantee"]
    r = client.post(f"/demo/milestones/{mids[1]}/mark-verified", headers=h(grantee["token"], ctx["orgs"]["grantee"]))
    assert r.status_code == 403


def test_shortcut_refuses_real_agreements(client):
    _, mids, ctx = create_active(client, dict(GOOD_DRAFT))
    r = client.post(f"/demo/milestones/{mids[0]}/mark-verified", headers=h(ctx["payer"], ctx["payer_org"]))
    assert r.status_code == 403 and "demo agreements" in r.json()["detail"]


def test_a_real_person_joining_makes_it_a_real_agreement(client):
    _, mids, ctx = demo_agreement(client)
    funder = ctx["people"]["funder"]
    r = client.post(
        f"/orgs/{ctx['orgs']['funder']}/invitations",
        json={"email": PAYER_EMAIL, "role": "admin"},
        headers=h(funder["token"]),
    )
    assert r.status_code == 201
    real = sign_in(client, PAYER_EMAIL)
    [inv] = client.get("/me", headers=h(real)).json()["invitations"]
    assert client.post(f"/invitations/{inv['id']}/accept", json={}, headers=h(real)).status_code == 200
    r = client.post(f"/demo/milestones/{mids[0]}/mark-verified", headers=h(funder["token"], ctx["orgs"]["funder"]))
    assert r.status_code == 403


def test_no_demo_when_turned_off_or_live():
    with TestClient(hermetic_app(demo=False)) as c:
        assert c.get("/health").json() == {"status": "ok", "mode": "sandbox", "demo": False}
        assert c.get("/demo").status_code == 404 and c.post("/demo/cast").status_code == 404
    live = hermetic_app(mode="live", public_base_url="https://engine.example", paypal_webhook_id="w")
    with TestClient(live) as c:
        assert c.get("/health").json() == {"status": "ok", "mode": "live", "demo": False}
        assert c.post("/demo/cast").status_code == 404


# --- the demo's documents (demo/, written by scripts/demo_fixtures.py) -----------------------------------------


def _fixtures_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_fixtures", ROOT / "scripts" / "demo_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def demo_file(client, key: str) -> tuple[str, bytes, str]:
    f = next(x for x in client.get("/demo/files").json() if x["key"] == key)
    r = client.get(f"/demo/files/{f['name']}")
    assert r.status_code == 200, r.text
    return f["name"], r.content, f["media_type"]


def upload(client, mid, token, org, *files, **form):
    r = client.post(
        f"/milestones/{mid}/uploads",
        files=[("files", f) for f in files],
        data=form,
        headers=h(token, org),
    )
    assert r.status_code == 202, r.text
    return r.json()


def test_demo_files_are_listed_served_and_up_to_date(client, tmp_path):
    listed = client.get("/demo/files").json()
    assert [f["key"] for f in listed] == [f.key for f in FILES]
    assert [f["role"] for f in listed] == ["agreement"] * 2 + ["evidence"] * 6
    assert all(f["size"] > 0 for f in listed)
    _name, data, media = demo_file(client, "sign_in")
    assert media == "text/csv" and data.count(b"\n") == 215  # header + 214 attendees
    assert client.get("/demo/files/../pyproject.toml").status_code == 404
    assert client.get("/demo/files/nope.pdf").status_code == 404
    # The committed files are exactly what the generator writes (byte for byte: it is deterministic).
    mod = _fixtures_module()
    mod.write_all(tmp_path)
    for f in FILES:
        assert (tmp_path / f.name).read_bytes() == (FILES_DIR / f.name).read_bytes(), f"{f.name}: rerun the generator"


def test_the_grant_agreement_says_what_the_demo_grant_encodes(client):
    from pypdf import PdfReader

    _, data, _ = demo_file(client, "agreement")
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages).replace("\n", " ")
    for phrase in (
        "USD 30,000.00",
        "attendance register",
        "Tranche 1",
        "USD 10,000.00",
        "at least 200 participants",
        "Tranche 2",
        "USD 20,000.00",
        "September 30, 2027",
        "pre- and post-program reading assessments for every student",
        "meaningful community impact",  # the one clause the lint should flag
    ):
        assert phrase in text, phrase
    # It goes through the extraction endpoint as a PDF like any real agreement (the mock model's answer is canned).
    funder = cast(client)["funder"]
    r = client.post(
        "/agreements/extract", files={"file": ("agreement.pdf", data, "application/pdf")}, headers=h(funder["token"])
    )
    assert r.status_code == 200, r.text
    assert r.json()["lint"]["ok"]


def test_evidence_fixtures_go_through_upload_and_the_checks_as_the_script_says(client):
    _aid, (m1, m2), ctx = demo_agreement(client)
    grantee, funder = ctx["people"]["grantee"], ctx["people"]["funder"]
    g = (grantee["token"], ctx["orgs"]["grantee"])
    f = (funder["token"], ctx["orgs"]["funder"])

    # Tranche 1, thin evidence first: nothing to count, so every check fails and names what to send.
    upload(client, m1, *g, demo_file(client, "thin"))
    v = client.get(f"/milestones/{m1}/verdict", headers=h(*f)).json()
    assert v["met"] is False and all(not r["met"] for r in v["criteria_results"])
    assert all("spreadsheet" in (r["missing"] or "") for r in v["criteria_results"])

    # Then the real sign-in sheets (signed PDF and the register) and the budget report: 214 rows and USD 9,800.50,
    # decided by the checks alone; the CSVs take their date from the form, the PDF brings its own.
    upload(
        client,
        m1,
        *g,
        demo_file(client, "sign_in_pdf"),
        demo_file(client, "sign_in"),
        demo_file(client, "budget"),
        dated="2027-05-22",
    )
    v = client.get(f"/milestones/{m1}/verdict", headers=h(*f)).json()
    assert v["met"] is True and v["model"] == "checks"
    by_check = {r["decided_by"]: r for r in v["criteria_results"]}
    assert by_check["check:row_count"]["observed"].startswith("214 rows")
    assert by_check["check:sheet_total"]["observed"].startswith("9800.50 USD")

    # Tranche 2: the report PDF carries its own date, so the deadline check reads it without a form field.
    upload(client, m2, *g, demo_file(client, "report"))
    v = client.get(f"/milestones/{m2}/verdict", headers=h(*f)).json()
    dated = next(r for r in v["criteria_results"] if r["decided_by"] == "check:dated_by")
    assert dated["met"] is True and dated["observed"] == "dated 2027-09-12"
    [ev] = client.get(f"/milestones/{m2}/evidence", headers=h(*f)).json()
    assert ev["evidence_type"] == "document" and ev["dated"] == "2027-09-12" and ev["has_file"]
    # What the checks and the verifier read: the PDF's text, page by page, with the score table in it.
    [parsed] = parse_upload(*demo_file(client, "report")[:2], None, None)
    assert "[page 1]" in parsed.evidence.content and "Table 2" in parsed.evidence.content
    assert "R-087" in parsed.evidence.content

    # The draft report is the same document with section B's post scores missing; the skeptic's markers are there.
    _, draft, _ = demo_file(client, "report_draft")
    from pypdf import PdfReader

    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(draft)).pages)
    assert "TODO" in text and text.count("TODO") > 40  # one per section-B student, plus the note


def test_the_sow_reads_as_invoice_on_merge_and_a_demo_sow_can_connect_a_repo(client):
    from pypdf import PdfReader

    _, data, _ = demo_file(client, "sow")
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages).replace("\n", " ")
    for phrase in (
        "USD 6,000.00",
        "USD 3,500.00",
        "USD 2,500.00",
        "merged into the main branch",
        "continuous-integration check passing",
        "Acceptance is automatic",
    ):
        assert phrase in text, phrase

    # The way the demo page builds the SOW: invoice mode, a ci_green criterion, the contractor connects a repo.
    people = cast(client)
    client_, contractor = people["funder"], people["grantee"]
    acme = new_org(client, client_["token"], "Acme Retail", "acme@example.org")
    body = {
        "title": "Checkout redesign SOW",
        "total_amount": "3500.00",
        "currency": "USD",
        "settlement_mode": "invoice_on_verify",
        "counterparty_email": contractor["email"],
        "milestones": [
            {
                "title": "Milestone 1: payment form",
                "amount": "3500.00",
                "criteria": [
                    {
                        "statement": "The payment form PR is merged with every CI check passing",
                        "measure": {"metric": "CI check runs", "comparator": "exists"},
                        "evidence_required": ["code_change"],
                        "evidence_description": "Merged pull request with passing CI on its head commit",
                        "verification": "check",
                        "check_id": "ci_green",
                    }
                ],
            }
        ],
    }
    r = client.post(f"/orgs/{acme}/agreements", json=body, headers=h(client_["token"]))
    assert r.status_code == 201, r.text
    aid = r.json()["agreement"]["id"]
    [inv] = client.get("/me", headers=h(contractor["token"])).json()["invitations"]
    org = {"name": "Dev Co", "paypal_email": "dev@example.org"}
    r = client.post(f"/invitations/{inv['id']}/accept", json={"new_org": org}, headers=h(contractor["token"]))
    assert r.status_code == 200, r.text
    devco = r.json()["organization"]["id"]
    r = client.post(
        f"/agreements/{aid}/integrations/github",
        json={"repo": "acme-retail/storefront"},
        headers=h(contractor["token"], devco),
    )
    assert r.status_code == 200, r.text
    g = r.json()
    assert (
        g["via_app"] is False and g["webhook_url"].endswith(f"/webhooks/github/{g['integration_id']}") and g["secret"]
    )
    a = client.get(f"/agreements/{aid}", headers=h(client_["token"], acme)).json()
    assert [lbl["label"] for lbl in g["labels"]] == [f"mde-{m['id']}" for m in a["milestones"]]
