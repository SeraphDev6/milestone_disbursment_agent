"""The demo (/demo): on sandbox deployments only; its people sign in without email and can't receive any; its
shortcut works on demo agreements alone."""

from fastapi.testclient import TestClient

from app.config import DEMO_EMAIL_DOMAIN
from tests.conftest import GOOD_DRAFT, hermetic_app
from tests.helpers import PAYER_EMAIL, confirm, create_active, h, new_org, sign_in


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
    app = hermetic_app(demo_paypal_payer_email="buyer@personal.example.com", demo_paypal_payer_password="pw-1")
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
