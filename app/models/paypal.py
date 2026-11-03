"""An organization's connected PayPal account (PayPal partner onboarding), used to pay it as a payee."""

from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.columns import id_field, timestamp


class PayPalAccountBase(SQLModel):
    merchant_id: str | None = Field(
        default=None, max_length=32, index=True, description="PayPal merchant id; set once onboarding completes."
    )
    payments_receivable: bool = Field(default=False, description="PayPal says the account can receive payments.")
    primary_email_confirmed: bool = Field(default=False, description="The account's primary email is confirmed.")
    permissions_granted: bool = Field(
        default=False, description="The account granted this platform payments, refunds and delayed disbursement."
    )


class PayPalAccount(PayPalAccountBase, table=True):
    __tablename__ = "paypal_accounts"

    id: str = id_field()
    org_id: str | None = Field(default=None, foreign_key="organizations.id", unique=True, max_length=36)
    tracking_id: str = Field(max_length=127, unique=True)  # our reference for the onboarding (PayPal echoes it)
    started_by: str = Field(max_length=120)
    created_at: datetime = timestamp()
    checked_at: datetime | None = timestamp(optional=True)  # last status read from PayPal
    revoked_at: datetime | None = timestamp(optional=True)  # MERCHANT.PARTNER-CONSENT.REVOKED

    @property
    def ready(self) -> bool:
        return bool(
            self.merchant_id
            and self.payments_receivable
            and self.primary_email_confirmed
            and self.permissions_granted
            and self.revoked_at is None
        )

    def issues(self) -> list[str]:
        if self.revoked_at is not None:
            return ["the account revoked this platform's permissions; connect PayPal again"]
        if not self.merchant_id:
            return ["onboarding isn't finished in PayPal"]
        out = []
        if not self.payments_receivable:
            out.append("PayPal says this account can't receive payments yet")
        if not self.primary_email_confirmed:
            out.append("the account's primary email isn't confirmed in PayPal")
        if not self.permissions_granted:
            out.append("the account didn't grant payments, refunds and delayed disbursement to this platform")
        return out


class PayPalAccountRead(PayPalAccountBase):
    org_id: str
    ready: bool = Field(description="True when tranches can be held and paid straight to this account.")
    issues: list[str] = Field(description="What still blocks payments, in plain words.")
    checked_at: datetime | None


class PayPalConnectStarted(SQLModel):
    onboarding_url: str = Field(description="Send an admin of the payee org here to connect their PayPal account.")
