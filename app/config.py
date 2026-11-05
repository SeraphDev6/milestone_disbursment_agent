from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# One host serves three things: the web app at /, the MCP server at /mcp (with its OAuth endpoints at the root,
# where clients look for them), and the REST API under this prefix.
API_PREFIX = "/api"


class Mode(str, Enum):
    mock = "mock"  # no keys needed: fake PayPal ids, canned verifier
    sandbox = "sandbox"  # real PayPal sandbox and a real model
    live = "live"  # production PayPal: direct route only (needs PayPal partner approval and a BN code)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mode: Mode = Mode.mock
    database_url: str = "sqlite:///./local.db"
    # mock: deterministic stand-in; claude: Claude via Pydantic AI (needs ANTHROPIC_API_KEY).
    # Unset follows MODE, so sandbox and live use Claude and mock uses the stand-in.
    llm: Literal["mock", "claude"] | None = None
    anthropic_api_key: SecretStr | None = None
    # Encrypts secrets stored for later use (orgs' SSO client secrets) and signs browser CSRF tokens. Must be set
    # and kept stable outside mock mode.
    secret_key: SecretStr = SecretStr("dev-only-insecure-secret-key")
    # Encrypts stored files at rest (AES-256-GCM). 32 bytes, base64. Unset: derived from SECRET_KEY. To rotate, set a
    # new key and list the old ones (comma-separated) in FILE_ENCRYPTION_OLD_KEYS, then run `app.jobs encrypt-files`.
    file_encryption_key: SecretStr | None = None
    file_encryption_old_keys: SecretStr | None = None
    # Custody of held deliverables. Unset keeps originals for good. Set: this many days after a tranche closes
    # (settled, refunded, or its agreement cancelled, with no activity since), and never before PayPal's 180-day
    # chargeback window from capture ends, the `daily` job either deletes the original files and evidence text
    # (keeping their hashes) or makes them payee-only.
    retention_days: int | None = Field(default=None, ge=0)
    retention_action: Literal["delete", "payee_only"] = "delete"
    # When set, creating agreements and extracting documents (which spends model credits) requires X-Org-Key.
    org_api_key: str | None = None
    # Apply pending Alembic migrations at startup. Safe with one app instance; turn off and run
    # `python -m app.jobs migrate` before starting when running several.
    auto_migrate: bool = True
    # How often the in-process loop delivers outbound webhooks; 0 turns it off (tests, or a separate worker).
    outbound_interval_seconds: float = 5.0
    # How often the in-process loop pays tranches whose dispute window closed; 0 turns it off (use the
    # `payouts` job instead; with several app instances run that job from one scheduler only).
    payouts_interval_seconds: float = 300.0
    github_token: str | None = None  # optional; raises GitHub API rate limits and reads private repos
    # GitHub App (register it with `uv run python scripts/github_app_setup.py`). With these set, orgs install the app
    # instead of configuring repo webhooks, and GitHub reads use each installation's own short-lived token.
    # Mock mode without them uses a stand-in app with a mock install page.
    github_app_id: str | None = None
    github_app_slug: str | None = None
    github_app_client_id: str | None = None
    github_app_client_secret: SecretStr | None = None
    github_app_private_key: SecretStr | None = None  # PEM; literal "\n" sequences are accepted
    github_app_webhook_secret: SecretStr | None = None
    paypal_client_id: str | None = None
    paypal_client_secret: SecretStr | None = None
    # Unset follows MODE: the sandbox API in sandbox mode, the live API in live mode.
    paypal_base_url: str | None = None
    paypal_webhook_id: str | None = None  # from the app's webhook config; needed to verify webhooks
    # The platform's own PayPal merchant id (for partner onboarding status calls). Looked up once when unset.
    paypal_partner_merchant_id: str | None = None
    paypal_bn_code: str | None = None  # partner attribution id, once PayPal issues one for a live partner app
    # Where PayPal sends the payer back after approving. Render sets RENDER_EXTERNAL_URL.
    public_base_url: str = Field(
        "http://localhost:8000", validation_alias=AliasChoices("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL")
    )
    # Where the web app lives. Unset: at PUBLIC_BASE_URL when a web build is present (production), otherwise
    # emailed links open the API's own pages. Set it for the dev split (Vite on 5173, the API on 8000).
    public_app_url: str | None = None
    # Serve the web build (web/dist) at the root and point emailed links at it. Unset: whenever the build exists.
    web_app_built: bool | None = None

    # Outgoing email. console: log only (mock mode returns links in responses instead). resend: Resend's API.
    # smtp: any SMTP server (STARTTLS when the server offers it).
    email_provider: Literal["console", "resend", "smtp"] = "console"
    email_from: str = "Milestone Engine <no-reply@localhost>"
    resend_api_key: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None

    # Per-client limits on endpoints that send email, spend model credits or create PayPal orders. Counted in
    # memory per app instance (a shared store is not needed for one web instance). 0 turns a limit off.
    rate_limit_magic_link_per_hour: int = 20  # per IP (and per email address, at half this)
    rate_limit_extract_per_hour: int = 30  # per caller
    rate_limit_precheck_per_hour: int = 60  # per caller
    rate_limit_pay_link_per_hour: int = 30  # per IP
    # Trust X-Forwarded-For from the reverse proxy in front of the app (Render, Docker behind a load balancer)
    # when attributing requests to a client for rate limits.
    trust_proxy_headers: bool = True

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, v: str) -> str:
        # Render and most hosts hand out postgres:// URLs; SQLAlchemy needs the driver named.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @model_validator(mode="after")
    def _real_modes_need_keys(self) -> "Settings":
        if self.mode is Mode.mock:
            return self
        if not (self.paypal_client_id and self.paypal_client_secret):
            raise ValueError(f"MODE={self.mode.value} requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
        if self.secret_key.get_secret_value().startswith("dev-only"):
            raise ValueError(f"MODE={self.mode.value} requires SECRET_KEY")
        if self.email_provider == "resend" and not self.resend_api_key:
            raise ValueError("EMAIL_PROVIDER=resend requires RESEND_API_KEY")
        if self.email_provider == "smtp" and not self.smtp_host:
            raise ValueError("EMAIL_PROVIDER=smtp requires SMTP_HOST")
        if self.mode is Mode.live:
            if not self.public_base_url.startswith("https://"):
                raise ValueError("MODE=live requires an https PUBLIC_BASE_URL")
            if not self.paypal_webhook_id:
                raise ValueError("MODE=live requires PAYPAL_WEBHOOK_ID (webhooks are the source of truth)")
        return self

    @property
    def paypal_api_url(self) -> str:
        if self.paypal_base_url:
            return self.paypal_base_url
        return "https://api-m.paypal.com" if self.mode is Mode.live else "https://api-m.sandbox.paypal.com"

    @property
    def github_app_configured(self) -> bool:
        return all(
            (
                self.github_app_id,
                self.github_app_slug,
                self.github_app_client_id,
                self.github_app_client_secret,
                self.github_app_private_key,
                self.github_app_webhook_secret,
            )
        )

    @property
    def is_mock(self) -> bool:
        return self.mode is Mode.mock

    @property
    def api_base_url(self) -> str:
        """Where the REST API's own pages and callbacks live (PayPal returns, pay links, sign-in and consent pages,
        webhooks): the public URL plus the API prefix."""
        return self.public_base_url.rstrip("/") + API_PREFIX


def use_claude(settings: Settings) -> bool:
    return settings.llm == "claude" if settings.llm else settings.mode is not Mode.mock


@lru_cache
def get_settings() -> Settings:
    return Settings()
