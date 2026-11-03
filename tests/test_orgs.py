"""Sign-in, organizations, API keys, org-scoped views, and third-party reviewers."""

from tests.helpers import (
    PAYER_EMAIL,
    REVIEWER_EMAIL,
    accept,
    confirm,
    create_active,
    create_draft,
    h,
    new_org,
    sign_in,
    token_from,
)
from tests.test_verification import BUDGET, SIGN_IN, status_of, submit

# --- sign-in ---------------------------------------------------------------------------


def test_magic_link_is_single_use_and_creates_the_user(client):
    link = client.post("/auth/magic-link", json={"email": "New.Person@Example.org"}).json()["dev_link"]
    r = client.post("/auth/magic-link/verify", json={"token": token_from(link)})
    assert r.status_code == 200 and r.json()["user"]["email"] == "new.person@example.org"
    assert client.post("/auth/magic-link/verify", json={"token": token_from(link)}).status_code == 401
    me = client.get("/me", headers=h(r.json()["token"])).json()
    assert me["memberships"] == [] and me["invitations"] == []


def test_sign_out_revokes_the_session(client):
    token = sign_in(client, PAYER_EMAIL)
    assert client.get("/me", headers=h(token)).status_code == 200
    assert client.post("/auth/sign-out", headers=h(token)).status_code == 204
    assert client.get("/me", headers=h(token)).status_code == 401


def test_me_lists_pending_invitations(client, draft):
    payer = sign_in(client, PAYER_EMAIL)
    org = new_org(client, payer, "Harbor", "funder@example.org")
    client.post(f"/orgs/{org}/agreements", json=draft, headers=h(payer))
    payee = sign_in(client, draft["counterparty_email"])
    [inv] = client.get("/me", headers=h(payee)).json()["invitations"]
    assert inv["kind"] == "counterparty" and inv["agreement_title"] == draft["title"]


# --- organizations ---------------------------------------------------------------------


def test_org_roles_and_owner_protection(client):
    owner = sign_in(client, "owner@harbor.example.org")
    org = new_org(client, owner, "Harbor", "funder@example.org")
    inv = client.post(
        f"/orgs/{org}/invitations", json={"email": "adm@harbor.example.org", "role": "admin"}, headers=h(owner)
    ).json()
    admin = sign_in(client, "adm@harbor.example.org")
    accept(client, admin, inv["dev_link"])
    members = {m["user"]["email"]: m for m in client.get(f"/orgs/{org}/members", headers=h(admin)).json()}
    assert members["adm@harbor.example.org"]["role"] == "admin"

    owner_id = members["owner@harbor.example.org"]["user"]["id"]
    admin_id = members["adm@harbor.example.org"]["user"]["id"]
    r = client.patch(f"/orgs/{org}/members/{owner_id}", json={"role": "member"}, headers=h(admin))
    assert r.status_code == 403  # admins can't demote owners
    r = client.post(f"/orgs/{org}/invitations", json={"email": "x@example.org", "role": "owner"}, headers=h(admin))
    assert r.status_code == 403  # nor create owners
    r = client.patch(f"/orgs/{org}/members/{owner_id}", json={"role": "admin"}, headers=h(owner))
    assert r.status_code == 409  # last owner
    assert client.delete(f"/orgs/{org}/members/{admin_id}", headers=h(owner)).status_code == 204
    assert client.get(f"/orgs/{org}", headers=h(admin)).status_code == 404


def test_api_key_acts_as_its_org(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)
    r = client.post(
        f"/orgs/{ctx['payee_org']}/api-keys",
        json={"name": "evidence agent", "org_role": "member"},
        headers=h(ctx["payee"]),
    )
    assert r.status_code == 201
    key = r.json()["token"]
    assert client.get(f"/agreements/{aid}", headers=h(key)).status_code == 200
    r = client.post(
        f"/milestones/{m1}/evidence",
        headers=h(key),
        json={"evidence_type": "spreadsheet", "title": "s.csv", "content": SIGN_IN},
    )
    assert r.status_code == 202  # an agent submitting evidence for the payee
    actors = [e["actor"] for e in client.get(f"/milestones/{m1}/audit", headers=h(key)).json()]
    assert actors[0].startswith("apikey:")
    assert client.post(f"/milestones/{m1}/dispute", json={"reason": "x" * 20}, headers=h(key)).status_code == 403
    assert client.get("/me", headers=h(key)).status_code == 403

    revoked = client.delete(f"/orgs/{ctx['payee_org']}/api-keys/{_key_id(client, ctx)}", headers=h(ctx["payee"]))
    assert revoked.status_code == 204
    assert client.get(f"/agreements/{aid}", headers=h(key)).status_code == 401


