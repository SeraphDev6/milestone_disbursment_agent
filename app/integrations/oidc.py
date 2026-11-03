"""OpenID Connect relying party: discovery, PKCE authorization URL, code exchange, ID token validation."""

import base64
import hashlib
import secrets
import time
from collections.abc import Callable
from urllib.parse import urlencode

import dns.resolver
import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

ALLOWED_ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]
CLOCK_SKEW = 120  # seconds


class OIDCError(Exception):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class OIDCClient:
    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = 10.0):
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)
        self._discovery: dict[str, tuple[float, dict]] = {}

    def _get_json(self, url: str) -> dict:
        try:
            r = self._http.get(url)
        except httpx.HTTPError as e:
            raise OIDCError(f"identity provider unreachable: {e}") from e
        if r.status_code != 200:
            raise OIDCError(f"identity provider returned {r.status_code} for {url}")
        return r.json()

    def discover(self, issuer: str) -> dict:
        issuer = issuer.rstrip("/")
        cached = self._discovery.get(issuer)
        if cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        if not issuer.startswith("https://") and not issuer.startswith("http://localhost"):
            raise OIDCError("issuer must be an https URL")
        doc = self._get_json(f"{issuer}/.well-known/openid-configuration")
        if doc.get("issuer", "").rstrip("/") != issuer:
            raise OIDCError("discovery document's issuer doesn't match the configured issuer")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not doc.get(key):
                raise OIDCError(f"discovery document has no {key}")
        self._discovery[issuer] = (time.monotonic(), doc)
        return doc

    def authorize_url(
        self,
        issuer: str,
        client_id: str,
        redirect_uri: str,
        state: str,
        nonce: str,
        challenge: str,
        login_hint: str | None,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{self.discover(issuer)['authorization_endpoint']}?{urlencode(params)}"

    def exchange(
        self, issuer: str, client_id: str, client_secret: str, code: str, redirect_uri: str, code_verifier: str
    ) -> str:
        """Trade the authorization code for tokens; returns the raw ID token."""
        try:
            r = self._http.post(
                self.discover(issuer)["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
                auth=(client_id, client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise OIDCError(f"identity provider unreachable: {e}") from e
        if r.status_code != 200:
            raise OIDCError(f"code exchange failed ({r.status_code})")
        id_token = r.json().get("id_token")
        if not id_token:
            raise OIDCError("identity provider returned no ID token")
        return id_token

    def validate(self, issuer: str, client_id: str, id_token: str, nonce: str) -> dict:
        """Signature (provider's JWKS), issuer, audience, expiry, issued-at and nonce. Returns the claims."""
        keys = KeySet.import_key_set(self._get_json(self.discover(issuer)["jwks_uri"]))
        try:
            token = jwt.decode(id_token, keys, algorithms=ALLOWED_ALGS)
        except (JoseError, ValueError) as e:
            raise OIDCError(f"ID token signature is invalid: {e}") from e
        claims = token.claims
        try:
            jwt.JWTClaimsRegistry(
                leeway=CLOCK_SKEW,
                iss={"essential": True, "value": issuer.rstrip("/")},
                exp={"essential": True},
                iat={"essential": True},
                nonce={"essential": True, "value": nonce},
            ).validate(claims)
        except JoseError as e:
            raise OIDCError(f"ID token rejected: {e}") from e
        aud = claims.get("aud")
        audiences = aud if isinstance(aud, list) else [aud]
        if client_id not in audiences or (len(audiences) > 1 and claims.get("azp") != client_id):
            raise OIDCError("ID token wasn't issued to this application")
        return claims


TxtLookup = Callable[[str], list[str]]


def lookup_txt(name: str) -> list[str]:
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=5)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return []
    return [b"".join(r.strings).decode(errors="replace") for r in answers]
