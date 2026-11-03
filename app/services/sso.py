"""Org-configured SSO: configuration, domain ownership, and the OIDC sign-in round trip.

Security rules:
- An org's SSO applies only to email domains it has proven it owns (DNS TXT), never to public mail domains,
  and a domain can be verified by one org only. Otherwise an org could sign in as anyone.
- The callback must match an unused, unexpired state; the ID token must carry that state's nonce, be signed
  by the provider's keys, be issued to this client, and name an email in one of the org's verified domains.
"""

import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from sqlmodel import Session, col, select

from app.db import as_utc, utcnow
from app.integrations.crypto import SecretBox
from app.integrations.oidc import OIDCClient, OIDCError, TxtLookup, pkce_pair
from app.models import Membership, OrgRole, User
from app.models.sso import SSOConnection, SSOConnectionRead, SSOConnectionWrite, SSODomain, SSODomainRead, SSOLoginState
from app.services import ServiceError
from app.services.identity import normalize_email, sign_in

STATE_TTL = timedelta(minutes=10)
TXT_PREFIX = "_mde-verification"
PUBLIC_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "yahoo.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "gmx.com",
    "mail.com",
    "yandex.com",
    "zoho.com",
}


class SSOError(ServiceError):
    pass


def _domain_of(email: str) -> str:
    return normalize_email(email).rsplit("@", 1)[-1]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def redirect_uri(base_url: str) -> str:
    return f"{base_url}/auth/sso/callback"


def read(conn: SSOConnection, base_url: str) -> SSOConnectionRead:
    return SSOConnectionRead.model_validate(
        conn,
        update={
            "redirect_uri": redirect_uri(base_url),
            "domains": [
                SSODomainRead(
                    domain=d.domain,
                    verified=d.verified_at is not None,
                    dns_record=f'{TXT_PREFIX}.{d.domain} TXT "mde-verification={d.verification_token}"',
                )
                for d in conn.domains
            ],
        },
    )


# --- configuration ---------------------------------------------------------------------


def configure(
    session: Session, org_id: str, body: SSOConnectionWrite, oidc: OIDCClient, box: SecretBox
) -> SSOConnection:
    if body.default_role == OrgRole.owner:
        raise SSOError("SSO can't make people owners automatically", 422)
    domains = sorted({d.strip().lower().lstrip("@") for d in body.domains})
    public = [d for d in domains if d in PUBLIC_DOMAINS]
    if public:
        raise SSOError(f"public email domains can't be claimed: {', '.join(public)}", 422)
    try:
        oidc.discover(body.issuer)  # fail now, not at someone's first sign-in
    except OIDCError as e:
        raise SSOError(str(e), 422) from e

    conn = session.exec(select(SSOConnection).where(SSOConnection.org_id == org_id)).first()
    if conn is None:
        if not body.client_secret:
            raise SSOError("client_secret is required when setting up SSO", 422)
        conn = SSOConnection(org_id=org_id, client_secret_encrypted="")
        session.add(conn)
    for field in ("issuer", "client_id", "enforce", "default_role"):
        setattr(conn, field, getattr(body, field))
    conn.issuer = body.issuer.rstrip("/")
    if body.client_secret:
        conn.client_secret_encrypted = box.encrypt(body.client_secret)

    existing = {d.domain: d for d in conn.domains}
    conn.domains = [existing.get(d) or SSODomain(domain=d, verification_token=secrets.token_hex(16)) for d in domains]
    session.commit()
    return conn


def verify_domain(session: Session, org_id: str, domain: str, txt_lookup: TxtLookup) -> SSODomain:
    conn = session.exec(select(SSOConnection).where(SSOConnection.org_id == org_id)).first()
    d = next((x for x in (conn.domains if conn else []) if x.domain == domain.lower()), None)
    if d is None:
        raise SSOError("that domain isn't part of this org's SSO setup", 404)
    elsewhere = session.exec(
        select(SSODomain).where(
            SSODomain.domain == d.domain, col(SSODomain.verified_at).is_not(None), SSODomain.id != d.id
        )
    ).first()
    if elsewhere is not None:
        raise SSOError("another organization has already verified this domain", 409)
    expected = f"mde-verification={d.verification_token}"
    if expected not in txt_lookup(f"{TXT_PREFIX}.{d.domain}"):
        raise SSOError(f'TXT record not found: add {TXT_PREFIX}.{d.domain} TXT "{expected}" and retry', 422)
    d.verified_at = utcnow()
    session.commit()
    return d