def _key_id(client, ctx) -> str:
    [k] = client.get(f"/orgs/{ctx['payee_org']}/api-keys", headers=h(ctx["payee"])).json()
    return k["id"]


def test_api_key_cant_exceed_its_creator(client):
    owner = sign_in(client, "o@x.example.org")
    org = new_org(client, owner, "X")
    r = client.post(f"/orgs/{org}/api-keys", json={"name": "k", "org_role": "owner"}, headers=h(owner))
    assert r.status_code == 422


# --- org-scoped views ------------------------------------------------------------------


def test_each_side_sees_its_own_list(client, draft):
    aid, (_m1, _), ctx = create_active(client, draft)
    payer_list = client.get(f"/orgs/{ctx['payer_org']}/agreements?side=payer", headers=h(ctx["payer"])).json()
    payee_list = client.get(f"/orgs/{ctx['payee_org']}/agreements", headers=h(ctx["payee"])).json()
    assert [a["id"] for a in payer_list] == [aid] and payer_list[0]["counterparties"] == ["Riverside Reads"]
    assert payee_list[0]["side"] == "payee" and payee_list[0]["needs_attention"] == 2  # both tranches need evidence
    assert client.get(f"/orgs/{ctx['payer_org']}/agreements", headers=h(ctx["payee"])).status_code == 404


def test_review_queue_and_portfolio(client, draft):
    _aid, (m1, _), ctx = create_active(client, draft)
    url = client.post(f"/milestones/{m1}/fund", headers=h(ctx["payer"])).json()["approve_url"]
    client.get(url)
    submit(client, m1, ctx, "spreadsheet", "s.csv", SIGN_IN)
    [item] = client.get(f"/orgs/{ctx['payer_org']}/review-queue", headers=h(ctx["payer"])).json()
    assert item["milestone_id"] == m1 and item["verdict_met"] is False
    p = client.get(f"/orgs/{ctx['payer_org']}/portfolio", headers=h(ctx["payer"])).json()
    assert p["currency_totals"]["USD"] == {
        "total": "30000.00",
        "held": "10000.00",
        "invoiced": "0.00",
        "released": "0.00",
    }


# --- reviewers -------------------------------------------------------------------------


def active_with_reviewer(client, draft, **extra):
    draft = {**draft, **extra}
    aid, mids, ctx = create_draft(client, draft, reviewers=(REVIEWER_EMAIL,))
    reviewer = sign_in(client, REVIEWER_EMAIL)
    r = accept(client, reviewer, ctx["links"][REVIEWER_EMAIL], new_org={"name": "Fiscal Sponsor LLC"})
    ctx["reviewer"], ctx["reviewer_org"] = reviewer, r.json()["organization"]["id"]
    for who in ("payer", "payee", "reviewer"):
        assert confirm(client, aid, ctx[who]).status_code == 200
    return aid, mids, ctx


def test_reviewer_reviews_but_never_moves_money(client, draft):
    aid, (m1, _), ctx = active_with_reviewer(client, draft)
    rv = h(ctx["reviewer"])
    assert client.post(f"/milestones/{m1}/fund", headers=rv).status_code == 403
    submit(client, m1, ctx, "spreadsheet", "s.csv", SIGN_IN)
    submit(client, m1, ctx, "spreadsheet", "b.csv", BUDGET)
    assert client.get(f"/milestones/{m1}/evidence", headers=rv).status_code == 200
    assert client.get(f"/milestones/{m1}/verdict", headers=rv).json()["met"]
    [item] = client.get(f"/orgs/{ctx['reviewer_org']}/review-queue", headers=rv).json()
    assert item["side"] == "reviewer"

    r = client.post(f"/milestones/{m1}/review", json={"decision": "approve", "note": "Sheets check out"}, headers=rv)
    assert r.json()["milestone_status"] == "verified"  # not funded yet, so it waits
    assert client.post(f"/milestones/{m1}/release", headers=rv).status_code == 403
    assert client.get(f"/agreements/{aid}/compliance-packet", headers=rv).status_code == 200


def test_require_reviewer_approval(client, draft):
    aid, (m1, _), ctx = active_with_reviewer(client, draft, require_reviewer_approval=True)
    submit(client, m1, ctx, "spreadsheet", "s.csv", SIGN_IN)
    r = client.post(f"/milestones/{m1}/review", json={"decision": "approve"}, headers=h(ctx["payer"]))
    assert r.status_code == 403 and "reviewer" in r.json()["detail"]
    r = client.post(f"/milestones/{m1}/review", json={"decision": "reject"}, headers=h(ctx["payer"]))
    assert r.json()["milestone_status"] == "pending"  # the payer can still reject
    assert status_of(client, aid, ctx) == "pending"
