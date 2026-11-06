from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# One host serves three things: the web app at /, the MCP server at /mcp (with its OAuth endpoints at the root,
# where clients look for them), and the REST API under this prefix.
API_PREFIX = "/api"


class Mode(str, Enum):
    """Which PayPal the deployment is connected to. Everything else is the same product.

    sandbox: PayPal's sandbox API. Payees connect sandbox accounts, payers approve holds with sandbox accounts, and
    no real money moves. The demo (/demo) lives here.
    live: PayPal's production API: direct route only (needs PayPal partner approval and a BN code). A live
    deployment never accepts a sandbox account, and the demo is off.
    """

    sandbox = "sandbox"
    live = "live"


# Demo people get addresses at this domain. `.invalid` is reserved (RFC 2606), so nothing is ever delivered
# there; the mailer drops mail to it instead of trying.
DEMO_EMAIL_DOMAIN = "demo.milestone-ledger.invalid"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mode: Mode = Mode.sandbox
    database_url: str = "sqlite:///./local.db"
    # claude (default): Claude via Pydantic AI (needs ANTHROPIC_API_KEY). mock: a deterministic stand-in for tests
    # and local runs whose criteria are all deterministic checks (scripts/sandbox_walkthrough.py).
    llm: Literal["mock", "claude"] = "claude"
    anthropic_api_key: SecretStr | None = None
    # The demo at /demo: a ready-made grant with its three people signed in, for judges and anyone trying the
    # product. Sandbox only; a live deployment never has it. DEMO=false turns it off on a sandbox deployment.
    demo: bool = True
    # Sandbox PayPal test accounts: a payer (personal or business, not the app owner) who approves holds and a payee
    # (business, not the app owner) who connects as the grantee. scripts/sandbox_walkthrough.py uses them, and the
    # demo page shows them (email and password) to anyone who opens /demo, so make them throwaway accounts.
    paypal_test_payer_email: str | None = None
    paypal_test_payer_password: SecretStr | None = None
    paypal_test_payee_email: str | None = None
    paypal_test_payee_password: SecretStr | None = None
    # Encrypts secrets stored for later use (orgs' SSO client secrets) and signs browser CSRF tokens. Required;
    # keep it stable.
    secret_key: SecretStr = SecretStr("dev-only-insecure-secret-key")
    # Encrypts stored files at rest (AES-256-GCM). 32 bytes, base64. Unset: derived from SECRET_KEY. To rotate, set a
    # new key and list the old ones (comma-separated) in FILE_ENCRYPTION_OLD_KEYS, then run `app.jobs encrypt-files`.
    file_encryption_key: SecretStr | None = None
    file_encryption_old_keys: SecretStr | None = None
    # Where stored files' bytes live: the database, or an S3-compatible bucket (Cloudflare R2). With s3, set the
    # bucket, endpoint (https://<account id>.r2.cloudflarestorage.com for R2) and an access key scoped to the bucket,
    # then run `app.jobs encrypt-files` once to move files already in the database.
    file_storage: Literal["database", "s3"] = "database"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_region: str = "auto"
    s3_prefix: str = "files/"
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
    # Without them, payees configure a webhook per repo.
    github_app_id: str | None = None
    github_app_slug: str | None = None
    github_app_client_id: str | None = None
    github_app_client_secret: SecretStr | None = None
    github_app_private_key: SecretStr | None = None  # PEM; literal "\n" sequences are accepted
    github_app_webhook_secret: SecretStr | None = None
    # Credentials of a REST app in the same environment as MODE: a sandbox app for sandbox, a live app for live.
    # The API host follows MODE and can't be overridden, so a deployment never mixes the two.
    paypal_client_id: str | None = None
    paypal_client_secret: SecretStr | None = None
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

    # Outgoing email. console: log only (sign-in and invitation links then come from the server log). resend:
    # Resend's API. smtp: any SMTP server (STARTTLS when the server offers it).
    email_provider: Literal["console", "resend", "smtp"] = "console"
    email_from: str = "Milestone Engine <no-reply@localhost>"
    resend_api_key: SecretStr | None = None
    # Inbound email as evidence (Resend receiving): the domain whose evidence-<id>@ addresses reach the app, and the
    # signing secret of the receiving webhook pointed at /api/webhooks/email/resend. Both unset: no email adapter.
    email_inbox_domain: str | None = None
    resend_webhook_secret: SecretStr | None = None
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
    pay_link_valid_days: int = 7  # a pay link's signature expires after this; the payer side can get a fresh one
    rate_limit_demo_per_hour: int = 10  # per IP: demo casts (three people and their sessions each)
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
    def _modes_need_keys(self) -> "Settings":
        if not (self.paypal_client_id and self.paypal_client_secret):
            raise ValueError(
                f"MODE={self.mode.value} requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET "
                f"(a {self.mode.value} REST app from developer.paypal.com)"
            )
        if self.secret_key.get_secret_value().startswith("dev-only"):
            raise ValueError(f"MODE={self.mode.value} requires SECRET_KEY")
        if self.email_provider == "resend" and not self.resend_api_key:
            raise ValueError("EMAIL_PROVIDER=resend requires RESEND_API_KEY")
        if self.email_provider == "smtp" and not self.smtp_host:
            raise ValueError("EMAIL_PROVIDER=smtp requires SMTP_HOST")
        if self.email_inbox_domain and not (self.resend_api_key and self.resend_webhook_secret):
            raise ValueError("EMAIL_INBOX_DOMAIN requires RESEND_API_KEY and RESEND_WEBHOOK_SECRET")
        if self.mode is Mode.live:
            if not self.public_base_url.startswith("https://"):
                raise ValueError("MODE=live requires an https PUBLIC_BASE_URL")
            if not self.paypal_webhook_id:
                raise ValueError("MODE=live requires PAYPAL_WEBHOOK_ID (webhooks are the source of truth)")
        return self

    @property
    def paypal_api_url(self) -> str:
        """Fixed by MODE: sandbox credentials and accounts only work against the sandbox API, live ones only
        against the live API, so a deployment can't accept a connection from the other environment."""
        return "https://api-m.paypal.com" if self.mode is Mode.live else "https://api-m.sandbox.paypal.com"

    @property
    def paypal_web_url(self) -> str:
        """Where people sign in to the PayPal this deployment uses (the demo page points payers there)."""
        return "https://www.paypal.com" if self.mode is Mode.live else "https://www.sandbox.paypal.com"

    @property
    def demo_enabled(self) -> bool:
        return self.demo and self.mode is Mode.sandbox

    @property
    def local(self) -> bool:
        """Served over plain http: a run on someone's machine. Outbound webhooks may then go to http addresses
        and SSO issuers may be local, which an https deployment refuses."""
        return not self.public_base_url.startswith("https://")

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
    def api_base_url(self) -> str:
        """Where the REST API's own pages and callbacks live (PayPal returns, pay links, sign-in and consent pages,
        webhooks): the public URL plus the API prefix."""
        return self.public_base_url.rstrip("/") + API_PREFIX


def hermetic_settings(**overrides) -> Settings:
    """Settings that ignore .env and need nothing from outside: placeholder PayPal credentials (for a stand-in
    gateway, see create_app), the mock model, SQLite in memory and no background loops. Tests use these, and so
    does rendering the OpenAPI document (app.sdk_spec, scripts/openapi.py)."""
    return Settings(
        **{
            "_env_file": None,
            "mode": "sandbox",
            "llm": "mock",
            "paypal_client_id": "test-client-id",
            "paypal_client_secret": "test-client-secret",
            "secret_key": "test-only-secret-key-that-is-long-enough-000",
            "database_url": "sqlite://",
            "outbound_interval_seconds": 0,
            "payouts_interval_seconds": 0,
            "web_app_built": False,
            **overrides,
        }
    )


def use_claude(settings: Settings) -> bool:
    return settings.llm == "claude"


@lru_cache
def get_settings() -> Settings:
    return Settings()
