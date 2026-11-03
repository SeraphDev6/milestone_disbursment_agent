"""Compatibility with real MCP clients, replaying each vendor's documented behaviour (see docs/MCP_CLIENTS.md).

CIMD documents below are the ones the vendors publish (fetched Nov 2026); fetching is faked so tests stay offline.
"""

import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastmcp.server.auth.cimd import CIMDDocument, CIMDFetcher, CIMDFetchError

from tests.conftest import GOOD_DRAFT
from tests.helpers import create_active
from tests.test_mcp import BASE, RESOURCE, browser_sign_in, client, consent_form, path, pkce, rpc  # noqa: F401

CLAUDE_CODE = "https://claude.ai/oauth/claude-code-client-metadata"
CHATGPT = "https://chatgpt.com/oauth/client.json"
CIMD_DOCS = {
    CLAUDE_CODE: {
        "client_id": CLAUDE_CODE,
        "client_name": "Claude Code",
        "client_uri": "https://claude.ai",
        "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    },
    CHATGPT: {
        "client_id": CHATGPT,
        "client_name": "ChatGPT",
        "client_uri": "https://chatgpt.com/",
        "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        "token_endpoint_auth_method": "private_key_jwt",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "jwks_uri": "https://chatgpt.com/oauth/jwks.json",
    },
}


class OfflineCIMDFetcher(CIMDFetcher):
    async def fetch(self, client_id_url: str) -> CIMDDocument:
        if client_id_url not in CIMD_DOCS:
            raise CIMDFetchError("unreachable")
        return CIMDDocument.model_validate(CIMD_DOCS[client_id_url])


@pytest.fixture
def server(client):  # noqa: F811
    client.app.state.oauth.cimd_fetcher = OfflineCIMDFetcher()
    create_active(client, GOOD_DRAFT)
    return client


def authorize(c, client_id: str, redirect_uri: str, scope: str = "mcp offline_access", resource: str = RESOURCE):
    verifier, challenge = pkce()
    q = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "s1",
        "scope": scope,
        "resource": resource,
    }
    return c.get(f"/authorize?{urlencode(q)}", follow_redirects=False), verifier


def complete(
    c,
    client_id: str,
    redirect_uri: str,
    *,
    secret: str | None = None,
    scope="mcp offline_access",
    resource: str = RESOURCE,
) -> dict:
    """Run a client's flow from /authorize to tokens. Returns the token response."""
    c.cookies.clear()
    r, verifier = authorize(c, client_id, redirect_uri, scope, resource)
    assert r.status_code in (302, 307), r.text
    consent_url = r.headers["location"]
    browser_sign_in(c, consent_url, "officer@harbor.example.org")
    form = consent_form(c, consent_url)
    r = c.post(
        "/oauth/consent",
        data={"request": form["request"], "csrf": form["csrf"], "decision": "approve", "org_id": form["orgs"][0]},
        follow_redirects=False,
    )
    assert r.headers["location"].startswith(redirect_uri), r.headers["location"]
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": resource,
    }
    if secret:
        data["client_secret"] = secret
    r = c.post("/token", data=data)  # form-urlencoded, as every client sends it
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert rpc(c, tokens["access_token"], "tools/list").status_code == 200
    return tokens


