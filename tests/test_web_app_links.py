"""Links and hand-offs for the web app: emailed links open the app, SSO returns to it, in-app invitation accept,
and the PayPal status says whether onboarding was started."""

from urllib.parse import parse_qs, urlsplit

import pytest

from tests.conftest import GOOD_DRAFT
from tests.helpers import PAYEE_EMAIL, create_active, emailed_link, h, new_org, sign_in
from tests.test_sso import idp, setup_sso  # noqa: F401

APP = "http://web.test/app"


@pytest.fixture
def app_client(client):
    client.app.state.app_url = APP  # as create_app sets it from PUBLIC_APP_URL (or a web build)
    return client


def test_magic_link_opens_the_web_app(app_client):
    app_client.post("/auth/magic-link", json={"email": "pat@example.org"})
    link = emailed_link(app_client, "pat@example.org", "/auth/")
    assert link.startswith(f"{APP}/auth/verify?token=")
    token = parse_qs(urlsplit(link).query)["token"][0]
    assert app_client.post("/auth/magic-link/verify", json={"token": token}).status_code == 200


def test_mcp_consent_sign_in_still_uses_the_api_page(app_client):
    r = app_client.post("/oauth/sign-in", data={"email": "pat@example.org", "next": "/oauth/consent?request=x"})
    assert r.status_code == 200 and "token=" not in r.text  # the link is emailed, never shown
    link = emailed_link(app_client, "pat@example.org", "/auth/magic-link/verify")
    assert "/auth/magic-link/verify?token=" in link and APP not in link


def test_invitations_open_the_web_app(app_client, draft):
    payer = sign_in(app_client, "officer@harbor.example.org")
    org = new_org(app_client, payer, "Harbor Foundation", "funder@example.org")
    r = app_client.post(f"/orgs/{org}/agreements", json={**draft, "counterparty_email": PAYEE_EMAIL}, headers=h(payer))
    assert r.status_code == 201 and "dev_links" not in r.json()  # links are emailed, never returned
    assert emailed_link(app_client, PAYEE_EMAIL, "/invitations").startswith(f"{APP}/invitations/accept?token=")
    r = app_client.post(f"/orgs/{org}/invitations", json={"email": "colleague@harbor.example.org"}, headers=h(payer))
    assert r.status_code == 201 and "dev_link" not in r.json()
    assert emailed_link(app_client, "colleague@harbor.example.org", "/invitations").startswith(
        f"{APP}/invitations/accept?token="
    )


def test_without_an_app_links_use_the_api(client):
    assert client.post("/auth/magic-link", json={"email": "pat@example.org"}).json() == {"sent": True}
    assert "/auth/magic-link/verify?token=" in emailed_link(client, "pat@example.org", "/auth/")


def test_accept_invitation_by_id_from_the_inbox(client, draft):
    payer = sign_in(client, "officer@harbor.example.org")
    org = new_org(client, payer, "Harbor Foundation", "funder@example.org")
    client.post(f"/orgs/{org}/agreements", json={**draft, "counterparty_email": PAYEE_EMAIL}, headers=h(payer))

    stranger = sign_in(client, "someone@else.example.org")
    payee = sign_in(client, PAYEE_EMAIL)
    [inv] = client.get("/me", headers=h(payee)).json()["invitations"]
    body = {"new_org": {"name": "Riverside Reads", "paypal_email": "grantee@example.org"}}
    r = client.post(f"/invitations/{inv['id']}/accept", json=body, headers=h(stranger))
    assert r.status_code == 403 and PAYEE_EMAIL in r.json()["detail"]  # only the invited email can accept
    r = client.post(f"/invitations/{inv['id']}/accept", json=body, headers=h(payee))
    assert r.status_code == 200 and r.json()["organization"]["name"] == "Riverside Reads"
    assert client.post(f"/invitations/{inv['id']}/accept", json=body, headers=h(payee)).status_code == 404
    assert client.post("/invitations/nope/accept", json=body, headers=h(payee)).status_code == 404


def test_sso_started_from_the_app_returns_there(app_client, idp):  # noqa: F811
    setup_sso(app_client, idp)
    url = app_client.post("/auth/sso/start", json={"email": "pat@harbor.example.org", "to_app": True}).json()
    code, state = idp.authorize(url["authorize_url"], "pat@harbor.example.org")
    r = app_client.get("/auth/sso/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"{APP}/auth/sso#token=")
    token = r.headers["location"].split("#token=")[1]
    assert app_client.get("/me", headers=h(token)).json()["user"]["email"] == "pat@harbor.example.org"
    assert "set-cookie" not in r.headers


def test_sso_to_app_needs_an_app(client, idp):  # noqa: F811
    setup_sso(client, idp)
    r = client.post("/auth/sso/start", json={"email": "pat@harbor.example.org", "to_app": True})
    assert r.status_code == 422


def test_paypal_status_says_whether_onboarding_started(client):
    _, _, ctx = create_active(client, dict(GOOD_DRAFT))
    url = f"/orgs/{ctx['payee_org']}/paypal"
    assert client.get(url, headers=h(ctx["payee"])).json()["started"] is False
    client.post(f"/orgs/{ctx['payee_org']}/paypal/connect", headers=h(ctx["payee"]))
    started = client.get(url, headers=h(ctx["payee"])).json()
    assert started["started"] is True and started["ready"] is False
