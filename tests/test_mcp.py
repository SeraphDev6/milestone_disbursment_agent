"""The MCP server: discovery, the OAuth 2.1 flow an MCP client runs, tools, and the guarantees around them."""

import base64
import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import GOOD_DRAFT, hermetic_settings
from tests.helpers import create_active, h, token_from
from tests.test_verification import SIGN_IN

BASE = "http://localhost"  # the MCP SDK requires https issuers except localhost
RESOURCE = f"{BASE}/mcp"
REDIRECT = "http://localhost:3333/callback"


@pytest.fixture
def client():
    with TestClient(create_app(hermetic_settings(PUBLIC_BASE_URL=BASE)), base_url=BASE) as c:
        yield c


def rpc(client, token, method, params=None):
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
    )
    return r


def call(client, token, tool, **arguments):
    r = rpc(client, token, "tools/call", {"name": tool, "arguments": arguments})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    text = result["content"][0]["text"] if result.get("content") else ""
    body = json.loads(text) if text.startswith(("{", "[")) else text
    if isinstance(body, dict) and set(body) == {"result"}:  # FastMCP wraps non-object results
        body = body["result"]
    return result.get("isError", False), body


def rejected(response) -> bool:
    """The MCP SDK answers grant errors with 401 + invalid_grant (RFC 6749 suggests 400); clients key off `error`."""
    return response.status_code in (400, 401) and response.json()["error"] == "invalid_grant"


def path(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


# --- the OAuth flow, as an MCP client runs it ------------------------------------------


def register(client) -> str:
    r = client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT],
            "client_name": "Test Agent",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "mcp",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def start_authorize(client, client_id: str, challenge: str, resource: str = RESOURCE):
    q = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-123",
        "scope": "mcp",
        "resource": resource,
    }
    return client.get(f"/authorize?{urlencode(q)}", follow_redirects=False)


def browser_sign_in(client, consent_url: str, email: str) -> None:
    page = client.post("/oauth/sign-in", data={"email": email, "next": path(consent_url)})
    link = re.search(r"href='([^']+)'", page.text).group(1).replace("&amp;", "&")
    q = parse_qs(urlsplit(link).query)
    r = client.post(
        "/auth/magic-link/complete", data={"token": q["token"][0], "next": q["next"][0]}, follow_redirects=False
    )
    assert r.status_code == 303 and "mde_session" in r.cookies


def consent_form(client, consent_url: str) -> dict:
    page = client.get(path(consent_url)).text
    return {
        "request": re.search(r"name=request value='([^']+)'", page).group(1),
        "csrf": re.search(r"name=csrf value='([^']+)'", page).group(1),
        "orgs": re.findall(r"name=org_id value='([^']+)'", page),
    }


def connect(client, email: str, org_id: str | None = None) -> dict:
    """Full flow: register, authorize (PKCE), sign in, consent for an org, exchange the code."""
    client.cookies.clear()
    client_id = register(client)
    verifier, challenge = pkce()
    consent_url = start_authorize(client, client_id, challenge).headers["location"]
    browser_sign_in(client, consent_url, email)
    form = consent_form(client, consent_url)
    r = client.post(
        "/oauth/consent",
        data={
            "request": form["request"],
            "csrf": form["csrf"],
            "decision": "approve",
            "org_id": org_id or form["orgs"][0],
        },
        follow_redirects=False,
    )
    location = r.headers["location"]
    assert location.startswith(REDIRECT), location
    q = parse_qs(urlsplit(location).query)
    assert q["state"] == ["st-123"]
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )
    assert r.status_code == 200, r.text
    return {**r.json(), "client_id": client_id}


# --- tests -----------------------------------------------------------------------------


def test_discovery_and_challenge(client):
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == RESOURCE and prm["scopes_supported"] == ["mcp"]
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["code_challenge_methods_supported"] == ["S256"] and asm["registration_endpoint"].endswith("/register")
    r = rpc(client, "nope", "tools/list")
    assert r.status_code == 401
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]


