"""Organizations, users, memberships, invitations and tokens."""

from datetime import datetime

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from app.models.columns import enum_type, fk, id_field, timestamp
from app.models.enums import InvitationKind, OrgRole, TokenKind

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


# --- Organization ----------------------------------------------------------------------


class OrganizationBase(SQLModel):
    name: str = Field(min_length=1, max_length=200, description="Display name, e.g. 'Harbor Foundation'.")
    paypal_email: str | None = Field(
        default=None,
        max_length=320,
        schema_extra={"pattern": EMAIL_PATTERN},
        description="PayPal account this org funds from (as payer) and is paid into (as payee).",
    )


class Organization(OrganizationBase, table=True):
    __tablename__ = "organizations"

    id: str = id_field()
    created_at: datetime = timestamp()

    memberships: list["Membership"] = Relationship(
        back_populates="organization", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class OrganizationCreate(OrganizationBase):
    pass


class OrganizationRead(OrganizationBase):
    id: str
    my_role: OrgRole | None = Field(default=None, description="The caller's role in this org.")


# --- User and membership ---------------------------------------------------------------


class User(SQLModel, table=True):
    """A person, identified by a verified email (magic link or the org's SSO)."""

    __tablename__ = "users"

    id: str = id_field()
    email: str = Field(max_length=320, unique=True, index=True)  # stored lowercase
    name: str | None = Field(default=None, max_length=120)
    created_at: datetime = timestamp()
    last_sign_in_at: datetime | None = timestamp(optional=True)

    memberships: list["Membership"] = Relationship(
        back_populates="user", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class UserRead(SQLModel):
    id: str
    email: str
    name: str | None


class MembershipBase(SQLModel):
    role: OrgRole = Field(sa_type=enum_type(OrgRole, 16), description="owner, admin, member or viewer.")


class Membership(MembershipBase, table=True):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    id: str = id_field()
    org_id: str | None = fk("organizations.id")
    user_id: str | None = fk("users.id")
    created_at: datetime = timestamp()

    organization: Organization = Relationship(back_populates="memberships")
    user: User = Relationship(back_populates="memberships")


class MembershipRead(MembershipBase):
    org_id: str
    org_name: str
    user: UserRead


# --- Invitations -----------------------------------------------------------------------


class Invitation(SQLModel, table=True):
    """An invitation to join an agreement (as counterparty or reviewer) or an organization (as member)."""

    __tablename__ = "invitations"

    id: str = id_field()
    kind: InvitationKind = Field(sa_type=enum_type(InvitationKind, 16))
    email: str = Field(max_length=320, index=True)  # stored lowercase; only this email can accept
    agreement_id: str | None = fk("agreements.id", optional=True)  # counterparty, reviewer
    org_id: str | None = fk("organizations.id", optional=True)  # member: the org being joined
    org_role: OrgRole | None = Field(default=None, sa_type=enum_type(OrgRole, 16))  # member invitations
    token_hash: str = Field(max_length=64, unique=True)
    invited_by: str = Field(max_length=120)  # actor string
    created_at: datetime = timestamp()
    expires_at: datetime = timestamp()
    accepted_at: datetime | None = timestamp(optional=True)
    accepted_org_id: str | None = Field(default=None, max_length=36)
    revoked_at: datetime | None = timestamp(optional=True)


class InvitationRead(SQLModel):
    id: str
    kind: InvitationKind
    email: str
    agreement_id: str | None
    agreement_title: str | None = None
    org_id: str | None
    org_role: OrgRole | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


# --- Tokens ----------------------------------------------------------------------------


class LoginToken(SQLModel, table=True):
    """One-time magic-link token. Only its hash is stored."""

    __tablename__ = "login_tokens"

    id: str = id_field()
    email: str = Field(max_length=320, index=True)
    token_hash: str = Field(max_length=64, unique=True)
    created_at: datetime = timestamp()
    expires_at: datetime = timestamp()
    used_at: datetime | None = timestamp(optional=True)


class ApiToken(SQLModel, table=True):
    """Bearer credentials: a user's session, or an org's API key (which acts as the org with `org_role`)."""

    __tablename__ = "api_tokens"

    id: str = id_field()
    kind: TokenKind = Field(sa_type=enum_type(TokenKind, 16))
    token_hash: str = Field(max_length=64, unique=True, index=True)
    user_id: str | None = fk("users.id", optional=True)  # sessions
    org_id: str | None = fk("organizations.id", optional=True)  # API keys
    org_role: OrgRole | None = Field(default=None, sa_type=enum_type(OrgRole, 16))  # API keys
    name: str | None = Field(default=None, max_length=120)  # API keys: what it's for
    scopes: list[str] = Field(default_factory=list, sa_type=JSON)  # reserved
    created_by: str | None = Field(default=None, max_length=120)
    created_at: datetime = timestamp()
    expires_at: datetime | None = timestamp(optional=True)
    last_used_at: datetime | None = timestamp(optional=True)
    revoked_at: datetime | None = timestamp(optional=True)


class ApiKeyCreate(SQLModel):
    name: str = Field(min_length=1, max_length=120, description="What this key is for, e.g. 'grant ops agent'.")
    org_role: OrgRole = Field(default=OrgRole.member, description="The rights the key acts with in this org.")


class ApiKeyRead(SQLModel):
    id: str
    name: str | None
    org_role: OrgRole | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyRead):
    token: str = Field(description="Shown once. Send as 'Authorization: Bearer <token>'.")