def connection_for_email(session: Session, email: str) -> SSOConnection | None:
    d = session.exec(
        select(SSODomain).where(SSODomain.domain == _domain_of(email), col(SSODomain.verified_at).is_not(None))
    ).first()
    return d.connection if d else None


def enforced_for(session: Session, email: str) -> bool:
    conn = connection_for_email(session, email)
    return bool(conn and conn.enforce)


# --- sign-in round trip ----------------------------------------------------------------


def state_of(authorize_url: str) -> str:
    """The `state` parameter of an authorize URL from `start`."""
    return parse_qs(urlsplit(authorize_url).query)["state"][0]


def start(session: Session, email: str, oidc: OIDCClient, base_url: str, next_path: str | None = None) -> str:
    """Returns the identity provider URL to send the person to."""
    conn = connection_for_email(session, email)
    if conn is None:
        raise SSOError("no organization has SSO for this email's domain; use a sign-in link", 404)
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    session.add(
        SSOLoginState(
            state_hash=_hash(state),
            nonce=nonce,
            code_verifier=verifier,
            connection_id=conn.id,
            next_path=next_path,
            expires_at=utcnow() + STATE_TTL,
        )
    )
    session.commit()
    try:
        return oidc.authorize_url(
            conn.issuer,
            conn.client_id,
            redirect_uri(base_url),
            state,
            nonce,
            challenge,
            login_hint=normalize_email(email),
        )
    except OIDCError as e:
        raise SSOError(str(e), 502) from e


def finish(
    session: Session,
    code: str,
    state: str,
    oidc: OIDCClient,
    box: SecretBox,
    base_url: str,
    *,
    browser_bound: bool = True,
) -> tuple[User, str, str | None]:
    """`browser_bound`: the callback arrived in the browser that started this attempt (its binding cookie
    matched). An attempt that will sign a browser in (it has a next_path) is refused without it and burned."""
    row = session.exec(select(SSOLoginState).where(SSOLoginState.state_hash == _hash(state))).first()
    if row is None or row.used_at is not None or as_utc(row.expires_at) < utcnow():
        raise SSOError("this sign-in attempt is invalid or expired; start again", 401)
    row.used_at = utcnow()
    session.commit()
    if row.next_path and not browser_bound:
        raise SSOError("this sign-in must finish in the browser that started it; start again", 403)
    conn = session.get(SSOConnection, row.connection_id)
    try:
        id_token = oidc.exchange(
            conn.issuer,
            conn.client_id,
            box.decrypt(conn.client_secret_encrypted),
            code,  # type: ignore[union-attr]
            redirect_uri(base_url),
            row.code_verifier,
        )
        claims = oidc.validate(conn.issuer, conn.client_id, id_token, row.nonce)  # type: ignore[union-attr]
    except (OIDCError, ValueError) as e:
        raise SSOError(f"SSO sign-in failed: {e}", 401) from e

    email = normalize_email(claims.get("email") or "")
    if not email or claims.get("email_verified") is False:
        raise SSOError("the identity provider didn't return a verified email", 401)
    verified = {d.domain for d in conn.domains if d.verified_at is not None}  # type: ignore[union-attr]
    if _domain_of(email) not in verified:
        raise SSOError("that email's domain isn't one of this organization's verified domains", 403)

    user, token = sign_in(session, email)
    if claims.get("name") and not user.name:
        user.name = str(claims["name"])[:120]
    member = session.exec(
        select(Membership).where(Membership.org_id == conn.org_id, Membership.user_id == user.id)  # type: ignore[union-attr]
    ).first()
    if member is None:  # first SSO sign-in: join the org
        session.add(Membership(org_id=conn.org_id, user_id=user.id, role=conn.default_role))  # type: ignore[union-attr]
    session.commit()
    return user, token, row.next_path
