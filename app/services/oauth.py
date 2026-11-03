"""OAuth 2.1 authorization server for MCP clients, backed by our database.

FastMCP / the MCP SDK provide the protocol surface (metadata, dynamic registration, /authorize, /token,
/revoke, PKCE verification, the 401 challenge). This class supplies storage and policy:
- authorize() parks the request and sends the person to our consent page (sign in, pick an org, approve);
- codes are one-time, short-lived, bound to the person, the org and the PKCE challenge;
- access tokens last an hour, act only within the chosen org, and are only valid for this server's /mcp;
- refresh tokens rotate on every use; presenting a used one revokes the whole family (theft detection).

Client compatibility (see docs/MCP_CLIENTS.md): clients identify themselves by Dynamic Client Registration
or by a Client ID Metadata Document (an https URL as client_id, fetched SSRF-safely). CIMD clients are public
(token auth "none") and their loopback redirects match on any port (RFC 8252), as Claude Code needs.
"""

import secrets
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from fastmcp.server.auth import OAuthProvider
from fastmcp.server.auth.cimd import CIMDDocument, CIMDFetcher, CIMDFetchError, CIMDValidationError
from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.routes import MetadataHandler, build_metadata
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import (
    InvalidRedirectUriError,
    InvalidScopeError,
    OAuthClientInformationFull,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import AnyHttpUrl, AnyUrl, PrivateAttr
from sqlmodel import Session, col, select
from starlette.routing import Route

from app.db import as_utc, new_id, utcnow
from app.models import ApiToken, TokenKind
from app.models.oauth import OAuthClient, OAuthCode, OAuthPendingAuthorization
from app.services.access import hash_token

SCOPE = "mcp"
# offline_access is accepted (Claude appends it when advertised; ChatGPT relies on refresh tokens). Refresh
# tokens are issued either way.
SCOPES = [SCOPE, "offline_access"]
TOKEN_AUTH_METHODS = ["none", "client_secret_post", "client_secret_basic"]
CODE_TTL = timedelta(minutes=5)
PENDING_TTL = timedelta(minutes=15)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)


def _epoch(dt: datetime | None) -> int | None:
    return int(as_utc(dt).timestamp()) if dt else None


