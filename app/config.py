from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(str, Enum):
    mock = "mock"  # no keys needed: fake PayPal ids, canned verifier
    sandbox = "sandbox"  # real PayPal sandbox and a real model


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mode: Mode = Mode.mock
    database_url: str = "sqlite:///./local.db"
    # mock: deterministic stand-in; claude: Claude via Pydantic AI (needs ANTHROPIC_API_KEY).
    # Unset follows MODE, so sandbox uses Claude and mock uses the stand-in.
    llm: Literal["mock", "claude"] | None = None
    anthropic_api_key: SecretStr | None = None
    # Encrypts secrets stored for later use (orgs' SSO client secrets). Must be set and kept stable in sandbox/prod.
    secret_key: SecretStr = SecretStr("dev-only-insecure-secret-key")
    # When set, creating agreements and extracting documents (which spends model credits) requires X-Org-Key.
    org_api_key: str | None = None
    # Apply pending Alembic migrations at startup. Safe with one app instance; turn off and run
    # `python -m app.jobs migrate` before starting when running several.
    auto_migrate: bool = True
    # How often the in-process loop delivers outbound webhooks; 0 turns it off (tests, or a separate worker).
    outbound_interval_seconds: float = 5.0
    # How often the in-process loop pays tranches whose dispute window closed; 0 turns it off (use the
    # `payouts` job instead).
    payouts_interval_seconds: float = 300.0
    github_token: str | None = None  # optional; raises GitHub API rate limits and reads private repos
    paypal_client_id: str | None = None
    paypal_client_secret: str | None = None
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str | None = None  # from the sandbox app's webhook config; needed to verify webhooks
    # The platform's own PayPal merchant id (for partner onboarding status calls). Looked up once when unset.
    paypal_partner_merchant_id: str | None = None
    paypal_bn_code: str | None = None  # partner attribution id, once PayPal issues one for a live partner app
    # Where PayPal sends the payer back after approving. Render sets RENDER_EXTERNAL_URL.
    # Where the web app lives (e.g. https://host/app). When set, emailed sign-in and invitation links open the web
    # app (/auth/verify, /invitations/accept) instead of the API's pages, and SSO started from the app returns there.
    public_app_url: str | None = None
    public_base_url: str = Field(
        "http://localhost:8000", validation_alias=AliasChoices("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL")
    )

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, v: str) -> str:
        # Render and most hosts hand out postgres:// URLs; SQLAlchemy needs the driver named.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @model_validator(mode="after")
    def _sandbox_needs_keys(self) -> "Settings":
        if self.mode is Mode.sandbox and not (self.paypal_client_id and self.paypal_client_secret):
            raise ValueError("MODE=sandbox requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
        if self.mode is Mode.sandbox and self.secret_key.get_secret_value().startswith("dev-only"):
            raise ValueError("MODE=sandbox requires SECRET_KEY")
        return self


def use_claude(settings: Settings) -> bool:
    return settings.llm == "claude" if settings.llm else settings.mode is Mode.sandbox


@lru_cache
def get_settings() -> Settings:
    return Settings()