def test_oauth_flow_gives_org_bound_tools(client):
    aid, (_m1, _), ctx = create_active(client, GOOD_DRAFT)
    tokens = connect(client, "officer@harbor.example.org")
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600 and tokens["scope"] == "mcp"

    names = {t["name"] for t in rpc(client, tokens["access_token"], "tools/list").json()["result"]["tools"]}
    # Exactly the tagged operations; confirming, reviewing, releasing, funding and disputing stay with people.
    assert names == {
        "create_agreement",
        "draft_agreement_from_text",
        "explain_milestone",
        "export_compliance_packet",
        "get_agreement_status",
        "get_portfolio",
        "get_verdict",
        "lint_agreement_criteria",
        "list_agreements",
        "list_evidence",
        "precheck_evidence",
        "review_queue",
        "submit_evidence",
    }

    is_error, body = call(client, tokens["access_token"], "get_agreement_status", agreement_id=aid)
    assert not is_error and body["id"] == aid and body["status"] == "active"
    is_error, body = call(client, tokens["access_token"], "list_agreements", org_id=ctx["payer_org"], side="payer")
    assert not is_error and body[0]["id"] == aid


def test_tools_respect_permissions_and_audit_names_the_client(client):
    _aid, (m1, _), ctx = create_active(client, GOOD_DRAFT)
    payer = connect(client, "officer@harbor.example.org")
    is_error, body = call(
        client,
        payer["access_token"],
        "submit_evidence",
        milestone_id=m1,
        evidence_type="spreadsheet",
        title="s.csv",
        content=SIGN_IN,
    )
    assert is_error and "403" in str(body)  # the payer side can't submit evidence, via MCP or otherwise

    payee = connect(client, "director@riverside.example.org")
    is_error, body = call(
        client,
        payee["access_token"],
        "submit_evidence",
        milestone_id=m1,
        evidence_type="spreadsheet",
        title="s.csv",
        content=SIGN_IN,
    )
    assert not is_error and body["evidence_id"]
    audit = client.get(f"/milestones/{m1}/audit", headers=h(ctx["payee"])).json()
    assert f"+mcp:{payee['client_id'][:24]}" in audit[0]["actor"]


def test_token_is_bound_to_the_chosen_org(client):
    aid, _, ctx = create_active(client, GOOD_DRAFT)
    # The payer's person also runs a second org; they connect the agent for that one.
    other_org = client.post("/orgs", json={"name": "Side Project"}, headers=h(ctx["payer"])).json()["id"]
    tokens = connect(client, "officer@harbor.example.org", org_id=other_org)
    is_error, body = call(client, tokens["access_token"], "get_agreement_status", agreement_id=aid)
    assert is_error and "404" in str(body)  # the Harbor agreement isn't visible through a Side Project token


def test_refresh_rotates_and_reuse_revokes_the_family(client):
    create_active(client, GOOD_DRAFT)
    t1 = connect(client, "officer@harbor.example.org")
    refresh = lambda rt: client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": t1["client_id"],
        },
    )
    r = refresh(t1["refresh_token"])
    assert r.status_code == 200
    t2 = r.json()
    assert t2["refresh_token"] != t1["refresh_token"]
    assert rpc(client, t2["access_token"], "tools/list").status_code == 200

    assert rejected(refresh(t1["refresh_token"]))  # reused: treated as stolen
    assert rpc(client, t2["access_token"], "tools/list").status_code == 401  # whole family revoked
    assert rejected(refresh(t2["refresh_token"]))


def test_code_needs_the_right_verifier_and_works_once(client):
    create_active(client, GOOD_DRAFT)
    client_id = register(client)
    verifier, challenge = pkce()
    consent_url = start_authorize(client, client_id, challenge).headers["location"]
    browser_sign_in(client, consent_url, "officer@harbor.example.org")
    form = consent_form(client, consent_url)
    r = client.post(
        "/oauth/consent",
        data={**{k: form[k] for k in ("request", "csrf")}, "decision": "approve", "org_id": form["orgs"][0]},
        follow_redirects=False,
    )
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    exchange = lambda v: client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": v,
        },
    )
    assert rejected(exchange(pkce()[0]))  # wrong verifier
    assert exchange(verifier).status_code == 200
    assert rejected(exchange(verifier))  # code already used


