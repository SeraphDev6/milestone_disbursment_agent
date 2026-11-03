"""Organization-configured single sign-on (OIDC)."""

from datetime import datetime

from sqlmodel import Field, Relationship, SQLModel

from app.models.columns import enum_type, fk, id_field, timestamp
from app.models.enums import OrgRole


class SSOConnectionBase(SQLModel):
    issuer: str = Field(
        max_length=500, description="OIDC issuer URL, e.g. https://login.microsoftonline.com/<tenant>/v2.0"
    )
    client_id: str = Field(max_length=300, description="Client (application) id registered with the identity provider.")
    enforce: bool = Field(
        default=False, description="Refuse magic links for this org's verified domains; sign-in must use SSO."
    )
    default_role: OrgRole = Field(
        default=OrgRole.member,
        sa_type=enum_type(OrgRole, 16),
        description="Role given to people added on their first SSO sign-in.",
    )


class SSOConnection(SSOConnectionBase, table=True):
    __tablename__ = "sso_connections"

    id: str = id_field()
    org_id: str | None = Field(default=None, foreign_key="organizations.id", unique=True, max_length=36)
    client_secret_encrypted: str = Field(max_length=2000)  # Fernet, keyed from SECRET_KEY
    created_at: datetime = timestamp()

    domains: list["SSODomain"] = Relationship(
        back_populates="connection", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class SSODomain(SQLModel, table=True):
    """An email domain an org claims for SSO. Applies only once ownership is proven with a DNS TXT record."""

    __tablename__ = "sso_domains"

    id: str = id_field()
    connection_id: str | None = fk("sso_connections.id")
    domain: str = Field(max_length=253, index=True)  # lowercase
    verification_token: str = Field(max_length=64)
    verified_at: datetime | None = timestamp(optional=True)

    connection: SSOConnection = Relationship(back_populates="domains")


class SSOLoginState(SQLModel, table=True):
    """One in-flight SSO sign-in: ties the callback to the request (state), the ID token (nonce) and PKCE."""

    __tablename__ = "sso_login_states"

    id: str = id_field()
    state_hash: str = Field(max_length=64, unique=True)
    nonce: str = Field(max_length=100)
    code_verifier: str = Field(max_length=200)
    connection_id: str | None = fk("sso_connections.id")
    next_path: str | None = Field(default=None, max_length=500)  # browser sign-in: where to go afterwards
    created_at: datetime = timestamp()
    expires_at: datetime = timestamp()
    used_at: datetime | None = timestamp(optional=True)


class SSOConnectionWrite(SSOConnectionBase):
    client_secret: str | None = Field(
        default=None, max_length=1000, description="Required when creating; omit to keep the stored secret."
    )
    domains: list[str] = Field(min_length=1, description="Email domains this org owns, e.g. ['harbor.org'].")


class SSODomainRead(SQLModel):
    domain: str
    verified: bool
    dns_record: str = Field(description="Create this TXT record, then call verify.")


class SSOConnectionRead(SSOConnectionBase):
    org_id: str
    redirect_uri: str = Field(description="Register this as the redirect URI at your identity provider.")
    domains: list[SSODomainRead]
