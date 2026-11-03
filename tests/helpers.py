"""Test helpers that drive the real flows: magic-link sign-in, orgs, invitations, confirmation."""

from urllib.parse import parse_qs, urlsplit

PAYER_EMAIL = "officer@harbor.example.org"
PAYEE_EMAIL = "director@riverside.example.org"
REVIEWER_EMAIL = "auditor@sponsor.example.org"
PAYER_PAYPAL = "funder@example.org"
PAYEE_PAYPAL = "grantee@example.org"


def h(token: str, org_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if org_id:
        headers["X-Org-Id"] = org_id
    return headers


def token_from(link: str) -> str:
    return parse_qs(urlsplit(link).query)["token"][0]


def sign_in(client, email: str) -> str:
    r = client.post("/auth/magic-link", json={"email": email})
    assert r.status_code == 200, r.text
    r = client.post("/auth/magic-link/verify", json={"token": token_from(r.json()["dev_link"])})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def new_org(client, token: str, name: str, paypal: str | None = None) -> str:
    r = client.post("/orgs", json={"name": name, "paypal_email": paypal}, headers=h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def accept(client, token: str, link: str, *, org_id: str | None = None, new_org: dict | None = None):
    body = {"token": token_from(link), "org_id": org_id, "new_org": new_org}
    return client.post("/invitations/accept", json=body, headers=h(token))


def create_draft(
    client,
    draft: dict,
    *,
    reviewers: tuple[str, ...] = (),
    payer_email: str = PAYER_EMAIL,
    payee_email: str = PAYEE_EMAIL,
) -> tuple[str, list[str], dict]:
    """Payer org creates the agreement; the payee accepts with a new org. Nobody has confirmed yet.

    Returns (agreement_id, milestone_ids, ctx) where ctx has tokens and org ids per role, plus invitation links.
    """
    payer = sign_in(client, payer_email)
    payer_org = new_org(client, payer, "Harbor Foundation", PAYER_PAYPAL)
    body = {k: v for k, v in draft.items() if k != "parties"}
    body["counterparty_email"] = payee_email
    body.setdefault("reviewer_emails", list(reviewers))
    r = client.post(f"/orgs/{payer_org}/agreements", json=body, headers=h(payer))
    assert r.status_code == 201, r.text
    created = r.json()
    aid = created["agreement"]["id"]

    payee = sign_in(client, body["counterparty_email"])
    r = accept(
        client,
        payee,
        created["dev_links"][body["counterparty_email"]],
        new_org={"name": "Riverside Reads", "paypal_email": PAYEE_PAYPAL},
    )
    assert r.status_code == 200, r.text
    ctx = {
        "payer": payer,
        "payee": payee,
        "payer_org": payer_org,
        "payee_org": r.json()["organization"]["id"],
        "links": created["dev_links"],
    }
    return aid, [m["id"] for m in created["agreement"]["milestones"]], ctx


def confirm(client, aid: str, token: str, **extra):
    current = client.get(f"/agreements/{aid}", headers=h(token)).json()["criteria_hash"]
    return client.post(f"/agreements/{aid}/confirm", json={"criteria_hash": current, **extra}, headers=h(token))


def create_active(client, draft: dict, auto_release: bool = False, **people) -> tuple[str, list[str], dict]:
    """An active agreement: payer and payee orgs joined and both confirmed. keys['payer'|'payee'] are tokens."""
    aid, mids, ctx = create_draft(client, draft, **people)
    assert confirm(client, aid, ctx["payer"], auto_release_enabled=auto_release).status_code == 200
    r = confirm(client, aid, ctx["payee"])
    assert r.json()["status"] == "active", r.text
    return aid, mids, ctx