def test_consent_rules(client):
    create_active(client, GOOD_DRAFT)
    client_id = register(client)
    _, challenge = pkce()
    # tokens are only issued for this server
    r = start_authorize(client, client_id, challenge, resource="https://other.example/mcp")
    assert "error=invalid_request" in r.headers.get("location", "") or r.status_code == 400

    consent_url = start_authorize(client, client_id, challenge).headers["location"]
    assert "Sign in to connect" in client.get(path(consent_url)).text  # not signed in yet
    browser_sign_in(client, consent_url, "officer@harbor.example.org")
    form = consent_form(client, consent_url)
    r = client.post(
        "/oauth/consent",
        data={"request": form["request"], "csrf": "forged", "decision": "approve", "org_id": form["orgs"][0]},
        follow_redirects=False,
    )
    assert r.status_code == 403
    r = client.post(
        "/oauth/consent",
        data={"request": form["request"], "csrf": form["csrf"], "decision": "deny"},
        follow_redirects=False,
    )
    assert "error=access_denied" in r.headers["location"]
    assert "expired" in client.get(path(consent_url)).text  # a decided request can't be reused


def test_leaving_the_org_cuts_off_the_agent(client):
    aid, _, ctx = create_active(client, GOOD_DRAFT)
    inv = client.post(
        f"/orgs/{ctx['payer_org']}/invitations",
        json={"email": "analyst@harbor.example.org", "role": "member"},
        headers=h(ctx["payer"]),
    ).json()
    client.cookies.clear()
    analyst = client.post("/auth/magic-link", json={"email": "analyst@harbor.example.org"}).json()["dev_link"]
    session = client.post("/auth/magic-link/verify", json={"token": token_from(analyst)}).json()
    client.post(
        "/invitations/accept",
        json={"token": token_from(inv["dev_link"]), "org_id": None, "new_org": None},
        headers=h(session["token"]),
    )
    tokens = connect(client, "analyst@harbor.example.org")
    assert not call(client, tokens["access_token"], "get_agreement_status", agreement_id=aid)[0]
    client.delete(f"/orgs/{ctx['payer_org']}/members/{session['user']['id']}", headers=h(ctx["payer"]))
    is_error, _ = call(client, tokens["access_token"], "get_agreement_status", agreement_id=aid)
    assert is_error


def test_org_api_key_works_for_headless_agents(client):
    _aid, (m1, _), ctx = create_active(client, GOOD_DRAFT)
    key = client.post(f"/orgs/{ctx['payee_org']}/api-keys", json={"name": "ci agent"}, headers=h(ctx["payee"])).json()
    is_error, _body = call(
        client,
        key["token"],
        "submit_evidence",
        milestone_id=m1,
        evidence_type="spreadsheet",
        title="s.csv",
        content=SIGN_IN,
    )
    assert not is_error
    client.delete(f"/orgs/{ctx['payee_org']}/api-keys/{key['id']}", headers=h(ctx["payee"]))
    assert rpc(client, key["token"], "tools/list").status_code == 401


def test_session_tokens_dont_work_on_mcp(client):
    _, _, ctx = create_active(client, GOOD_DRAFT)
    assert rpc(client, ctx["payer"], "tools/list").status_code == 401  # MCP needs an OAuth token or an API key


def test_sign_in_redirects_stay_local(client):
    from app.api.browser import safe_next

    for bad in ("https://evil.example", "//evil.example/x", "/\\evil.example", "javascript:alert(1)", ""):
        assert safe_next(bad) == "/"
    assert safe_next("/oauth/consent?request=abc") == "/oauth/consent?request=abc"
