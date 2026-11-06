"""The MCP server, generated from the REST API with FastMCP.

- Tools: every REST operation tagged `x-mcp-tool` becomes a tool named by that tag. Human-only steps
  (confirming criteria, approving releases, funding, disputing) are never tagged.
- Calls go through the real REST endpoint in-process, carrying the caller's verified credential, so
  permissions and the audit log are enforced in one place. (FastMCP strips Authorization by default
  because forwarding a client's token to *another* service is unsafe; here the API is this same resource
  server, with the same token store and audience.)
- Auth: OAuth 2.1 for interactive clients (DatabaseOAuthProvider: discovery, dynamic registration, PKCE,
  consent, org-bound tokens) and org API keys for headless agents.
"""

from typing import Any

import httpx
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.auth import MultiAuth, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.providers.openapi.routing import MCPType
from mcp.server.auth.provider import AccessToken
from mcp.types import ToolAnnotations
from sqlmodel import select

from app.db import as_utc, utcnow
from app.models import ApiToken, TokenKind
from app.services.access import hash_token
from app.services.oauth import SCOPE, DatabaseOAuthProvider

INSTRUCTIONS = """\
Milestone disbursement engine: grant and SOW payments released when evidence meets agreed criteria.
You act for one organization with the rights of the person (or API key) that connected you.
Typical flows: list_agreements -> get_agreement_status -> list_evidence / get_verdict / explain_milestone;
payees (an agent doing the work): open_work lists the tranches you can earn with their criteria and what a
rejection asked for; precheck_evidence a draft, then submit_evidence; get_verdict shows what was decided.
Payers: review_queue and get_portfolio.
Confirming criteria, approving releases, funding and disputes are done by people in the app, not by tools.
"""


class ApiKeyVerifier(TokenVerifier):
    """Org API keys (mdk_...) for headless agents."""

    def __init__(self, state, resource_url: str):
        super().__init__(required_scopes=[SCOPE])
        self._state = state
        self._resource = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith("mdk_"):
            return None
        with self._state.sessionmaker() as s:
            row = s.exec(select(ApiToken).where(ApiToken.token_hash == hash_token(token))).first()
            if row is None or row.kind != TokenKind.api_key or row.revoked_at is not None:
                return None
            if row.expires_at and as_utc(row.expires_at) < utcnow():
                return None
            return AccessToken(
                token=token,
                client_id=f"apikey:{row.id}",
                scopes=[SCOPE],
                resource=self._resource,
                claims={"org_id": row.org_id},
            )


async def _attach_caller(request: httpx.Request) -> None:
    """In-process REST call made by a tool: carry the MCP caller's verified credential."""
    token = get_access_token()
    if token is not None:
        request.headers["Authorization"] = f"Bearer {token.token}"


def _tool_names(app: FastAPI) -> dict[str, str]:
    names = {}
    for methods in app.openapi()["paths"].values():
        for op in methods.values():
            if isinstance(op, dict) and op.get("x-mcp-tool"):
                names[op["operationId"]] = op["x-mcp-tool"]
    return names


def _route_map_fn(route: Any, mcp_type: MCPType) -> MCPType:
    return MCPType.TOOL if route.extensions.get("x-mcp-tool") else MCPType.EXCLUDE


# name -> (title, read_only, idempotent, open_world). Clients use these hints to decide when to ask the person
# before running a tool (ChatGPT and Claude treat tools without readOnlyHint as writes).
TOOL_HINTS: dict[str, tuple[str, bool, bool, bool]] = {
    "list_agreements": ("List agreements", True, True, False),
    "get_agreement_status": ("Get agreement status", True, True, False),
    "review_queue": ("Review queue", True, True, False),
    "open_work": ("Open work (payee)", True, True, False),
    "get_portfolio": ("Funding portfolio", True, True, False),
    "list_evidence": ("List evidence", True, True, False),
    "get_verdict": ("Latest verdict", True, True, False),
    "explain_milestone": ("Explain a tranche (audit log)", True, True, False),
    "export_compliance_packet": ("Compliance packet", True, True, False),
    "lint_agreement_criteria": ("Check criteria", True, True, False),
    "draft_agreement_from_text": ("Draft agreement from text", True, False, False),
    "precheck_evidence": ("Pre-check evidence", True, False, False),
    "create_agreement": ("Create agreement and invite", False, False, True),  # emails invitations
    "submit_evidence": ("Submit evidence", False, True, False),  # identical evidence is deduplicated
}
SECURITY_SCHEMES = [{"type": "oauth2", "scopes": [SCOPE]}]  # ChatGPT: per-tool auth declaration


def _component_fn(route: Any, component: Any) -> None:
    name = route.extensions.get("x-mcp-tool")
    title, read_only, idempotent, open_world = TOOL_HINTS.get(name, (route.summary or name, False, False, False))
    component.title = title
    component.annotations = ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=False,  # nothing an agent can call deletes or overwrites anything
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )
    component.meta = {**(component.meta or {}), "securitySchemes": SECURITY_SCHEMES}


def build_mcp(app: FastAPI, base_url: str, api_base_url: str | None = None) -> tuple[FastMCP, DatabaseOAuthProvider]:
    oauth = DatabaseOAuthProvider(base_url=base_url, state=app.state, consent_base_url=api_base_url)
    auth = MultiAuth(server=oauth, verifiers=[ApiKeyVerifier(app.state, oauth.resource_url)])
    mcp = FastMCP.from_fastapi(
        app,
        name="milestone-disbursement-engine",
        instructions=INSTRUCTIONS,
        route_map_fn=_route_map_fn,
        mcp_component_fn=_component_fn,
        mcp_names=_tool_names(app),
        httpx_client_kwargs={"event_hooks": {"request": [_attach_caller]}, "base_url": "http://mde.internal"},
        auth=auth,
    )
    return mcp, oauth
