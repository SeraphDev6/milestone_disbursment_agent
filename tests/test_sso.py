"""Org-configured OIDC SSO against a fake identity provider that signs real ID tokens."""

import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from sqlmodel import select

from app.integrations.oidc import OIDCClient
from app.models.sso import SSOConnection
from tests.helpers import h, new_org, sign_in

ISSUER = "https://idp.harbor.example"
CLIENT_ID = "mde-client"
CLIENT_SECRET = "s3cret-from-idp"


class FakeIdP:
    def __init__(self):
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1", "alg": "RS256", "use": "sig"})
        self.codes: dict[str, dict] = {}
        # what the next token will claim; tests tamper with these
        self.overrides: dict = {}
        self.sign_with: RSAKey | None = None

    def authorize(self, url: str, email: str) -> tuple[str, str]:
        """What the person does at the IdP: signs in; returns (code, state) the browser brings back."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert q["code_challenge_method"] == "S256" and q["client_id"] == CLIENT_ID
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"nonce": q["nonce"], "challenge": q["code_challenge"], "email": email}
        return code, q["state"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json=KeySet([self.key]).as_dict(private=False))
        if path == "/token":
            form = parse_qs(request.content.decode())
            grant = self.codes.pop(form["code"][0], None)
            expected_auth = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            verifier = form["code_verifier"][0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if grant is None or request.headers["authorization"] != expected_auth or challenge != grant["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            claims = {
                "iss": ISSUER,
                "aud": CLIENT_ID,
                "sub": "u1",
                "iat": now,
                "exp": now + 300,
                "nonce": grant["nonce"],
                "email": grant["email"],
                "email_verified": True,
                "name": "Pat Officer",
                **self.overrides,
            }
            token = jwt.encode({"alg": "RS256", "kid": "k1"}, claims, self.sign_with or self.key)
            return httpx.Response(200, json={"id_token": token, "access_token": "x", "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def idp(client):
    fake = FakeIdP()
    client.app.state.oidc = OIDCClient(transport=httpx.MockTransport(fake.handler))
    client.app.state.txt_lookup = lambda name: fake.txt.get(name, [])
    fake.txt = {}
    return fake


def setup_sso(client, idp, *, enforce=False, verify=True, domain="harbor.example.org"):
    owner = sign_in(client, f"owner@{domain}")
    org = new_org(client, owner, "Harbor Foundation", "funder@example.org")
    r = client.put(
        f"/orgs/{org}/sso",
        headers=h(owner),
        json={
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "domains": [domain],
            "enforce": enforce,
        },
    )
    assert r.status_code == 200, r.text
    record = r.json()["domains"][0]["dns_record"]
    if verify:
        name, value = record.split(" TXT ")
        idp.txt[name] = [value.strip('"')]
        assert client.post(f"/orgs/{org}/sso/domains/{domain}/verify", headers=h(owner)).status_code == 200
    return org, owner


def sso_sign_in(client, idp, email):
    url = client.post("/auth/sso/start", json={"email": email}).json()["authorize_url"]
    code, state = idp.authorize(url, email)
    return client.get("/auth/sso/callback", params={"code": code, "state": state})


def test_sso_sign_in_adds_the_person_to_the_org(client, idp):
    org, _ = setup_sso(client, idp)
    r = sso_sign_in(client, idp, "pat@harbor.example.org")
    assert r.status_code == 200, r.text
    me = client.get("/me", headers=h(r.json()["token"])).json()
    assert me["user"]["name"] == "Pat Officer"
    assert [(m["org_id"], m["role"]) for m in me["memberships"]] == [(org, "member")]
    assert sso_sign_in(client, idp, "pat@harbor.example.org").status_code == 200  # again: same membership


def test_state_is_single_use(client, idp):
    setup_sso(client, idp)
    url = client.post("/auth/sso/start", json={"email": "pat@harbor.example.org"}).json()["authorize_url"]
    code, state = idp.authorize(url, "pat@harbor.example.org")
    assert client.get("/auth/sso/callback", params={"code": code, "state": state}).status_code == 200
    assert client.get("/auth/sso/callback", params={"code": code, "state": state}).status_code == 401


@pytest.mark.parametrize(
    ("tamper", "status"),
    [
        ({"overrides": {"nonce": "replayed"}}, 401),
        ({"overrides": {"aud": "someone-else"}}, 401),
        ({"overrides": {"iss": "https://evil.example"}}, 401),
        ({"overrides": {"exp": int(time.time()) - 3600}}, 401),
        ({"overrides": {"email_verified": False}}, 401),
        ({"sign_with": RSAKey.generate_key(2048, parameters={"kid": "k1"})}, 401),
    ],
)
def test_bad_id_tokens_are_rejected(client, idp, tamper, status):
    setup_sso(client, idp)
    for attr, value in tamper.items():
        setattr(idp, attr, value)
    assert sso_sign_in(client, idp, "pat@harbor.example.org").status_code == status


def test_idp_cant_vouch_for_other_domains(client, idp):
    setup_sso(client, idp)
    idp.overrides = {"email": "ceo@another-company.example"}
    r = sso_sign_in(client, idp, "pat@harbor.example.org")
    assert r.status_code == 403 and "verified domains" in r.json()["detail"]


def test_unverified_and_public_domains_dont_count(client, idp):
    setup_sso(client, idp, verify=False)
    assert client.post("/auth/sso/start", json={"email": "pat@harbor.example.org"}).status_code == 404
    owner = sign_in(client, "someone@startup.example.org")
    org = new_org(client, owner, "Startup")
    r = client.put(
        f"/orgs/{org}/sso",
        headers=h(owner),
        json={
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "domains": ["gmail.com"],
        },
    )
    assert r.status_code == 422 and "public" in r.json()["detail"]


def test_a_domain_belongs_to_one_org(client, idp):
    setup_sso(client, idp)
    other = sign_in(client, "squatter@elsewhere.example.org")
    org = new_org(client, other, "Squatter")
    r = client.put(
        f"/orgs/{org}/sso",
        headers=h(other),
        json={
            "issuer": ISSUER,
            "client_id": CLIENT_ID,
            "client_secret": "x",
            "domains": ["harbor.example.org"],
        },
    )
    record = r.json()["domains"][0]["dns_record"]
    name, value = record.split(" TXT ")
    idp.txt[name] = [value.strip('"')]  # even with the TXT record, the first org keeps it
    assert client.post(f"/orgs/{org}/sso/domains/harbor.example.org/verify", headers=h(other)).status_code == 409


def test_enforced_sso_refuses_magic_links(client, idp):
    setup_sso(client, idp, enforce=True)
    r = client.post("/auth/magic-link", json={"email": "pat@harbor.example.org"})
    assert r.status_code == 409 and "single sign-on" in r.json()["detail"]
    assert client.post("/auth/magic-link", json={"email": "pat@unrelated.example.org"}).status_code == 200


def test_only_admins_configure_and_secret_is_encrypted(client, idp):
    org, owner = setup_sso(client, idp)
    member = sso_sign_in(client, idp, "pat@harbor.example.org").json()["token"]
    assert client.get(f"/orgs/{org}/sso", headers=h(member)).status_code == 403
    view = client.get(f"/orgs/{org}/sso", headers=h(owner)).json()
    assert "client_secret" not in view and view["redirect_uri"].endswith("/auth/sso/callback")
    with client.app.state.sessionmaker() as s:
        stored = s.exec(select(SSOConnection)).one().client_secret_encrypted
    assert CLIENT_SECRET not in stored
