"""OAuth 2.1 authorization server state for MCP clients (registration, pending consent, one-time codes).

Issued access and refresh tokens are ApiToken rows (kinds oauth_access / oauth_refresh).
"""

from datetime import datetime

from sqlalchemy import JSON
from sqlmodel import Field, SQLModel

from app.models.columns import fk, id_field, timestamp


class OAuthClient(SQLModel, table=True):
    """A dynamically registered MCP client (RFC 7591)."""

    __tablename__ = "oauth_clients"

    client_id: str = Field(primary_key=True, max_length=100)
    info: dict = Field(default_factory=dict, sa_type=JSON)  # OAuthClientInformationFull as JSON
    created_at: datetime = timestamp()


class OAuthPendingAuthorization(SQLModel, table=True):
    """An authorization request waiting for the person to sign in and consent."""

    __tablename__ = "oauth_pending_authorizations"

    id: str = id_field()
    client_id: str = Field(max_length=100)
    params: dict = Field(default_factory=dict, sa_type=JSON)  # AuthorizationParams as JSON
    created_at: datetime = timestamp()
    expires_at: datetime = timestamp()
    used_at: datetime | None = timestamp(optional=True)


class OAuthCode(SQLModel, table=True):
    """A one-time authorization code, bound to the person, the org they chose, and the PKCE challenge."""

    __tablename__ = "oauth_codes"

    id: str = id_field()
    code_hash: str = Field(max_length=64, unique=True)
    client_id: str = Field(max_length=100)
    user_id: str | None = fk("users.id")
    org_id: str | None = fk("organizations.id")
    redirect_uri: str = Field(max_length=2000)
    redirect_uri_provided_explicitly: bool = True
    code_challenge: str = Field(max_length=200)
    scopes: list[str] = Field(default_factory=list, sa_type=JSON)
    resource: str | None = Field(default=None, max_length=500)
    expires_at: datetime = timestamp()
    used_at: datetime | None = timestamp(optional=True)