def canonical_resource(uri: str) -> str:
    """RFC 8707 resource as clients send it: tolerate host/scheme case and a trailing slash, nothing else."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(uri.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


class CIMDClient(OAuthClientInformationFull):
    """A client identified by its Client ID Metadata Document. Public client; redirects checked against the
    document with RFC 8252 loopback port flexibility."""

    _doc: CIMDDocument = PrivateAttr()
    _fetcher: CIMDFetcher = PrivateAttr()

    @classmethod
    def from_document(cls, doc: CIMDDocument, fetcher: CIMDFetcher) -> "CIMDClient":
        client = cls(
            client_id=str(doc.client_id),
            client_name=doc.client_name,
            client_uri=doc.client_uri,
            logo_uri=doc.logo_uri,
            redirect_uris=[AnyUrl(u) for u in doc.redirect_uris],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope=" ".join(SCOPES),
        )
        client._doc, client._fetcher = doc, fetcher
        return client

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is None:
            if len(self._doc.redirect_uris) == 1 and "localhost" not in self._doc.redirect_uris[0]:
                return AnyUrl(self._doc.redirect_uris[0])
            raise InvalidRedirectUriError("redirect_uri is required for this client")
        if not self._fetcher.validate_redirect_uri(self._doc, str(redirect_uri)):
            raise InvalidRedirectUriError(f"redirect_uri {redirect_uri} isn't in the client's metadata document")
        return redirect_uri

    def validate_scope(self, requested_scope: str | None) -> list[str] | None:
        if requested_scope is None:
            return [SCOPE]
        requested = requested_scope.split()
        if not set(requested) <= set(SCOPES):
            raise InvalidScopeError(f"unsupported scopes: {' '.join(sorted(set(requested) - set(SCOPES)))}")
        return requested


class DatabaseOAuthProvider(OAuthProvider):
    def __init__(self, *, base_url: str, state, consent_base_url: str | None = None):
        """base_url: the host root, where the MCP server and its OAuth endpoints are; consent_base_url: where
        the REST API's browser pages (the consent screen) are, when the API lives under a prefix."""
        super().__init__(
            base_url=base_url,
            issuer_url=base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[SCOPE],
        )
        self._state = state  # app.state: sessionmaker exists only once the app has started
        self.base_url_str = base_url.rstrip("/")
        self.consent_base_url = (consent_base_url or base_url).rstrip("/")
        self.resource_url = f"{self.base_url_str}/mcp"
        self.cimd_fetcher = CIMDFetcher()  # SSRF-safe (https only, no private IPs, size/time limits, no redirects)

    # --- discovery ---------------------------------------------------------------------

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """The SDK's routes, plus what clients beyond the spec minimum need:
        - authorization-server metadata that also advertises CIMD and public clients ("none"), which Claude
          requires before using CIMD and which ChatGPT's CIMD client intersects with;
        - protected-resource metadata at the root path too (ChatGPT's documented location)."""
        routes = super().get_routes(mcp_path)
        metadata = build_metadata(
            self.issuer_url, self.service_documentation_url, self.client_registration_options, self.revocation_options
        ).model_copy(
            update={
                "client_id_metadata_document_supported": True,
                "token_endpoint_auth_methods_supported": TOKEN_AUTH_METHODS,
            }
        )
        as_path = "/.well-known/oauth-authorization-server"
        prm_paths = {"/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"}
        routes = [r for r in routes if getattr(r, "path", None) not in {as_path, *prm_paths}]
        routes.append(Route(as_path, MetadataHandler(metadata).handle, methods=["GET", "OPTIONS"]))
        # Resource metadata lists only what the resource needs (MCP spec: minimal scopes_supported); the
        # authorization server separately advertises offline_access for refresh tokens.
        prm = ProtectedResourceMetadata(
            resource=AnyHttpUrl(self.resource_url),
            authorization_servers=[AnyHttpUrl(str(self.issuer_url))],
            scopes_supported=[SCOPE],
            bearer_methods_supported=["header"],
        )
        for path in prm_paths:
            routes.append(Route(path, ProtectedResourceMetadataHandler(prm).handle, methods=["GET", "OPTIONS"]))
        return routes

    def _session(self) -> Session:
        return self._state.sessionmaker()

    # --- clients -----------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if self.cimd_fetcher.is_cimd_client_id(client_id):
            try:
                return CIMDClient.from_document(await self.cimd_fetcher.fetch(client_id), self.cimd_fetcher)
            except (CIMDFetchError, CIMDValidationError):
                return None
        with self._session() as s:
            row = s.get(OAuthClient, client_id)
            return OAuthClientInformationFull.model_validate(row.info) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Clients often register with just "mcp" (or no scope) and then ask for every scope the metadata
        # advertises, offline_access included (ChatGPT does). Let any registered client ask for both.
        scopes = set((client_info.scope or "").split()) | set(SCOPES)
        client_info = client_info.model_copy(update={"scope": " ".join(s for s in SCOPES if s in scopes)})
        with self._session() as s:
            s.add(OAuthClient(client_id=client_info.client_id, info=client_info.model_dump(mode="json")))
            s.commit()

    # --- authorization -----------------------------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and canonical_resource(params.resource) != canonical_resource(self.resource_url):
            raise AuthorizeError(error="invalid_request", error_description="tokens are only issued for this server")
        shown = {
            "_client_name": client.client_name,
            "_cimd_host": urlsplit(client.client_id).hostname if isinstance(client, CIMDClient) else None,
        }
        with self._session() as s:
            pending = OAuthPendingAuthorization(
                client_id=client.client_id,
                params=params.model_dump(mode="json") | shown,
                expires_at=utcnow() + PENDING_TTL,
            )
            s.add(pending)
            s.commit()
            return f"{self.consent_base_url}/oauth/consent?request={pending.id}"

    def issue_code(self, session: Session, pending: OAuthPendingAuthorization, user_id: str, org_id: str) -> str:
        """Called by the consent page on approval. Returns the raw one-time code."""
        params = AuthorizationParams.model_validate(pending.params)
        raw = secrets.token_urlsafe(32)
        session.add(
            OAuthCode(
                code_hash=hash_token(raw),
                client_id=pending.client_id,
                user_id=user_id,
                org_id=org_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                code_challenge=params.code_challenge,
                scopes=params.scopes or [SCOPE],
                resource=self.resource_url,
                expires_at=utcnow() + CODE_TTL,
            )
        )
        pending.used_at = utcnow()
        session.commit()
        return raw

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        with self._session() as s:
            row = s.exec(select(OAuthCode).where(OAuthCode.code_hash == hash_token(authorization_code))).first()
            if row is None or row.client_id != client.client_id or row.used_at is not None:
                return None
            if as_utc(row.expires_at) < utcnow():
                return None
            return AuthorizationCode(
                code=authorization_code,
                scopes=row.scopes,
                expires_at=as_utc(row.expires_at).timestamp(),
                client_id=row.client_id,
                code_challenge=row.code_challenge,
                redirect_uri=row.redirect_uri,
                redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
                resource=row.resource,
                subject=row.user_id,
            )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with self._session() as s:
            row = s.exec(select(OAuthCode).where(OAuthCode.code_hash == hash_token(authorization_code.code))).first()
            if row is None or row.used_at is not None:
                raise TokenError(error="invalid_grant", error_description="authorization code already used")
            row.used_at = utcnow()
            token = self._issue(s, client.client_id, row.user_id, row.org_id, row.scopes, family_id=new_id())  # type: ignore[arg-type]
            s.commit()
            return token

    # --- tokens ------------------------------------------------------------------------

    def _issue(self, s: Session, client_id: str, user_id: str, org_id: str, scopes: list[str], family_id: str):
        access, refresh = f"mda_{secrets.token_urlsafe(32)}", f"mdr_{secrets.token_urlsafe(32)}"
        now = utcnow()
        for raw, kind, ttl in (
            (access, TokenKind.oauth_access, ACCESS_TTL),
            (refresh, TokenKind.oauth_refresh, REFRESH_TTL),
        ):
            s.add(
                ApiToken(
                    kind=kind,
                    token_hash=hash_token(raw),
                    user_id=user_id,
                    org_id=org_id,
                    client_id=client_id,
                    scopes=scopes,
                    resource=self.resource_url,
                    family_id=family_id,
                    expires_at=now + ttl,
                    created_by=f"oauth:{client_id}",
                )
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=int(ACCESS_TTL.total_seconds()),
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    def _token_row(self, s: Session, raw: str, kind: TokenKind) -> ApiToken | None:
        row = s.exec(select(ApiToken).where(ApiToken.token_hash == hash_token(raw))).first()
        return row if row is not None and row.kind == kind else None

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        with self._session() as s:
            row = self._token_row(s, refresh_token, TokenKind.oauth_refresh)
            if row is None or row.client_id != client.client_id:
                return None
            if row.revoked_at is not None:
                self._revoke_family(s, row.family_id)  # a used refresh token came back: assume it was stolen
                s.commit()
                return None
            if row.expires_at and as_utc(row.expires_at) < utcnow():
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=row.client_id,
                scopes=row.scopes,
                expires_at=_epoch(row.expires_at),
                resource=row.resource,
                subject=row.user_id,
            )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        with self._session() as s:
            row = self._token_row(s, refresh_token.token, TokenKind.oauth_refresh)
            if row is None or row.revoked_at is not None:
                raise TokenError(error="invalid_grant", error_description="refresh token is no longer valid")
            if scopes and not set(scopes) <= set(row.scopes):
                raise TokenError(error="invalid_scope", error_description="can't widen scopes on refresh")
            row.revoked_at = utcnow()  # rotation: each refresh token works once
            token = self._issue(s, client.client_id, row.user_id, row.org_id, scopes or row.scopes, row.family_id)  # type: ignore[arg-type]
            s.commit()
            return token

    async def load_access_token(self, token: str) -> AccessToken | None:
        with self._session() as s:
            row = self._token_row(s, token, TokenKind.oauth_access)
            if row is None or row.revoked_at is not None or row.resource != self.resource_url:
                return None
            if row.expires_at and as_utc(row.expires_at) < utcnow():
                return None
            return AccessToken(
                token=token,
                client_id=row.client_id,
                scopes=row.scopes,
                expires_at=_epoch(row.expires_at),
                resource=row.resource,
                subject=row.user_id,
                claims={"org_id": row.org_id},
            )

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self._session() as s:
            row = s.exec(select(ApiToken).where(ApiToken.token_hash == hash_token(token.token))).first()
            if row is not None:
                self._revoke_family(s, row.family_id)
                s.commit()

    @staticmethod
    def _revoke_family(s: Session, family_id: str | None) -> None:
        if not family_id:
            return
        now = utcnow()
        for t in s.exec(select(ApiToken).where(ApiToken.family_id == family_id, col(ApiToken.revoked_at).is_(None))):
            t.revoked_at = now