def register(c, **metadata) -> dict:
    r = c.post(
        "/register",
        json={
            "client_name": "x",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            **metadata,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- discovery, as each vendor reads it ------------------------------------------------


def test_metadata_meets_every_vendors_rules(server):
    prm = server.get("/.well-known/oauth-protected-resource/mcp").json()
    assert server.get("/.well-known/oauth-protected-resource").json() == prm  # ChatGPT's documented location
    assert prm["resource"] == RESOURCE  # Claude: must equal the URL the user enters, path included
    assert prm["scopes_supported"] == ["mcp"]  # minimal resource scopes
    assert len(prm["authorization_servers"]) == 1  # Claude uses only the first entry

    asm = server.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == prm["authorization_servers"][0]  # ChatGPT: exact string match
    assert "S256" in asm["code_challenge_methods_supported"]  # everyone; MCP spec: clients refuse without it
    assert asm["client_id_metadata_document_supported"] is True  # Claude + ChatGPT prefer CIMD
    assert "none" in asm["token_endpoint_auth_methods_supported"]  # Claude requires it to use CIMD
    assert asm["registration_endpoint"].endswith("/register")  # DCR fallback (VS Code, Cursor, Gemini CLI)
    assert "offline_access" in asm["scopes_supported"]  # Claude appends it to get refresh tokens
    assert {"authorization_code", "refresh_token"} <= set(asm["grant_types_supported"])
    assert "authorization_response_iss_parameter_supported" not in asm  # ChatGPT rejects it if advertised but unmet

    r = rpc(server, "missing", "tools/list")
    assert r.status_code == 401  # Claude ignores WWW-Authenticate on anything but 401
    challenge = r.headers["www-authenticate"]
    assert 'resource_metadata="' in challenge and 'scope="mcp"' in challenge


def test_cors_for_browser_based_clients(server):
    for target in ("/mcp", "/token", "/register", "/.well-known/oauth-authorization-server"):
        r = server.options(
            target,
            headers={
                "Origin": "http://localhost:6274",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,mcp-protocol-version",
            },
        )
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*", (target, r.status_code)
    r = rpc(server, "missing", "tools/list")
    assert r.status_code == 401  # actual request still answered; header exposure configured for browsers


# --- each client's sign-in ------------------------------------------------------------


def test_claude_ai_dcr(server):
    callback = "https://claude.ai/api/mcp/auth_callback"
    reg = register(server, redirect_uris=[callback], token_endpoint_auth_method="none", scope="mcp offline_access")
    tokens = complete(server, reg["client_id"], callback)
    assert tokens["refresh_token"] and "offline_access" in tokens["scope"]
    r = server.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": reg["client_id"]},
    )
    assert r.status_code == 200 and r.json()["refresh_token"] != tokens["refresh_token"]  # rotated


def test_claude_code_cimd_with_ephemeral_loopback_port(server):
    for redirect in ("http://localhost:51853/callback", "http://127.0.0.1:3118/callback"):
        tokens = complete(server, CLAUDE_CODE, redirect)
        assert tokens["refresh_token"]


def test_consent_names_cimd_client_and_warns_about_loopback(server):
    r, _ = authorize(server, CLAUDE_CODE, "http://localhost:51853/callback")
    consent_url = r.headers["location"]
    browser_sign_in(server, consent_url, "officer@harbor.example.org")
    page = server.get(path(consent_url)).text
    assert "Claude Code" in page and "claude.ai" in page and "runs on your computer" in page


def test_chatgpt_cimd_public_client(server):
    # ChatGPT's document prefers private_key_jwt but also supports "none"; we only advertise none for public
    # clients, so the intersection is none and no client authentication is sent.
    tokens = complete(server, CHATGPT, "https://chatgpt.com/connector_platform_oauth_redirect")
    assert tokens["refresh_token"]


def test_chatgpt_dcr_confidential_client(server):
    callback = "https://chatgpt.com/connector/oauth/abc123"
    reg = register(server, redirect_uris=[callback], token_endpoint_auth_method="client_secret_post")
    assert reg["client_secret"]
    complete(server, reg["client_id"], callback, secret=reg["client_secret"])


def test_ide_clients_dcr_loopback(server):  # VS Code, Cursor, Gemini CLI register an exact loopback redirect
    callback = "http://127.0.0.1:33418/"
    reg = register(server, redirect_uris=[callback], token_endpoint_auth_method="none")
    complete(server, reg["client_id"], callback, scope="mcp")


# --- what must still be refused --------------------------------------------------------


@pytest.mark.parametrize(
    "redirect",
    ["https://evil.example/callback", "http://localhost:51853/other", "http://localhost@evil.example/callback"],
)
def test_cimd_redirects_must_match_the_document(server, redirect):
    r, _ = authorize(server, CLAUDE_CODE, redirect)
    assert r.status_code == 400 and "evil" not in r.headers.get("location", "")


def test_unreachable_cimd_document_is_an_unknown_client(server):
    r, _ = authorize(server, "https://unknown.example/client.json", "https://unknown.example/cb")
    assert r.status_code == 400


def test_resource_must_be_this_server(server):
    ok, _ = authorize(
        server, CHATGPT, "https://chatgpt.com/connector_platform_oauth_redirect", resource="HTTP://LOCALHOST/mcp/"
    )
    assert "/oauth/consent" in ok.headers["location"]  # case and trailing slash tolerated
    bad, _ = authorize(
        server, CHATGPT, "https://chatgpt.com/connector_platform_oauth_redirect", resource="https://other.example/mcp"
    )
    assert "error=invalid_request" in bad.headers["location"]


def test_unsupported_scopes_are_refused(server):
    r, _ = authorize(server, CLAUDE_CODE, "http://localhost:51853/callback", scope="mcp admin")
    assert "error=invalid_scope" in r.headers["location"]


# --- tool definitions every client accepts ---------------------------------------------


def test_tool_definitions_are_portable(server):
    key = None
    ctx = create_active(server, GOOD_DRAFT)[2]
    key = server.post(
        f"/orgs/{ctx['payee_org']}/api-keys",
        json={"name": "probe"},
        headers={"Authorization": f"Bearer {ctx['payee']}"},
    ).json()["token"]
    tools = rpc(server, key, "tools/list").json()["result"]["tools"]
    server_name = "milestone-disbursement-engine"
    for t in tools:
        blob = json.dumps({k: t.get(k) for k in ("inputSchema", "outputSchema")})
        assert "$ref" not in blob and "$defs" not in blob, t["name"]  # Gemini, OpenAI strict, some Claude Code builds
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", t["name"])  # OpenAI and Anthropic tool name rule
        assert len(f"{server_name}_{t['name']}") <= 60  # Cursor's combined-name limit (reported)
        assert t["inputSchema"]["type"] == "object"
        a = t["annotations"]
        assert a["title"] and isinstance(a["readOnlyHint"], bool) and a["destructiveHint"] is False
        assert t["_meta"]["securitySchemes"] == [{"type": "oauth2", "scopes": ["mcp"]}]  # ChatGPT
    read_only = {t["name"] for t in tools if t["annotations"]["readOnlyHint"]}
    assert {"create_agreement", "submit_evidence"}.isdisjoint(read_only)
    assert len(tools) <= 40  # comfortably under client tool caps
